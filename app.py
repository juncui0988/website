import os
import json
from datetime import datetime, timezone
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key_change_this_in_prod')

db_url = os.environ.get('DATABASE_URL')
if not db_url:
    db_url = 'sqlite:///clicker.db'
elif db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine_options = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
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
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    max_hp = db.Column(db.Integer, default=100000)
    current_hp = db.Column(db.Integer, default=100000)
    is_defeated = db.Column(db.Boolean, default=False)
    defeated_at = db.Column(db.DateTime, nullable=True)
    total_reward_pool = db.Column(db.Integer, default=100)


class BossParticipation(db.Model):
    __tablename__ = 'boss_participation'
    id = db.Column(db.Integer, primary_key=True)
    boss_id = db.Column(db.Integer, db.ForeignKey('daily_boss.id'), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    damage_dealt = db.Column(db.Integer, default=0)
    __table_args__ = (db.UniqueConstraint('boss_id', 'user_id', name='_user_boss_uc'),)


class BossVote(db.Model):
    __tablename__ = 'boss_vote'
    user_id = db.Column(db.Integer, primary_key=True)


with app.app_context():
    db.create_all()


def get_server_time():
    return datetime.now(timezone.utc)


def get_boss_state(user_id=None):
    boss = DailyBoss.query.order_by(DailyBoss.id.desc()).first()

    state = {
        'status': 'VOTING',
        'boss_data': None,
        'cooldown_seconds': 0,
        'votes': 0,
        'user_voted': False
    }

    if not boss:
        state['votes'] = BossVote.query.count()
        if user_id:
            state['user_voted'] = bool(BossVote.query.get(user_id))
        return state

    if not boss.is_defeated:
        state['status'] = 'ACTIVE'
        state['boss_data'] = {
            'id': boss.id,
            'current_hp': boss.current_hp,
            'max_hp': boss.max_hp
        }
        return state

    now = get_server_time()
    defeat_time = boss.defeated_at if boss.defeated_at else boss.created_at

    if defeat_time.tzinfo is None:
        defeat_time = defeat_time.replace(tzinfo=timezone.utc)

    time_since_death = (now - defeat_time).total_seconds()
    cooldown_duration = 3600

    if time_since_death < cooldown_duration:
        state['status'] = 'COOLDOWN'
        state['cooldown_seconds'] = int(cooldown_duration - time_since_death)
        return state

    state['status'] = 'VOTING'
    state['votes'] = BossVote.query.count()
    if user_id:
        state['user_voted'] = bool(BossVote.query.get(user_id))

    return state


def distribute_rewards(boss_id):
    boss = DailyBoss.query.get(boss_id)
    if not boss or not boss.is_defeated:
        return

    participants = BossParticipation.query.filter_by(boss_id=boss_id).all()
    total_hp_pool = boss.max_hp
    reward_pool = boss.total_reward_pool

    for p in participants:
        if p.damage_dealt > 0:
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

    game_state = get_boss_state(user.id)
    return render_template('index.html', user=user, game_state=game_state)


@app.route('/stats')
def stats():
    user_id = session.get('user_id')
    total_clicks = db.session.query(func.sum(User.clicks)).scalar() or 0
    leaderboard = User.query.with_entities(User.id, User.username, User.clicks, User.coins) \
        .order_by(User.clicks.desc()) \
        .limit(50).all()

    return render_template('stats.html',
                           total_clicks=total_clicks,
                           leaderboard=leaderboard,
                           current_user_id=user_id)


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
            new_user = User(username=username, password_hash=hashed_pw)
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


@app.route('/vote_boss', methods=['POST'])
def vote_boss():
    if 'user_id' not in session:
        return jsonify({'error': 'Login required'}), 401

    user_id = session['user_id']
    state = get_boss_state(user_id)

    if state['status'] != 'VOTING':
        return jsonify({'error': 'Not in voting phase'}), 400

    try:
        existing_vote = BossVote.query.get(user_id)
        if not existing_vote:
            new_vote = BossVote(user_id=user_id)
            db.session.add(new_vote)
            db.session.commit()
    except:
        db.session.rollback()

    vote_count = BossVote.query.count()
    if vote_count >= 3:
        BossVote.query.delete()
        new_boss = DailyBoss(max_hp=150000, current_hp=150000)
        db.session.add(new_boss)
        db.session.commit()
        return jsonify({'spawned': True})

    return jsonify({'spawned': False, 'votes': vote_count})


@app.route('/sync_clicks', methods=['POST'])
def sync_clicks():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    try:
        data = request.get_json(force=True, silent=True) or json.loads(request.data)
    except:
        return jsonify({'error': 'Invalid Data'}), 400

    click_count = int(data.get('count', 0))
    user_id = session['user_id']

    if click_count > 0:
        User.query.filter_by(id=user_id).update({'clicks': User.clicks + click_count})

    state = get_boss_state(user_id)

    if state['status'] == 'ACTIVE':
        boss_id = state['boss_data']['id']
        if click_count > 0:
            participation = BossParticipation.query.filter_by(boss_id=boss_id, user_id=user_id).first()
            if not participation:
                participation = BossParticipation(boss_id=boss_id, user_id=user_id, damage_dealt=0)
                db.session.add(participation)
            participation.damage_dealt += click_count

            DailyBoss.query.filter(DailyBoss.id == boss_id, DailyBoss.current_hp > 0) \
                .update({'current_hp': DailyBoss.current_hp - click_count}, synchronize_session=False)

            db.session.commit()

            boss = DailyBoss.query.get(boss_id)
            if boss.current_hp <= 0 and not boss.is_defeated:
                boss.is_defeated = True
                boss.current_hp = 0
                boss.defeated_at = datetime.now(timezone.utc)
                db.session.commit()
                distribute_rewards(boss.id)
        else:
            pass
    elif click_count > 0:
        db.session.commit()

    user = User.query.get(user_id)
    final_state = get_boss_state(user_id)

    return jsonify({
        'total_clicks': user.clicks,
        'coins': user.coins,
        'game_state': final_state
    })


@app.route('/fix_db')
def fix_db():
    try:
        with db.engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS boss_participation"))
            conn.execute(text("DROP TABLE IF EXISTS daily_boss"))
            conn.execute(text("DROP TABLE IF EXISTS boss_vote"))
            conn.commit()

        db.create_all()
        return "Database Updated Successfully! <a href='/'>Go Back Home</a>"
    except Exception as e:
        return f"Error fixing DB: {e}"


if __name__ == '__main__':
    app.run(debug=True)
