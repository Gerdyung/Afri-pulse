from unittest.mock import patch

import pytest

from app import app, db, Payment, User


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_gift_initialize_creates_paystack_transaction(client):
    with patch('app.PAYSTACK_SECRET_KEY', 'secret-key'), patch('app.requests.post') as mock_post:
        mock_post.return_value.ok = True
        mock_post.return_value.headers = {'Content-Type': 'application/json'}
        mock_post.return_value.json.return_value = {
            'status': True,
            'data': {'authorization_url': 'https://checkout.paystack.com/example'},
        }

        response = client.post('/gift/initialize', json={
            'room': 'creator',
            'amount': '125.50',
            'email': 'viewer@example.com',
        })

        assert response.status_code == 200
        assert response.json['authorization_url'] == 'https://checkout.paystack.com/example'
        transaction = mock_post.call_args.kwargs['json']
        assert transaction['amount'] == 12550
        assert transaction['currency'] == 'NGN'
        assert transaction['metadata'] == {
            'gift_room': 'creator',
            'gift_email': 'viewer@example.com',
        }


def test_gift_initialize_rejects_fractional_kobo(client):
    with patch('app.PAYSTACK_SECRET_KEY', 'secret-key'), patch('app.requests.post') as mock_post:
        response = client.post('/gift/initialize', json={
            'room': 'creator',
            'amount': '1.001',
            'email': 'viewer@example.com',
        })

        assert response.status_code == 400
        mock_post.assert_not_called()


def test_gift_callback_records_and_broadcasts_only_once(client):
    with app.app_context():
        user = User(email='creator@example.com', username='creator', password_hash='dummy')
        db.session.add(user)
        db.session.commit()

    verified_transaction = {
        'status': 'success',
        'reference': 'gift-reference',
        'amount': 10000,
        'metadata': {
            'gift_room': 'creator',
            'gift_email': 'viewer@example.com',
        },
    }
    with patch('app.PAYSTACK_SECRET_KEY', 'secret-key'), \
            patch('app.requests.get') as mock_get, \
            patch('app.socketio.emit') as mock_emit:
        mock_get.return_value.ok = True
        mock_get.return_value.headers = {'Content-Type': 'application/json'}
        mock_get.return_value.json.return_value = {'status': True, 'data': verified_transaction}

        response = client.get('/gift/callback?reference=gift-reference')
        repeated_response = client.get('/gift/callback?reference=gift-reference')

        assert response.status_code == 302
        assert response.location.endswith('/creator/creator/livestream?gift=sent')
        assert repeated_response.location == response.location
        assert mock_emit.call_count == 1
        assert mock_emit.call_args.kwargs['to'] == 'creator'
        with app.app_context():
            assert Payment.query.filter_by(reference='gift-reference').count() == 1
