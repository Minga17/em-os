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
    if not column_exists(db, "projects", "goal_id"):
        db.execute(
            "ALTER TABLE projects ADD COLUMN goal_id INTEGER "
            "REFERENCES goals(id) ON DELETE SET NULL"
        )
        db.commit()
