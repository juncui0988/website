import os
import random
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text, CheckConstraint
from werkzeug.security import generate_password_hash, check_password_hash


# --- CONFIGURATION ---
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev_key_change_this_in_prod')

    # Database handling
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        SQLALCHEMY_DATABASE_URI = 'sqlite:///clicker.db'
    else:
        # Fix for Heroku/Render postgres URLs
        SQLALCHEMY_DATABASE_URI = db_url.replace("postgres://", "postgresql://", 1)

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    # Game Settings
    BOSS_MAX_HP = 100000
    BOSS_REWARD_POOL = 1000
    BOSS_COOLDOWN_SECONDS = 3600
    MAX_CLICKS_PER_REQUEST = 500  # Anti-cheat cap
    CHAT_HISTORY_LIMIT = 50


app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)


# --- MODELS ---

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    clicks = db.Column(db.Integer, default=0, index=True)
    coins = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {"id": self.id, "username": self.username, "clicks": self.clicks, "coins": self.coins}


class DailyBoss(db.Model):
    __tablename__ = 'daily_boss'
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    max_hp = db.Column(db.Integer, default=Config.BOSS_MAX_HP)
    current_hp = db.Column(db.Integer, default=Config.BOSS_MAX_HP)
    is_defeated = db.Column(db.Boolean, default=False, index=True)
    defeated_at = db.Column(db.DateTime, nullable=True)
    total_reward_pool = db.Column(db.Integer, default=Config.BOSS_REWARD_POOL)

    __table_args__ = (
        CheckConstraint('current_hp >= 0', name='check_hp_positive'),
    )


class BossParticipation(db.Model):
    __tablename__ = 'boss_participation'
    id = db.Column(db.Integer, primary_key=True)
    boss_id = db.Column(db.Integer, db.ForeignKey('daily_boss.id'), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    damage_dealt = db.Column(db.Integer, default=0)
    coins_earned = db.Column(db.Integer, default=0)

    __table_args__ = (db.UniqueConstraint('boss_id', 'user_id', name='_user_boss_uc'),)


class BossVote(db.Model):
    __tablename__ = 'boss_vote'
    user_id = db.Column(db.Integer, primary_key=True)


class ChatMessage(db.Model):
    __tablename__ = 'chat_message'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    username = db.Column(db.String(50))
    text = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


# --- GAME SERVICE (LOGIC) ---

class GameService:
    @staticmethod
    def get_boss_state(user_id: Optional[int] = None) -> Dict[str, Any]:
        boss = DailyBoss.query.order_by(DailyBoss.id.desc()).first()

        state = {
            'status': 'VOTING',
            'boss_data': None,
            'cooldown_seconds': 0,
            'votes': 0,
            'user_voted': False,
            'last_defeat_time': None
        }

        # Case 1: No boss ever spawned
        if not boss:
            state['votes'] = BossVote.query.count()
            if user_id:
                state['user_voted'] = bool(BossVote.query.get(user_id))
            return state

        # Case 2: Active Boss
        if not boss.is_defeated:
            state['status'] = 'ACTIVE'
            state['boss_data'] = {
                'id': boss.id,
                'current_hp': boss.current_hp,
                'max_hp': boss.max_hp
            }
            return state

        # Case 3: Boss Defeated (Cooldown or Voting)
        now = datetime.now(timezone.utc)
        defeat_time = boss.defeated_at.replace(
            tzinfo=timezone.utc) if boss.defeated_at and boss.defeated_at.tzinfo is None else (
                    boss.defeated_at or boss.created_at)

        time_since_death = (now - defeat_time).total_seconds()

        if time_since_death < Config.BOSS_COOLDOWN_SECONDS:
            state['status'] = 'COOLDOWN'
            state['cooldown_seconds'] = int(Config.BOSS_COOLDOWN_SECONDS - time_since_death)
            state['last_defeat_time'] = defeat_time.isoformat()
            return state

        # Case 4: Cooldown over, Voting allowed
        state['status'] = 'VOTING'
        state['votes'] = BossVote.query.count()
        if user_id:
            state['user_voted'] = bool(BossVote.query.get(user_id))

        return state

    @staticmethod
    def distribute_rewards(boss_id: int):
        """Calculates rewards based on damage contribution."""
        boss = DailyBoss.query.get(boss_id)
        if not boss or not boss.is_defeated:
            return

        participants = BossParticipation.query.filter_by(boss_id=boss_id).all()
        if not participants:
            return

        total_damage = db.session.query(func.sum(BossParticipation.damage_dealt)).filter_by(
            boss_id=boss_id).scalar() or 1

        for p in participants:
            if p.damage_dealt > 0:
                # Calculate share
                share_percentage = p.damage_dealt / total_damage
                coin_reward = int(share_percentage * boss.total_reward_pool)

                # Minimum 1 coin if they did damage
                if coin_reward < 1 and p.damage_dealt > 0:
                    coin_reward = 1

                if coin_reward > 0:
                    # Update participation record
                    p.coins_earned = coin_reward
                    # Update user wallet safely
                    User.query.filter_by(id=p.user_id).update(
                        {'coins': User.coins + coin_reward}
                    )

        db.session.commit()

    @staticmethod
    def process_click(user_id: int, count: int):
        """Handles user clicks: updates stats and deals boss damage."""
        if count <= 0:
            return

        # Cap clicks to prevent abuse
        safe_count = min(count, Config.MAX_CLICKS_PER_REQUEST)

        # 1. Update User Clicks
        User.query.filter_by(id=user_id).update({'clicks': User.clicks + safe_count})

        # 2. Check Boss Status
        state = GameService.get_boss_state(user_id)

        if state['status'] == 'ACTIVE':
            boss_id = state['boss_data']['id']

            # Ensure participation record exists
            participation = BossParticipation.query.filter_by(boss_id=boss_id, user_id=user_id).first()
            if not participation:
                participation = BossParticipation(boss_id=boss_id, user_id=user_id)
                db.session.add(participation)

            participation.damage_dealt += safe_count

            # Atomic Decrement of HP (prevents race conditions)
            # We assume the DB enforces min 0 via constraint or we handle it in logic
            DailyBoss.query.filter(
                DailyBoss.id == boss_id,
                DailyBoss.current_hp > 0
            ).update(
                {'current_hp': DailyBoss.current_hp - safe_count},
                synchronize_session=False
            )

            db.session.commit()

            # 3. Check for Death (Refresh object to see updated HP)
            # We re-query specifically to see if WE killed it or it is now dead
            boss = DailyBoss.query.get(boss_id)

            if boss.current_hp <= 0 and not boss.is_defeated:
                # Handle the kill
                boss.is_defeated = True
                boss.current_hp = 0
                boss.defeated_at = datetime.now(timezone.utc)
                db.session.commit()
                GameService.distribute_rewards(boss.id)
        else:
            # Just save the user clicks if no active boss
            db.session.commit()


# --- DECORATORS ---
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


# --- ROUTES ---

@app.route('/')
@login_required
def index():
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))

    game_state = GameService.get_boss_state(user.id)
    return render_template('index.html', user=user, game_state=game_state)


@app.route('/stats')
def stats():
    user_id = session.get('user_id')
    total_clicks = db.session.query(func.sum(User.clicks)).scalar() or 0

    # Optimized leaderboard query
    leaderboard = User.query.with_entities(
        User.username, User.clicks, User.coins
    ).order_by(User.clicks.desc()).limit(50).all()

    return render_template('stats.html',
                           total_clicks=total_clicks,
                           leaderboard=leaderboard,
                           current_user_id=user_id)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            return render_template('login.html', error="Missing credentials")

        user = User.query.filter_by(username=username).first()

        if user:
            if check_password_hash(user.password_hash, password):
                session['user_id'] = user.id
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error="Invalid password.")
        else:
            # Register new user
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
    session.clear()
    return redirect(url_for('login'))


@app.route('/vote_boss', methods=['POST'])
@login_required
def vote_boss():
    user_id = session['user_id']
    state = GameService.get_boss_state(user_id)

    if state['status'] != 'VOTING':
        return jsonify({'error': 'Not in voting phase'}), 400

    # Cast Vote
    try:
        if not BossVote.query.get(user_id):
            db.session.add(BossVote(user_id=user_id))
            db.session.commit()
    except IntegrityError:
        db.session.rollback()

    # Check for Spawn Condition
    vote_count = BossVote.query.count()
    if vote_count >= 3:
        # Clear votes and spawn boss
        BossVote.query.delete()
        new_boss = DailyBoss(
            max_hp=Config.BOSS_MAX_HP,
            current_hp=Config.BOSS_MAX_HP,
            total_reward_pool=Config.BOSS_REWARD_POOL
        )
        db.session.add(new_boss)
        db.session.commit()
        return jsonify({'spawned': True})

    return jsonify({'spawned': False, 'votes': vote_count})


@app.route('/sync_clicks', methods=['POST'])
@login_required
def sync_clicks():
    try:
        data = request.get_json(force=True)
        click_count = int(data.get('count', 0))
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid Data'}), 400

    user_id = session['user_id']

    # Process logic in service
    GameService.process_click(user_id, click_count)

    # Return updated state
    user = User.query.get(user_id)
    return jsonify({
        'total_clicks': user.clicks,
        'coins': user.coins,
        'game_state': GameService.get_boss_state(user_id)
    })


# --- CHAT SYSTEM ---

@app.route('/chat/send', methods=['POST'])
@login_required
def chat_send():
    data = request.get_json()
    text_content = data.get('text', '').strip()

    if not text_content:
        return jsonify({'error': 'Empty message'}), 400

    user = User.query.get(session['user_id'])
    msg = ChatMessage(user_id=user.id, username=user.username, text=text_content[:200])
    db.session.add(msg)

    # Optimization: Only run cleanup 10% of the time to save DB ops
    if random.random() < 0.1:
        count = ChatMessage.query.count()
        if count > Config.CHAT_HISTORY_LIMIT:
            limit = count - Config.CHAT_HISTORY_LIMIT
            # Efficient deletion of oldest records
            subq = db.session.query(ChatMessage.id).order_by(ChatMessage.timestamp.asc()).limit(limit).subquery()
            ChatMessage.query.filter(ChatMessage.id.in_(subq)).delete(synchronize_session=False)

    db.session.commit()
    return jsonify({'status': 'ok'})


@app.route('/chat/get')
def chat_get():
    # Fetch last N messages
    msgs = ChatMessage.query.order_by(ChatMessage.timestamp.desc()).limit(Config.CHAT_HISTORY_LIMIT).all()
    return jsonify([{
        'u': m.username,
        't': m.text,
        'ts': m.timestamp.strftime('%H:%M')
    } for m in reversed(msgs)])


@app.route('/get_last_battle_report')
def get_last_battle_report():
    last_boss = DailyBoss.query.filter_by(is_defeated=True).order_by(DailyBoss.defeated_at.desc()).first()
    if not last_boss:
        return jsonify({'error': 'No battles yet'})

    parts = db.session.query(BossParticipation, User.username) \
        .join(User, BossParticipation.user_id == User.id) \
        .filter(BossParticipation.boss_id == last_boss.id) \
        .order_by(BossParticipation.damage_dealt.desc()).all()

    report = []
    for p, username in parts:
        report.append({
            'username': username,
            'damage': p.damage_dealt,
            'coins': p.coins_earned
        })

    defeat_ts = last_boss.defeated_at or last_boss.created_at
    return jsonify({
        'date': defeat_ts.strftime('%Y-%m-%d %H:%M'),
        'participants': report
    })


# --- CLI COMMANDS (For Admin) ---

@app.cli.command("init-db")
def init_db_command():
    """Clear existing data and create new tables."""
    click = None
    try:
        import click
    except ImportError:
        pass

    if click and click.confirm('This will drop all tables. Are you sure?'):
        db.drop_all()
        db.create_all()
        print('Initialized the database.')
    else:
        print("Operation cancelled.")


@app.cli.command("reset-boss")
def reset_boss_command():
    """Force resets the current boss."""
    DailyBoss.query.update({DailyBoss.is_defeated: True})
    BossVote.query.delete()
    db.session.commit()
    print("Boss reset.")


# Initialize DB tables on startup if they don't exist
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)