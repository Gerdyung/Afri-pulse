from app import app, db, User, Creator

app.config['TESTING'] = True

with app.app_context():
    db.drop_all()
    db.create_all()
    u = User(email='a@example.com', username='alice', password_hash='x')
    u.set_password('pass')
    db.session.add(u)
    db.session.commit()
    c = Creator(display_name='Alice', subtitle='hi', description='desc', price=999, user_id=u.id)
    db.session.add(c)
    db.session.commit()

client = app.test_client()
paths = ['/', '/creator/alice', '/creator/alice/upload', '/login', '/signup', '/subscribe', '/livestream']
for p in paths:
    r = client.get(p)
    print(p, r.status_code, r.mimetype)
    if r.status_code >= 400:
        print(r.get_data(as_text=True)[:400])
