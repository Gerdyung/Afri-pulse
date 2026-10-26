import os
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_socketio import SocketIO, join_room, leave_room, emit
from dotenv import load_dotenv
from urllib.parse import quote_plus
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from sqlalchemy import inspect, text
from datetime import datetime
from decimal import Decimal, InvalidOperation
import uuid

load_dotenv()

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY", "")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-this-secret")
STREAM_RTMP_URL = os.getenv("STREAM_RTMP_URL", "")
STREAM_KEY = os.getenv("STREAM_KEY", "")


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}...{key[-4:]}"

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///afri-pulse.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# File upload configuration (Increased to 500MB - adjust as needed)
UPLOAD_FOLDER = 'uploads/videos'
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'webm'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

MAX_FILE_SIZE = 500 * 1024 * 1024  # Increased from 100MB to 500MB

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Handle file size too large (413 error) gracefully
@app.errorhandler(413)
def request_entity_too_large(error):
    flash('File is too large. Maximum allowed size is 500MB.')
    return redirect(request.url), 413

# Initialize extensions
db = SQLAlchemy(app)


def ensure_database_schema():
    with app.app_context():
        inspector = inspect(db.engine)
        table_names = set(inspector.get_table_names())

        if 'creator' not in table_names:
            db.create_all()
            return

        existing_columns = {column['name'] for column in inspector.get_columns('creator')}
        column_definitions = {
            'paystack_subaccount_code': 'VARCHAR(120)',
            'paystack_account_name': 'VARCHAR(200)',
            'paystack_business_name': 'VARCHAR(200)',
            'paystack_bank_code': 'VARCHAR(20)',
            'paystack_account_number': 'VARCHAR(40)',
            'paystack_contact_name': 'VARCHAR(120)',
            'paystack_contact_phone': 'VARCHAR(40)',
        }

        for column_name, column_type in column_definitions.items():
            if column_name not in existing_columns:
                db.session.execute(text(f"ALTER TABLE creator ADD COLUMN {column_name} {column_type}"))

        db.session.commit()


ensure_database_schema()

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

PAYSTACK_INITIALIZE_URL = "https://api.paystack.co/transaction/initialize"
PAYSTACK_VERIFY_URL = "https://api.paystack.co/transaction/verify/{}"
PAYSTACK_SUBACCOUNT_CREATE_URL = "https://api.paystack.co/subaccount"


@app.route('/gift/initialize', methods=['POST'])
def gift_initialize():
    data = request.get_json() or {}
    room = (data.get('room') or '').strip()
    email = (data.get('email') or '').strip()
    if not room or len(room) > 120:
        return jsonify({'success': False, 'message': 'A valid livestream room is required.'}), 400
    if not email or len(email) > 200 or '@' not in email:
        return jsonify({'success': False, 'message': 'Enter a valid email address.'}), 400
    if not PAYSTACK_SECRET_KEY:
        return jsonify({'success': False, 'message': 'Paystack is not configured.'}), 503

    try:
        amount = Decimal(str(data.get('amount', '')))
        amount_kobo = amount * 100
        if not amount.is_finite() or amount <= 0 or amount_kobo != amount_kobo.to_integral_value():
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        return jsonify({'success': False, 'message': 'Enter a valid gift amount with up to two decimal places.'}), 400

    transaction = {
        'email': email,
        'amount': int(amount_kobo),
        'currency': 'NGN',
        'callback_url': url_for('gift_callback', _external=True),
        'metadata': {'gift_room': room, 'gift_email': email},
    }
    try:
        response = requests.post(
            PAYSTACK_INITIALIZE_URL,
            json=transaction,
            headers=paystack_headers(),
            timeout=15,
        )
        payload = response.json() if response.headers.get('Content-Type', '').startswith('application/json') else {}
        payment_data = payload.get('data') or {}
        if response.ok and payload.get('status') and payment_data.get('authorization_url'):
            return jsonify({'success': True, 'authorization_url': payment_data['authorization_url']})
        return jsonify({
            'success': False,
            'message': payload.get('message') or 'Unable to start gift checkout.',
        }), 502
    except requests.RequestException:
        app.logger.exception('Paystack gift initialization failed')
        return jsonify({'success': False, 'message': 'Unable to connect to Paystack. Please try again.'}), 502


@app.route('/gift/callback')
def gift_callback():
    reference = request.args.get('reference', '').strip()
    if not reference or not PAYSTACK_SECRET_KEY:
        return redirect(url_for('livestream', gift='failed'))

    try:
        response = requests.get(
            PAYSTACK_VERIFY_URL.format(reference),
            headers=paystack_headers(),
            timeout=15,
        )
        payload = response.json() if response.headers.get('Content-Type', '').startswith('application/json') else {}
        payment_data = payload.get('data') or {}
        metadata = payment_data.get('metadata') or {}
        room = metadata.get('gift_room')
        email = metadata.get('gift_email')
        amount = payment_data.get('amount')

        if (not response.ok or not payload.get('status')
                or payment_data.get('status') != 'success'
                or payment_data.get('reference') != reference
                or not isinstance(room, str) or not room
                or not isinstance(email, str) or not email
                or not isinstance(amount, int) or amount <= 0):
            return redirect(url_for('livestream', gift='failed'))

        is_new_payment = Payment.query.filter_by(reference=reference).first() is None
        if is_new_payment:
            db.session.add(Payment(email=email, amount=amount, reference=reference))
            db.session.commit()
            socketio.emit('gift', {'amount': amount, 'currency': 'NGN'}, to=room)

        if User.query.filter_by(username=room).first():
            return redirect(url_for('creator_livestream', username=room, gift='sent'))
        return redirect(url_for('livestream', room=room, gift='sent'))
    except requests.RequestException:
        app.logger.exception('Paystack gift verification failed')
        return redirect(url_for('livestream', gift='failed'))
    except Exception:
        db.session.rollback()
        app.logger.exception('Unable to record verified gift payment')
        return redirect(url_for('livestream', gift='failed'))

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    username = db.Column(db.String(120), unique=True, nullable=False)
    creator = db.relationship('Creator', backref='user', uselist=False, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Creator(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    display_name = db.Column(db.String(120), nullable=False)
    subtitle = db.Column(db.String(200))
    description = db.Column(db.Text)
    live_url = db.Column(db.String(255))
    price = db.Column(db.Integer, default=999)
    paystack_subaccount_code = db.Column(db.String(120))
    paystack_account_name = db.Column(db.String(200))
    paystack_business_name = db.Column(db.String(200))
    paystack_bank_code = db.Column(db.String(20))
    paystack_account_number = db.Column(db.String(40))
    paystack_contact_name = db.Column(db.String(120))
    paystack_contact_phone = db.Column(db.String(40))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.now())
    
class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255))
    file_path = db.Column(db.String(255))
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    creator = db.relationship('User', backref='videos')


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200))
    amount = db.Column(db.Integer)
    reference = db.Column(db.String(200), unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class StreamSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rtmp_url = db.Column(db.String(512))
    stream_key = db.Column(db.String(512))
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())


def get_stream_settings():
    try:
        setting = StreamSetting.query.first()
    except Exception:
        return {'rtmp_url': STREAM_RTMP_URL or '', 'stream_key': STREAM_KEY or ''}

    if setting:
        return {'rtmp_url': setting.rtmp_url or '', 'stream_key': setting.stream_key or ''}
    return {'rtmp_url': STREAM_RTMP_URL or '', 'stream_key': STREAM_KEY or ''}


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def paystack_headers():
    return {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def get_creator_videos(username):
    creator_upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], username)
    if not os.path.exists(creator_upload_dir):
        return []
    
    videos = []
    for filename in os.listdir(creator_upload_dir):
        if allowed_file(filename):
            videos.append({
                'filename': filename,
                'url': f'/uploads/videos/{username}/{filename}'
            })
    return videos


def create_paystack_subaccount_for_creator(creator, business_name, account_number, bank_code, user_email, contact_name=None, contact_phone=None):
    if not creator:
        return None, "Creator profile not found."

    if not PAYSTACK_SECRET_KEY:
        return None, "Paystack secret key is not configured."

    business_name = (business_name or '').strip()
    account_number = (account_number or '').strip()
    bank_code = (bank_code or '').strip()
    contact_name = (contact_name or '').strip()
    contact_phone = (contact_phone or '').strip()
    user_email = (user_email or '').strip()

    if not business_name or not account_number or not bank_code:
        return None, "Business name, account number, and bank code are required."

    payload = {
        "business_name": business_name,
        "settlement_bank": bank_code,
        "account_number": account_number,
        "percentage_charge": 10,
        "primary_contact_email": user_email,
        "primary_contact_name": contact_name or creator.display_name,
        "primary_contact_phone": contact_phone,
    }

    try:
        response = requests.post(PAYSTACK_SUBACCOUNT_CREATE_URL, json=payload, headers=paystack_headers(), timeout=20)
        payload_json = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}

        if response.ok and payload_json.get("status") and payload_json.get("data"):
            data = payload_json["data"]
            creator.paystack_subaccount_code = data.get("subaccount_code")
            creator.paystack_account_name = data.get("account_name")
            creator.paystack_business_name = business_name
            creator.paystack_bank_code = bank_code
            creator.paystack_account_number = account_number
            creator.paystack_contact_name = contact_name or creator.display_name
            creator.paystack_contact_phone = contact_phone
            db.session.commit()
            return data, None

        message = payload_json.get("message") or "Unable to create Paystack subaccount."
        return None, message
    except requests.RequestException as exc:
        return None, f"Paystack request failed: {exc}"


# Authentication Routes
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        business_name = request.form.get("business_name", "").strip()
        account_number = request.form.get("account_number", "").strip()
        bank_code = request.form.get("bank_code", "").strip()
        contact_name = request.form.get("contact_name", "").strip() or username
        contact_phone = request.form.get("contact_phone", "").strip()

        if not email or not username or not password:
            flash("All fields are required.")
            return redirect(url_for('signup'))

        if password != confirm_password:
            flash("Passwords do not match.")
            return redirect(url_for('signup'))

        if User.query.filter_by(email=email).first():
            flash("Email already registered.")
            return redirect(url_for('signup'))

        if User.query.filter_by(username=username).first():
            flash("Username already taken.")
            return redirect(url_for('signup'))

        user = User(email=email, username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        creator = Creator(
            display_name=username,
            subtitle="New creator",
            description="Welcome to my creator page!",
            price=999,
            user_id=user.id,
            paystack_business_name=business_name,
            paystack_bank_code=bank_code,
            paystack_account_number=account_number,
            paystack_contact_name=contact_name,
            paystack_contact_phone=contact_phone,
        )
        db.session.add(creator)
        db.session.commit()

        if PAYSTACK_SECRET_KEY and (business_name or account_number or bank_code or contact_phone):
            if business_name and account_number and bank_code:
                _, paystack_error = create_paystack_subaccount_for_creator(
                    creator,
                    business_name=business_name,
                    account_number=account_number,
                    bank_code=bank_code,
                    user_email=email,
                    contact_name=contact_name,
                    contact_phone=contact_phone,
                )
                if paystack_error:
                    flash(f"Account created, but Paystack setup failed: {paystack_error}")
                else:
                    flash("Account created successfully and Paystack subscription account was linked.")
            else:
                flash("Account created successfully! Complete your Paystack bank details in your dashboard to enable subscriptions.")
        else:
            flash("Account created successfully! Please log in.")

        return redirect(url_for('login'))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not email or not password:
            flash("Email and password are required.")
            return redirect(url_for('login'))

        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Invalid email or password.")
            return redirect(url_for('login'))

        login_user(user, remember=request.form.get("remember"))
        return redirect(url_for('index'))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out successfully.")
    return redirect(url_for('index'))


# Creator Routes
@app.route("/")
def index():
    creators = Creator.query.all()
    return render_template("index.html", creators=creators)


@app.route("/creator/<username>")
def creator_profile(username):
    user = User.query.filter_by(username=username).first()
    if not user or not user.creator:
        return render_template("error.html", message="Creator not found."), 404
    
    creator = user.creator
    videos = get_creator_videos(username)
    return render_template("creator.html", creator=creator, creator_user=user, videos=videos)


@app.route("/dashboard")
@login_required
def dashboard():
    if not current_user.creator:
        creator = Creator(
            display_name=current_user.username,
            subtitle="New creator",
            description="Welcome to my creator page!",
            price=999,
            user_id=current_user.id
        )
        db.session.add(creator)
        db.session.commit()
    
    videos = get_creator_videos(current_user.username)
    return render_template("dashboard.html", creator=current_user.creator, videos=videos)


@app.route("/dashboard/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    if not current_user.creator:
        flash("You need to have a creator profile first.")
        return redirect(url_for('dashboard'))
    
    if request.method == "POST":
        current_user.creator.display_name = request.form.get("display_name", "").strip()
        current_user.creator.subtitle = request.form.get("subtitle", "").strip()
        current_user.creator.description = request.form.get("description", "").strip()
        current_user.creator.live_url = request.form.get("live_url", "").strip()
        current_user.creator.price = int(request.form.get("price", 999))
        current_user.creator.paystack_business_name = request.form.get("business_name", "").strip()
        current_user.creator.paystack_account_number = request.form.get("account_number", "").strip()
        current_user.creator.paystack_bank_code = request.form.get("bank_code", "").strip()
        current_user.creator.paystack_contact_name = request.form.get("contact_name", "").strip() or current_user.username
        current_user.creator.paystack_contact_phone = request.form.get("contact_phone", "").strip()

        db.session.commit()

        business_name = current_user.creator.paystack_business_name
        account_number = current_user.creator.paystack_account_number
        bank_code = current_user.creator.paystack_bank_code
        contact_name = current_user.creator.paystack_contact_name
        contact_phone = current_user.creator.paystack_contact_phone

        if PAYSTACK_SECRET_KEY and (business_name or account_number or bank_code or contact_phone):
            if business_name and account_number and bank_code:
                _, paystack_error = create_paystack_subaccount_for_creator(
                    current_user.creator,
                    business_name=business_name,
                    account_number=account_number,
                    bank_code=bank_code,
                    user_email=current_user.email,
                    contact_name=contact_name,
                    contact_phone=contact_phone,
                )
                if paystack_error:
                    flash(f"Paystack setup failed: {paystack_error}")
                else:
                    flash("Profile updated successfully and Paystack account linked.")
                    return redirect(url_for('dashboard'))

        flash("Profile updated successfully!")
        return redirect(url_for('dashboard'))

    return render_template("edit_profile.html", creator=current_user.creator)


@app.route("/creator/<username>/upload", methods=["GET", "POST"])
@login_required
def upload_video(username):
    user = User.query.filter_by(username=username).first()
    if not user or not user.creator:
        return render_template("error.html", message="Creator not found."), 404
    
    if current_user.id != user.id:
        flash("You can only upload videos to your own profile.")
        return redirect(url_for('creator_profile', username=username))
    
    if request.method == "POST":
        if 'video' not in request.files:
            flash('No video file selected')
            return redirect(request.url)
        
        file = request.files['video']
        if file.filename == '':
            flash('No video file selected')
            return redirect(request.url)
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_filename = f"{uuid.uuid4().hex}_{filename}"
            
            creator_upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], username)
            os.makedirs(creator_upload_dir, exist_ok=True)
            
            file_path = os.path.join(creator_upload_dir, unique_filename)
            file.save(file_path)
            
            new_video = Video(
                filename=unique_filename,
                file_path=file_path,
                original_name=filename,
                creator_id=user.id
            )
            db.session.add(new_video)
            db.session.commit()
            
            flash('Video uploaded successfully!')
            return redirect(url_for('creator_profile', username=username))
        else:
            flash('Invalid file type. Please upload MP4, AVI, MOV, MKV, or WebM files.')
            return redirect(request.url)
    
    return render_template("upload.html", creator=user.creator)


@app.route('/uploads/videos/<username>/<filename>')
def uploaded_video(username, filename):
    creator_upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], username)
    return send_from_directory(creator_upload_dir, filename)


@app.route("/subscribe", methods=["GET", "POST"])
def subscribe():
    if request.method == "POST":
        if not PAYSTACK_SECRET_KEY:
            flash("Paystack secret key is not configured. Add PAYSTACK_SECRET_KEY to .env.")
            return redirect(url_for("subscribe"))

        email = request.form.get("email", "").strip()
        amount = request.form.get("amount", "0").strip()
        callback_url = url_for("verify_payment", _external=True)

        if not email or not amount:
            flash("Please enter a valid email and amount.")
            return redirect(url_for("subscribe"))

        try:
            amount_kobo = int(float(amount) * 100)
        except ValueError:
            flash("Enter a valid amount.")
            return redirect(url_for("subscribe"))

        data = {
            "email": email,
            "amount": amount_kobo,
            "callback_url": callback_url,
        }

        response = requests.post(PAYSTACK_INITIALIZE_URL, json=data, headers=paystack_headers(), timeout=15)
        payload = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}

        if response.ok and payload.get("status"):
            authorization_url = payload["data"]["authorization_url"]
            return redirect(authorization_url)

        flash(payload.get("message", "Unable to initialize payment. Please try again."))
        return redirect(url_for("subscribe"))

    return render_template("subscribe.html")


@app.route("/payment/verify")
def verify_payment():
    reference = request.args.get("reference")
    if not reference:
        return render_template("error.html", message="No payment reference provided."), 400

    if not PAYSTACK_SECRET_KEY:
        return render_template("error.html", message="Paystack secret key is not configured."), 500
    
    paystack_headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"}

    response = requests.get(PAYSTACK_VERIFY_URL.format(reference), headers=paystack_headers, timeout=30)
    payload = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}

    if response.ok and payload.get("status") and payload.get("data", {}).get("status") == "success":
        amount = payload["data"]["amount"] / 100
        email = payload["data"]["customer"]["email"]
        try:
            payment = Payment(email=email, amount=int(payload["data"]["amount"]), reference=reference)
            db.session.add(payment)
            db.session.commit()
        except Exception:
            db.session.rollback()

        return render_template("success.html", reference=reference, amount=amount, email=email)

    return render_template("error.html", message=payload.get("message", "Payment could not be verified."))


broadcasters = {}  # room -> sid


@app.route("/livestream")
def livestream():
    return render_template("livestream.html")


@app.route('/creator/<username>/livestream')
def creator_livestream(username):
    user = User.query.filter_by(username=username).first()
    if not user or not user.creator:
        return render_template("error.html", message="Creator not found."), 404

    creator = user.creator
    is_owner = current_user.is_authenticated and current_user.username == username and current_user.creator

    embed_url = None
    if creator.live_url:
        url = creator.live_url.strip()
        if 'youtube.com/watch?v=' in url or 'youtu.be/' in url:
            vid = None
            if 'watch?v=' in url:
                vid = url.split('watch?v=')[-1].split('&')[0]
            elif 'youtu.be/' in url:
                vid = url.split('youtu.be/')[-1].split('?')[0]
            if vid:
                embed_url = f'https://www.youtube.com/embed/{vid}'
        elif 'facebook.com' in url or 'fb.watch' in url:
            encoded = quote_plus(url)
            embed_url = f'https://www.facebook.com/plugins/video.php?href={encoded}&show_text=0&width=560'
        else:
            embed_url = url

    settings = get_stream_settings()
    rtmp_url = settings.get('rtmp_url') or None
    rtmp_key_masked = _mask_key(settings.get('stream_key')) if is_owner and settings.get('stream_key') else None
    rendered = render_template('creator_livestream.html', username=username, is_owner=is_owner, creator=creator, embed_url=embed_url, rtmp_url=rtmp_url, rtmp_key_masked=rtmp_key_masked)
    return rendered

@app.route('/creator/<username>/manage')
@login_required
def creator_manage(username):
    if not (current_user.is_authenticated and current_user.username == username):
        flash('Access denied.')
        return redirect(url_for('creator_profile', username=username))

    users = User.query.order_by(User.id.desc()).limit(200).all()
    videos = get_creator_videos(username)
    payments = Payment.query.order_by(Payment.created_at.desc()).limit(200).all()

    return render_template('creator_manage.html', creator_user=current_user, users=users, videos=videos, payments=payments)


@app.route('/admin/stream', methods=['GET', 'POST'])
@login_required
def admin_stream():
    if not (current_user.is_authenticated and current_user.username == 'admin'):
        flash('Admin access required.')
        return redirect(url_for('index'))

    setting = StreamSetting.query.first()
    if request.method == 'POST':
        rtmp_url = request.form.get('rtmp_url', '').strip()
        stream_key = request.form.get('stream_key', '').strip()

        if not setting:
            setting = StreamSetting(rtmp_url=rtmp_url, stream_key=stream_key)
            db.session.add(setting)
        else:
            setting.rtmp_url = rtmp_url
            if stream_key:
                setting.stream_key = stream_key

        db.session.commit()
        flash('Stream settings updated.')
        return redirect(url_for('admin_stream'))

    masked = _mask_key(setting.stream_key) if setting and setting.stream_key else ''
    rtmp_current = setting.rtmp_url if setting and setting.rtmp_url else STREAM_RTMP_URL
    return render_template('admin_stream.html', rtmp_url=rtmp_current, rtmp_key_masked=masked)


@socketio.on('broadcaster')
def handle_broadcaster(data):
    room = data.get('room')
    if not room:
        return
    broadcasters[room] = request.sid
    join_room(room)
    emit('broadcaster-ack', {'room': room})


@socketio.on('watch')
def handle_watch(data):
    room = data.get('room')
    if not room:
        return
    join_room(room)
    viewer_sid = request.sid
    broadcaster_sid = broadcasters.get(room)
    if broadcaster_sid:
        emit('watch', {'viewer': viewer_sid}, to=broadcaster_sid)


@socketio.on('join')
def handle_join(data):
    room = data.get('room')
    if room:
        join_room(room)


@socketio.on('offer')
def handle_offer(data):
    target = data.get('to')
    if not target:
        return
    emit('offer', data, to=target)


@socketio.on('answer')
def handle_answer(data):
    target = data.get('to')
    if not target:
        return
    emit('answer', data, to=target)


@socketio.on('candidate')
def handle_candidate(data):
    target = data.get('to')
    candidate = data.get('candidate')
    if not target or not candidate:
        return
    emit('candidate', {'candidate': candidate, 'from': request.sid}, to=target)


@socketio.on('leave')
def handle_leave(data):
    room = data.get('room')
    if not room:
        return
    broadcaster_sid = broadcasters.get(room)
    if broadcaster_sid:
        emit('viewer-left', {'viewer': request.sid}, to=broadcaster_sid)


if __name__ == "__main__":
    ensure_database_schema()
    socketio.run(app, debug=True, port=5000)
