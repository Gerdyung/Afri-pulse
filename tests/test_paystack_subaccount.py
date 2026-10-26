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


@pytest.mark.parametrize("bank_code", ["058", "033", "044", "215"])
def test_create_paystack_subaccount_accepts_any_bank_code(client, bank_code):
    with app.app_context():
        user = User(email=f'creator_{bank_code}@example.com', username=f'creator_{bank_code}', password_hash='dummy')
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
                'data': {'subaccount_code': f'SUB_{bank_code}', 'account_name': 'Example Business'}
            }

            result, err = create_paystack_subaccount_for_creator(
                creator,
                business_name='Example Business',
                account_number='0123456789',
                bank_code=bank_code,
                user_email=f'creator_{bank_code}@example.com',
                contact_name='Creator User',
                contact_phone='08000000000',
            )

            assert err is None
            assert result['subaccount_code'] == f'SUB_{bank_code}'
            assert creator.paystack_bank_code == bank_code

       
