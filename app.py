import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'super_secret_random_key_change_this_later'

# ========================================================
# 1. DATABASE CONFIGURATION
# ========================================================
db_url = os.environ.get('DATABASE_URL')
if not db_url:
    db_url = 'sqlite:///clicker.db'

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


# ========================================================
# 2. DATABASE MODEL (Renamed Table to 'users')
# ========================================================
class User(db.Model):
    __tablename__ = 'users'  # Explicitly naming the table 'users' to avoid Postgres conflicts
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    clicks = db.Column(db.Integer, default=0)


# ========================================================
# 3. CRITICAL FIX: CREATE TABLES ON STARTUP
# ========================================================
# This block runs immediately when Render starts the app
with app.app_context():
    db.create_all()


# ========================================================
# 4. ROUTES
# ========================================================
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])

    # If user was deleted from DB but cookie remains, force logout
    if not user:
        session.pop('user_id', None)
        return redirect(url_for('login'))

    # Top 10 Leaderboard
    leaderboard = User.query.order_by(User.clicks.desc()).limit(10).all()

    return render_template('index.html', user=user, leaderboard=leaderboard)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()

        if not username or not password:
            return render_template('login.html', error="Please fill in all fields")

        user = User.query.filter_by(username=username).first()

        if user:
            # Existing user: Check Password
            if check_password_hash(user.password_hash, password):
                session['user_id'] = user.id
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error="Wrong password!")
        else:
            # New user: Create Account
            try:
                hashed_pw = generate_password_hash(password)
                new_user = User(username=username, password_hash=hashed_pw, clicks=0)
                db.session.add(new_user)
                db.session.commit()
                session['user_id'] = new_user.id
                return redirect(url_for('index'))
            except:
                return render_template('login.html', error="Error creating account. Try again.")

    return render_template('login.html')


@app.route('/click', methods=['POST'])
def click():
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            user.clicks += 1
            db.session.commit()
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(debug=True)