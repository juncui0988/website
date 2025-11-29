import os
from datetime import datetime
import pytz
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text

app = Flask(__name__)
app.secret_key = '284586jc'

db_url = os.environ.get('DATABASE_URL')
engine_options = {}

if not db_url:
    db_url = 'sqlite:///clicker.db'
else:
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    engine_options = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": 10,
        "max_overflow": 20
    }

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app, engine_options=engine_options)


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    clicks = db.Column(db.Integer, default=0, index=True)
    coins = db.Column(db.Integer, default=0)


class DailyBoss(db.Model):
    __tablename__ = 'daily_boss'
    id = db.Column(db.Integer, primary_key=True)
    date_str = db.Column(db.String(20), unique=True)
    max_hp = db.Column(db.Integer, default=100000)
    current_hp = db.Column(db.Integer, default=100000)
    is_defeated = db.Column(db.Boolean, default=False)
    total_reward_pool = db.Column(db.Integer, default=100)


class BossParticipation(db.Model):
    __tablename__ = 'boss_participation'
    id = db.Column(db.Integer, primary_key=True)
    boss_id = db.Column(db.Integer, db.ForeignKey('daily_boss.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    damage_dealt = db.Column(db.Integer, default=0)
    __table_args__ = (db.UniqueConstraint('boss_id', 'user_id', name='_user_boss_uc'),)


with app.app_context():
    db.create_all()


def get_spain_time():
    spain_tz = pytz.timezone('Europe/Madrid')
    return datetime.now(spain_tz)


def get_todays_boss_status():
    now = get_spain_time()
    today_str = now.strftime('%Y-%m-%d')

    if now.hour < 20:
        return None

    boss = DailyBoss.query.filter_by(date_str=today_str).first()

    if not boss:
        boss = DailyBoss(date_str=today_str, max_hp=100000, current_hp=100000)
        db.session.add(boss)
        db.session.commit()

    return boss


def distribute_rewards(boss_id):
    boss = DailyBoss.query.get(boss_id)
    participants = BossParticipation.query.filter_by(boss_id=boss_id).all()

    total_hp_pool = boss.max_hp
    reward_pool = boss.total_reward_pool

    for p in participants:
        raw_share = (p.damage_dealt / total_hp_pool) * reward_pool
        share = int(round(raw_share))

        if share > 0:
            User.query.filter_by(id=p.user_id).update({'coins': User.coins + share})

    db.session.commit()


@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])
    if not user:
        session.pop('user_id', None)
        return redirect(url_for('login'))

    boss = get_todays_boss_status()
    return render_template('index.html', user=user, boss=boss)


@app.route('/stats')
def stats():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    total_clicks = db.session.query(func.sum(User.clicks)).scalar() or 0
    leaderboard = User.query.with_entities(User.id, User.username, User.clicks, User.coins) \
        .order_by(User.clicks.desc()) \
        .limit(50).all()

    return render_template('stats.html',
                           total_clicks=total_clicks,
                           leaderboard=leaderboard,
                           current_user_id=session['user_id'])


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()

        user = User.query.filter_by(username=username).first()

        if user:
            if check_password_hash(user.password_hash, password):
                session['user_id'] = user.id
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error="Invalid password.")
        else:
            hashed_pw = generate_password_hash(password)
            new_user = User(username=username, password_hash=hashed_pw, clicks=0, coins=0)

            try:
                db.session.add(new_user)
                db.session.commit()
                session['user_id'] = new_user.id
                return redirect(url_for('index'))
            except IntegrityError:
                db.session.rollback()
                return render_template('login.html', error="Username taken.")

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))


@app.route('/sync_clicks', methods=['POST'])
def sync_clicks():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400

    click_count = data.get('count', 0)
    user_id = session['user_id']

    if click_count > 0:
        User.query.filter_by(id=user_id).update({'clicks': User.clicks + click_count})

    boss = get_todays_boss_status()

    if boss and not boss.is_defeated and click_count > 0:
        boss.current_hp = max(0, boss.current_hp - click_count)

        participation = BossParticipation.query.filter_by(boss_id=boss.id, user_id=user_id).first()
        if not participation:
            participation = BossParticipation(boss_id=boss.id, user_id=user_id, damage_dealt=0)
            db.session.add(participation)

        participation.damage_dealt += click_count

        if boss.current_hp == 0:
            boss.is_defeated = True
            db.session.commit()
            distribute_rewards(boss.id)

        db.session.commit()
    elif click_count > 0:
        db.session.commit()

    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    response = {
        'total_clicks': user.clicks,
        'coins': user.coins,
        'boss': None
    }

    if boss:
        response['boss'] = {
            'active': not boss.is_defeated,
            'current_hp': boss.current_hp,
            'max_hp': boss.max_hp
        }

    return jsonify(response)


@app.route('/debug/reset')
def debug_reset():
    db.session.query(BossParticipation).delete()
    db.session.query(DailyBoss).delete()
    db.session.commit()
    return "Boss reset."


@app.route('/fix_database')
def fix_database():
    try:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS coins INTEGER DEFAULT 0;"))
            conn.commit()
        db.create_all()

        return "Database updated! Coins column added and Boss tables created. You can go back to home now."
    except Exception as e:
        return f"An error occurred: {str(e)}"


if __name__ == '__main__':
    app.run(debug=True)
