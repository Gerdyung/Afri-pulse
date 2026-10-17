import os
from getpass import getpass

from app import app, db, User


def main():
    with app.app_context():
        username = 'admin'
        existing = User.query.filter_by(username=username).first()
        if existing:
            print(f"Admin user already exists: {existing.email} (username={existing.username})")
            return

        email = input('Admin email (default admin@example.com): ').strip() or 'admin@example.com'

        # Allow password from env for non-interactive use
        pwd = os.getenv('ADMIN_PASSWORD')
        if not pwd:
            pwd = getpass('Admin password: ')
            pwd2 = getpass('Confirm password: ')
            if pwd != pwd2:
                print('Passwords do not match. Aborting.')
                return

        u = User(email=email, username=username)
        u.set_password(pwd)
        db.session.add(u)
        db.session.commit()
        print('Admin user created: username=admin, email=' + email)


if __name__ == '__main__':
    main()
