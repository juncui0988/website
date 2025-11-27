import os
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# ========================================================
# 1. DATABASE CONFIGURATION (WITH FIX)
# ========================================================
# Get the URL from Render
db_url = os.environ.get('DATABASE_URL')

# If we are local (no Render URL), use a local file
if not db_url:
    db_url = 'sqlite:///clicker.db'

# THE FIX: Render uses 'postgres://', but SQLAlchemy needs 'postgresql://'
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


# ========================================================
# 2. DATABASE MODEL
# ========================================================
class Counter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    count = db.Column(db.Integer, default=0)


# ========================================================
# 3. ROUTES
# ========================================================
@app.route('/', methods=['GET', 'POST'])
def index():
    # Initialize the database tables if they don't exist yet
    # (We do this here to ensure it runs on Render automatically)
    with app.app_context():
        db.create_all()

        # Get the counter (create row 1 if it doesn't exist)
        counter = Counter.query.get(1)
        if not counter:
            counter = Counter(id=1, count=0)
            db.session.add(counter)
            db.session.commit()

        # If User Clicked the Button (POST request)
        if request.method == 'POST':
            counter.count += 1
            db.session.commit()

        current_count = counter.count

    return render_template('index.html', count=current_count)


if __name__ == '__main__':
    app.run(debug=True)