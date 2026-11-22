import sqlite3
from flask import g, current_app


def get_db():
    """Open one database connection per request and reuse it."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(error=None):
    """Close the connection when the request ends."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables from schema.sql, then apply any migrations."""
    db = get_db()
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf-8"))
    run_migrations(db)


def column_exists(db, table, column):
    """True if `table` already has a column called `column`."""
    return any(row["name"] == column for row in db.execute(f"PRAGMA table_info({table})"))


def run_migrations(db):
    """Change tables that already exist. CREATE TABLE IF NOT EXISTS can't do this."""
    if not column_exists(db, "users", "timezone"):
        db.execute("ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'")
        db.commit()
    if not column_exists(db, "users", "reminder_time"):
        db.execute("ALTER TABLE users ADD COLUMN reminder_time TEXT")      # '20:00', or NULL = off
        db.commit()
    if not column_exists(db, "users", "last_reminded_on"):
        db.execute("ALTER TABLE users ADD COLUMN last_reminded_on TEXT")   # stops double reminders
        db.commit()
    for column, definition in [
        ("email_verified", "INTEGER NOT NULL DEFAULT 0"),    # 1 once they click the email link
        ("email_sent_at", "TEXT"),                          # stops email spam
        ("session_version", "INTEGER NOT NULL DEFAULT 0"),  # "log out of all devices"
    ]:
        if not column_exists(db, "users", column):
            db.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
            db.commit()
    if not column_exists(db, "users", "onboarded"):
        db.execute("ALTER TABLE users ADD COLUMN onboarded INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE users SET onboarded = 1")      # people who already use EM OS skip the welcome
        db.commit()
    for column in ("morning_time", "last_morning_on"):     # morning "plan your day" reminder
        if not column_exists(db, "users", column):
            db.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT")
            db.commit()
    if not column_exists(db, "tasks", "focus_date"):
        db.execute("ALTER TABLE tasks ADD COLUMN focus_date TEXT")   # in that day's top 3
        db.commit()
    for table, column, definition in [
        # Flexible habits
        ("habits", "kind", "TEXT NOT NULL DEFAULT 'build'"),         # build / quit
        ("habits", "schedule", "TEXT NOT NULL DEFAULT 'daily'"),     # daily / days / weekly
        ("habits", "days", "TEXT NOT NULL DEFAULT ''"),              # e.g. '0,2,4' = Mon Wed Fri
        ("habits", "per_week", "INTEGER NOT NULL DEFAULT 3"),        # for 'weekly'
        ("habits", "goal_count", "INTEGER NOT NULL DEFAULT 1"),      # e.g. 8 (glasses)
        ("habits", "unit", "TEXT NOT NULL DEFAULT ''"),              # e.g. 'glasses'
        ("habit_logs", "amount", "INTEGER NOT NULL DEFAULT 1"),      # how many today
        # Streak freezes: everyone starts with one
        ("users", "freezes", "INTEGER NOT NULL DEFAULT 1"),
        ("users", "freeze_awarded_on", "TEXT"),
        ("users", "freezes_checked_on", "TEXT"),
    ]:
        if not column_exists(db, table, column):
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            db.commit()
    backfill_activity_days(db)
    if not column_exists(db, "projects", "goal_id"):
        db.execute(
            "ALTER TABLE projects ADD COLUMN goal_id INTEGER "
            "REFERENCES goals(id) ON DELETE SET NULL"
        )
        db.commit()


def backfill_activity_days(db):
    """One time only: fill the analytics table from the history we already have."""
    if db.execute("SELECT 1 FROM app_state WHERE key = 'activity_backfilled'").fetchone():
        return
    db.executescript("""
        INSERT OR IGNORE INTO activity_days (user_id, day)
            SELECT id, date(created_at) FROM users;
        INSERT OR IGNORE INTO activity_days (user_id, day)
            SELECT user_id, log_date FROM checkins;
        INSERT OR IGNORE INTO activity_days (user_id, day)
            SELECT h.user_id, l.log_date FROM habit_logs l JOIN habits h ON h.id = l.habit_id;
        INSERT INTO app_state (key, value) VALUES ('activity_backfilled', datetime('now'));
    """)
    db.commit()
