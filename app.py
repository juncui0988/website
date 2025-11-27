import os
from flask import Flask, render_template, redirect, url_for
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

database_url = os.environ.get('DATABASE_URL')

# ERROR PREVENTION:
# If the URL is empty (running locally without env var), use sqlite
if not database_url:
    database_url = 'sqlite:///counter.db'

# If the URL starts with postgres://, change it to postgresql://
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize the Database
db = SQLAlchemy(app)


# ---------- DATABASE MODEL ----------
# Instead of "CREATE TABLE", we define a Class
class Counter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    value = db.Column(db.Integer, default=0)


# ---------- HELPER FUNCTIONS ----------
def get_or_create_counter():
    # Try to find the counter with ID 1
    counter = Counter.query.get(1)
    if not counter:
        # If it doesn't exist, create it
        counter = Counter(id=1, value=0)
        db.session.add(counter)
        db.session.commit()
    return counter


# ---------- ROUTES ----------
@app.route("/")
def index():
    counter = get_or_create_counter()
    return render_template("index.html", value=counter.value)


@app.route("/increase")
def increase():
    counter = get_or_create_counter()
    counter.value += 1
    db.session.commit()  # Saves the change to Postgres
    return redirect(url_for("index"))


if __name__ == "__main__":
    # This creates the tables in the database if they don't exist
    with app.app_context():
        db.create_all()

    app.run(debug=True)