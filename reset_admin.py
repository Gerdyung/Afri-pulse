import os

from app import app, db, User


def main():
    with app.app_context():
        u = User.query.filter_by(username='admin').first()
        if not u:
            print('No admin user found.')
            return

        # Prefer env var for non-interactive use
        pwd = os.getenv('ADMIN_PASSWORD')
        if not pwd:
            pwd = input('New admin password (visible): ').strip()
            pwd2 = input('Confirm password: ').strip()
            if pwd != pwd2:
                print('Passwords do not match. Aborting.')
                return

        u.set_password(pwd)
        db.session.commit()
        print('Admin password updated.')


if __name__ == '__main__':
    main()
