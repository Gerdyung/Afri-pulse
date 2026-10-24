
import os
from unittest.mock import patch

import pytest

from app import app, db, Creator, User, create_paystack_subaccount_for_creator


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_create_paystack_subaccount_for_creator_saves_code(client):
    with app.app_context():
        user = User(email='creator@example.com', username='creator', password_hash='dummy')
        db.session.add(user)
        db.session.commit()
        creator = Creator(display_name='Creator', user_id=user.id)
        db.session.add(creator)
        db.session.commit()

        with patch('app.requests.post') as mock_post, patch('app.PAYSTACK_SECRET_KEY', 'secret-key'):
            mock_post.return_value.ok = True
            mock_post.return_value.headers = {'Content-Type': 'application/json'}
            mock_post.return_value.json.return_value = {
                'status': True,
                'data': {'subaccount_code': 'SUB_123', 'account_name': 'Example Business'}
            }

            result, err = create_paystack_subaccount_for_creator(
                creator,
                business_name='Example Business',
                account_number='0123456789',
                bank_code='058',
                user_email='creator@example.com',
                contact_name='Creator User',
                contact_phone='08000000000',
            )

            assert err is None
            assert result['subaccount_code'] == 'SUB_123'
            assert creator.paystack_subaccount_code == 'SUB_123'
            assert creator.paystack_account_name == 'Example Business'


def test_create_paystack_subaccount_for_creator_requires_secret(client):
    with app.app_context():
        user = User(email='noreq@example.com', username='noreq', password_hash='dummy')
        db.session.add(user)
        db.session.commit()
        creator = Creator(display_name='No Request', user_id=user.id)
        db.session.add(creator)
        db.session.commit()

        with patch('app.PAYSTACK_SECRET_KEY', ''):
            result, err = create_paystack_subaccount_for_creator(
                creator,
                business_name='Example Business',
                account_number='0123456789',
                bank_code='058',
                user_email='noreq@example.com',
            )

            assert result is None
            assert 'configured' in err.lower()
=======
import os
from unittest.mock import patch

import pytest

from app import app, db, Creator, User, create_paystack_subaccount_for_creator


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_create_paystack_subaccount_for_creator_saves_code(client):
    with app.app_context():
        user = User(email='creator@example.com', username='creator', password_hash='dummy')
        db.session.add(user)
        db.session.commit()
        creator = Creator(display_name='Creator', user_id=user.id)
        db.session.add(creator)
        db.session.commit()

        with patch('app.requests.post') as mock_post, patch('app.PAYSTACK_SECRET_KEY', 'secret-key'):
            mock_post.return_value.ok = True
            mock_post.return_value.headers = {'Content-Type': 'application/json'}
            mock_post.return_value.json.return_value = {
                'status': True,
                'data': {'subaccount_code': 'SUB_123', 'account_name': 'Example Business'}
            }

            result, err = create_paystack_subaccount_for_creator(
                creator,
                business_name='Example Business',
                account_number='0123456789',
                bank_code='058',
                user_email='creator@example.com',
                contact_name='Creator User',
                contact_phone='08000000000',
            )

            assert err is None
            assert result['subaccount_code'] == 'SUB_123'
            assert creator.paystack_subaccount_code == 'SUB_123'
            assert creator.paystack_account_name == 'Example Business'


def test_create_paystack_subaccount_for_creator_requires_secret(client):
    with app.app_context():
        user = User(email='noreq@example.com', username='noreq', password_hash='dummy')
        db.session.add(user)
        db.session.commit()
        creator = Creator(display_name='No Request', user_id=user.id)
        db.session.add(creator)
        db.session.commit()

        with patch('app.PAYSTACK_SECRET_KEY', ''):
            result, err = create_paystack_subaccount_for_creator(
                creator,
                business_name='Example Business',
                account_number='0123456789',
                bank_code='058',
                user_email='noreq@example.com',
            )

            assert result is None
            assert 'configured' in err.lower()
 (Initial commit)
