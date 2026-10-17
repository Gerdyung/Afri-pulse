import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app, db, User, Creator

app.config['TESTING'] = True
with app.app_context():
    db.drop_all()
    db.create_all()
    u = User(email='t@example.com', username='tuser', password_hash='x')
    u.set_password('pass')
    db.session.add(u)
    db.session.commit()
    c = Creator(display_name='T', subtitle='s', description='d', price=0, user_id=u.id, live_url='https://www.twitch.tv/examplechannel')
    db.session.add(c)
    db.session.commit()

client = app.test_client()
resp = client.get('/creator/tuser/livestream')
print(resp.status_code)
print(resp.get_data(as_text=True)[:800])
