"""Create a demo account with 60 days of realistic data.

Run:  python seed_demo.py
Then log in as  demo@example.com  /  demo-password
Your own account is never touched.
"""
import random
import sqlite3
from datetime import date, datetime, timedelta

from werkzeug.security import generate_password_hash

from app import create_app
from scoring import compute_score

DEMO_EMAIL = "demo@example.com"

app = create_app()  # makes sure all tables exist
db = sqlite3.connect(app.config["DATABASE"])
db.execute("PRAGMA foreign_keys = ON")

# Start fresh: deleting the demo user removes all its data (ON DELETE CASCADE)
db.execute("DELETE FROM users WHERE email = ?", (DEMO_EMAIL,))
cur = db.execute(
    "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
    ("Demo User", DEMO_EMAIL, generate_password_hash("demo-password")),
)
user_id = cur.lastrowid

random.seed(42)
today = date.today()

# Habits, created 60 days ago
habit_ids = []
for name, icon, chance in [("Read 20 minutes", "📚", 0.8),
                           ("Workout", "💪", 0.55),
                           ("Code 1 hour", "💻", 0.7)]:
    cur = db.execute(
        "INSERT INTO habits (user_id, name, icon, created_at) VALUES (?, ?, ?, ?)",
        (user_id, name, icon, f"{today - timedelta(days=60)} 09:00:00"),
    )
    habit_ids.append((cur.lastrowid, chance))

# 60 days of check-ins and habit ticks (some days skipped)
for days_ago in range(60):
    day = today - timedelta(days=days_ago)
    if random.random() < 0.15:
        continue
    weekend = day.weekday() >= 5
    energy = random.randint(2, 4 if weekend else 5)
    mood = random.randint(3, 5)
    focus = random.randint(2, 3 if weekend else 5)
    hours = round(random.uniform(0.5, 2.5 if weekend else 6), 1)
    accomplished = "Worked on EM OS"
    wins = "Shipped a feature" if random.random() < 0.5 else ""
    score = compute_score(energy, mood, focus, hours, accomplished, wins)
    db.execute(
        """INSERT INTO checkins (user_id, log_date, energy, mood, focus, focused_hours,
                                 accomplished, wins, score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, day.isoformat(), energy, mood, focus, hours, accomplished, wins, score),
    )
    for habit_id, chance in habit_ids:
        if random.random() < chance:
            db.execute("INSERT INTO habit_logs (habit_id, log_date) VALUES (?, ?)",
                       (habit_id, day.isoformat()))

# A project with tasks finished over the last 8 weeks
cur = db.execute(
    "INSERT INTO projects (user_id, title, status, next_action) VALUES (?, ?, 'active', ?)",
    (user_id, "EM OS", "Build the dashboard"),
)
project_id = cur.lastrowid
for i in range(25):
    done = random.random() < 0.75
    finished = datetime.now() - timedelta(days=random.randint(0, 55))
    db.execute(
        """INSERT INTO tasks (user_id, project_id, title, priority, status, completed_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, project_id, f"Demo task {i + 1}", random.randint(1, 3),
         "done" if done else "todo",
         finished.isoformat(timespec="seconds") if done else None),
    )

db.execute("UPDATE users SET email_verified = 1 WHERE email = ?", (DEMO_EMAIL,))
db.commit()
db.close()
print("Demo data ready! Log in as demo@example.com / demo-password")

