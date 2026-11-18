"""Each user's "today" must follow THEIR time zone, not the server's clock."""
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CHECKIN = {"energy": "3", "mood": "3", "focus": "3"}

# UTC+14 and UTC-11 are 25 hours apart, so they are ALWAYS on different dates.
AHEAD = "Pacific/Kiritimati"
BEHIND = "Pacific/Pago_Pago"


def local_date(tz):
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def test_checkin_uses_each_users_own_date(app, client, auth):
    auth.register(email="ahead@example.com", timezone=AHEAD)
    auth.login(email="ahead@example.com")
    client.post("/checkin/", data=CHECKIN)
    auth.logout()

    auth.register(email="behind@example.com", timezone=BEHIND)
    auth.login(email="behind@example.com")
    client.post("/checkin/", data=CHECKIN)

    db = sqlite3.connect(app.config["DATABASE"])
    dates = dict(db.execute(
        "SELECT u.email, c.log_date FROM checkins c JOIN users u ON u.id = c.user_id"
    ).fetchall())
    assert dates["ahead@example.com"] == local_date(AHEAD)
    assert dates["behind@example.com"] == local_date(BEHIND)
    assert dates["ahead@example.com"] != dates["behind@example.com"]


def test_habit_tick_uses_users_date(app, client, auth):
    auth.register(timezone=AHEAD)
    auth.login()
    client.post("/habits/", data={"name": "Read"})
    client.post("/habits/1/toggle")
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT log_date FROM habit_logs").fetchone()[0] == local_date(AHEAD)


def test_overdue_follows_users_date(client, auth):
    auth.register(timezone=AHEAD)
    auth.login()
    yesterday_there = (datetime.now(ZoneInfo(AHEAD)).date() - timedelta(days=1)).isoformat()
    client.post("/tasks/", data={"title": "Late", "priority": "2", "due_date": yesterday_there})
    assert "Overdue" in client.get("/tasks/").get_data(as_text=True)


def test_new_users_default_to_utc(app, auth):
    auth.register()
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT timezone FROM users").fetchone()[0] == "UTC"


def test_invalid_timezone_is_rejected(app, auth):
    auth.register(timezone="Mars/Olympus_Mons")
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_changing_timezone_in_settings(app, client, auth):
    auth.register_and_login()
    client.post("/settings/profile", data={"name": "Ema", "timezone": "America/Chicago"})
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT timezone FROM users").fetchone()[0] == "America/Chicago"
