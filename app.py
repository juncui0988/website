from flask import Flask, render_template, redirect, url_for
import sqlite3

app = Flask(__name__)


# ---------- DATABASE SETUP ----------
def init_db():
    conn = sqlite3.connect("counter.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS counter (
            id INTEGER PRIMARY KEY,
            value INTEGER
        )
    """)

    # Create row only if empty
    c.execute("SELECT value FROM counter WHERE id = 1")
    if c.fetchone() is None:
        c.execute("INSERT INTO counter (id, value) VALUES (1, 0)")

    conn.commit()
    conn.close()


def get_counter():
    conn = sqlite3.connect("counter.db")
    c = conn.cursor()
    c.execute("SELECT value FROM counter WHERE id = 1")
    value = c.fetchone()[0]
    conn.close()
    return value


def increase_counter():
    conn = sqlite3.connect("counter.db")
    c = conn.cursor()
    c.execute("UPDATE counter SET value = value + 1 WHERE id = 1")
    conn.commit()
    conn.close()


# ---------- ROUTES ----------
@app.route("/")
def index():
    value = get_counter()
    return render_template("index.html", value=value)


@app.route("/increase")
def increase():
    increase_counter()
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)