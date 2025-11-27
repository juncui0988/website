import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'super_secret_random_key_change_this_later'  # Needed for sessions

# ========================================================
# 1. DATABASE CONFIGURATION
# ========================================================
db_url = os.environ.get('DATABASE_URL')
if not db_url:
    db_url = 'sqlite:///clicker.db'

# Fix for Render PostgreSQL URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


# ========================================================
# 2. DATABASE MODEL (USER)
# ========================================================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    clicks = db.Column(db.Integer, default=0)


# ========================================================
# 3. ROUTES
# ========================================================

@app.route('/')
def index():
    # If user is not logged in, send them to login page
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # Get current user
    user = User.query.get(session['user_id'])

    # Get Top 10 Leaderboard
    leaderboard = User.query.order_by(User.clicks.desc()).limit(10).all()

    return render_template('index.html', user=user, leaderboard=leaderboard)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()

        if not username or not password:
            return "Please fill in all fields", 400

        # Check if user exists
        user = User.query.filter_by(username=username).first()

        if user:
            # --- EXISTING USER: CHECK PASSWORD ---
            if check_password_hash(user.password_hash, password):
                session['user_id'] = user.id
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error="Wrong password for this username!")
        else:
            # --- NEW USER: CREATE ACCOUNT ---
            hashed_pw = generate_password_hash(password)
            new_user = User(username=username, password_hash=hashed_pw, clicks=0)
            db.session.add(new_user)
            db.session.commit()

            session['user_id'] = new_user.id
            return redirect(url_for('index'))

    return render_template('login.html')


@app.route('/click', methods=['POST'])
def click():
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        user.clicks += 1
        db.session.commit()
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)