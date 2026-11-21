"""Phase B: welcome screen, 3-tap check-in, top 3, close your day, morning reminder."""
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from reminders import run_reminders
from timeutil import local_today_for

HTMX = {"HX-Request": "true"}


def connect(app):
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def today():
    return local_today_for("UTC").isoformat()


def at_hour(monkeypatch, hour):
    """Pretend it's a certain time of day on the user's clock."""
    fixed = datetime.now(timezone.utc).replace(hour=hour, minute=0)
    monkeypatch.setattr("plan.user_now", lambda: fixed)


# ── Welcome screen ──────────────────────────────────────────────
def test_new_people_see_welcome_first(client, auth):
    auth.register_and_login(welcome=True)
    response = client.get("/")
    assert response.status_code == 302 and "/welcome/" in response.headers["Location"]
    html = client.get("/welcome/").get_data(as_text=True)
    for name in ("Student", "Learning to code", "Fitness", "Better routines"):
        assert name in html


def test_existing_users_skip_welcome_after_update(tmp_path):
    """People who used EM OS before this update never see the welcome screen."""
    from app import create_app
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO users (name, email, password_hash) VALUES ('Old', 'o@x.com', 'h');""")
    db.close()
    create_app({"TESTING": True, "DATABASE": path})
    assert sqlite3.connect(path).execute("SELECT onboarded FROM users").fetchone()[0] == 1


def test_template_creates_chosen_habits_and_goal(app, client, auth):
    auth.register_and_login(welcome=True)
    preview = client.get("/welcome/?template=code").get_data(as_text=True)
    assert "Code for 30 minutes" in preview
    response = client.post("/welcome/", data={"template": "code", "habit": ["0", "2"], "goal": "1"},
                           follow_redirects=True)
    html = response.get_data(as_text=True)
    assert "all set" in html
    assert "Dreamer" in html                     # first badge, unlocked in the first minute
    with connect(app) as db:
        habits = [r[0] for r in db.execute("SELECT name FROM habits ORDER BY id")]
        goal = db.execute("SELECT g.title, a.name, g.target_date FROM goals g "
                          "JOIN life_areas a ON a.id = g.area_id").fetchone()
        assert db.execute("SELECT onboarded FROM users").fetchone()[0] == 1
    assert habits == ["Code for 30 minutes", "Write down what I learned"]
    assert goal["title"] == "Build and share my first project" and goal["name"] == "Career"
    assert goal["target_date"] == (date.fromisoformat(today()) + timedelta(days=60)).isoformat()
    assert client.get("/").status_code == 200   # straight to Today from now on


def test_template_without_goal(app, client, auth):
    auth.register_and_login(welcome=True)
    client.post("/welcome/", data={"template": "fitness", "habit": ["1"]})
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM habits").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM goals").fetchone()[0] == 0


@pytest.mark.parametrize("form", [{"template": "hacker"}, {"template": ""}])
def test_unknown_template_rejected(app, client, auth, form):
    auth.register_and_login(welcome=True)
    client.post("/welcome/", data=form)
    with connect(app) as db:
        assert db.execute("SELECT onboarded FROM users").fetchone()[0] == 0
    assert client.get("/welcome/?template=hacker").status_code == 404


def test_skip_welcome(app, client, auth):
    auth.register_and_login(welcome=True)
    client.post("/welcome/skip")
    assert client.get("/").status_code == 200
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM habits").fetchone()[0] == 0


def test_welcome_twice_doesnt_duplicate_area(app, client, auth):
    auth.register_and_login(welcome=True)
    client.post("/welcome/", data={"template": "student", "goal": "1"})
    client.post("/welcome/", data={"template": "student", "goal": "1"})
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM life_areas").fetchone()[0] == 1


# ── 3-tap check-in ──────────────────────────────────────────────
def test_today_shows_quick_checkin(client, auth):
    auth.register_and_login()
    html = client.get("/").get_data(as_text=True)
    assert "3 taps" in html and 'name="energy"' in html and "data-autosubmit" in html


def test_quick_checkin_instant(app, client, auth):
    auth.register_and_login()
    response = client.post("/checkin/quick", data={"energy": 4, "mood": 5, "focus": 3}, headers=HTMX)
    html = response.get_data(as_text=True)
    assert "Today's score" in html and ">48<" in html                # (4+5+3) x 4
    assert 'id="today-ring"' in html and 'id="today-flame"' in html   # ring + streak update too
    assert "Checked in" in response.headers["HX-Trigger"]
    with connect(app) as db:
        row = db.execute("SELECT energy, mood, focus, focused_hours, score, log_date FROM checkins").fetchone()
    assert tuple(row) == (4, 5, 3, 0, 48, today())


def test_quick_checkin_without_javascript(app, client, auth):
    auth.register_and_login()
    response = client.post("/checkin/quick", data={"energy": 2, "mood": 2, "focus": 2}, follow_redirects=True)
    assert "Check-in saved" in response.get_data(as_text=True)


def test_quick_checkin_keeps_full_details(app, client, auth):
    auth.register_and_login()
    client.post("/checkin/", data={"energy": 3, "mood": 3, "focus": 3, "focused_hours": 5,
                                   "accomplished": "Shipped Phase B", "wins": "Tests pass"})
    client.post("/checkin/quick", data={"energy": 5, "mood": 5, "focus": 5})
    with connect(app) as db:
        row = db.execute("SELECT energy, focused_hours, accomplished, score FROM checkins").fetchone()
    assert tuple(row) == (5, 5, "Shipped Phase B", 100)
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM checkins").fetchone()[0] == 1


@pytest.mark.parametrize("data", [{"energy": 4, "mood": 4}, {"energy": 9, "mood": 4, "focus": 4},
                                  {"energy": "x", "mood": 4, "focus": 4}])
def test_quick_checkin_rejects_bad_input(app, client, auth, data):
    auth.register_and_login()
    response = client.post("/checkin/quick", data=data, headers=HTMX)
    assert "Pick energy" in response.headers["HX-Trigger"]
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM checkins").fetchone()[0] == 0


# ── Top 3 ───────────────────────────────────────────────────────
def add_task(app, title, **cols):
    with connect(app) as db:
        names = ", ".join(["user_id", "title", *cols])
        marks = ", ".join("?" * (2 + len(cols)))
        return db.execute(f"INSERT INTO tasks ({names}) VALUES ({marks})",
                          (1, title, *cols.values())).lastrowid


def test_morning_card_and_adding_top3(app, client, auth, monkeypatch):
    auth.register_and_login()
    at_hour(monkeypatch, 8)
    assert "Good morning! What are your top 3?" in client.get("/").get_data(as_text=True)
    response = client.post("/plan/add", data={"title": "Finish essay intro"}, headers=HTMX)
    html = response.get_data(as_text=True)
    assert 'id="day-plan"' in html and "Finish essay intro" in html and "Today's top 3" in html
    with connect(app) as db:
        assert db.execute("SELECT focus_date FROM tasks").fetchone()[0] == today()


def test_top3_is_limited_to_three(app, client, auth):
    auth.register_and_login()
    for i in range(5):
        client.post("/plan/add", data={"title": f"Task {i}"}, headers=HTMX)
    extra = add_task(app, "Picked task")
    client.post(f"/plan/{extra}/pick", headers=HTMX)
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM tasks WHERE focus_date = ?", (today(),)).fetchone()[0] == 3


def test_pick_suggested_tasks_overdue_first(app, client, auth):
    auth.register_and_login()
    add_task(app, "No due date")
    add_task(app, "Overdue report", due_date="2020-01-01")
    html = client.get("/").get_data(as_text=True)
    assert html.index("Overdue report") < html.index("No due date")
    task_id = add_task(app, "Call the bank")
    client.post(f"/plan/{task_id}/pick")
    with connect(app) as db:
        assert db.execute("SELECT focus_date FROM tasks WHERE id = ?", (task_id,)).fetchone()[0] == today()


def test_ticking_top3_task_completes_it(app, client, auth):
    auth.register_and_login()
    task_id = add_task(app, "Gym", focus_date=today())
    response = client.post(f"/plan/{task_id}/toggle", headers=HTMX)
    assert "+10 XP" in response.headers["HX-Trigger"] and "Top 1 done" in response.headers["HX-Trigger"]
    with connect(app) as db:
        assert db.execute("SELECT status FROM tasks").fetchone()[0] == "done"
    client.post(f"/plan/{task_id}/toggle", headers=HTMX)          # undo
    with connect(app) as db:
        assert db.execute("SELECT status, completed_at FROM tasks").fetchone()[0] == "todo"


def test_unpick_keeps_the_task(app, client, auth):
    auth.register_and_login()
    task_id = add_task(app, "Maybe later", focus_date=today())
    client.post(f"/plan/{task_id}/unpick", headers=HTMX)
    with connect(app) as db:
        assert db.execute("SELECT focus_date FROM tasks").fetchone()[0] is None


def test_cant_touch_other_peoples_tasks(app, client, auth):
    auth.register_and_login()
    task_id = add_task(app, "Ema's secret task")
    auth.logout()
    auth.register_and_login(name="Other", email="other@example.com")
    for action in ("pick", "unpick", "toggle"):
        assert client.post(f"/plan/{task_id}/{action}").status_code == 404
    with connect(app) as db:
        assert tuple(db.execute("SELECT status, focus_date FROM tasks").fetchone()) == ("todo", None)


def test_plan_needs_login(client):
    assert client.post("/plan/add", data={"title": "x"}).status_code == 302


# ── Close your day ──────────────────────────────────────────────
def test_close_card_only_in_the_evening(client, auth, monkeypatch):
    auth.register_and_login()
    at_hour(monkeypatch, 14)
    assert "Close your day" not in client.get("/").get_data(as_text=True)
    at_hour(monkeypatch, 19)
    assert "Close your day" in client.get("/").get_data(as_text=True)


def test_close_day_moves_unfinished_to_tomorrow(app, client, auth, monkeypatch):
    auth.register_and_login()
    at_hour(monkeypatch, 21)
    done = add_task(app, "Done thing", focus_date=today(), status="done")
    left = add_task(app, "Unfinished thing", focus_date=today())
    response = client.post("/plan/close", data={"carry": "1"}, headers=HTMX)
    html = response.get_data(as_text=True)
    assert "Day closed" in html and "Unfinished thing" in html
    assert "+5 XP" in response.headers["HX-Trigger"]
    tomorrow = (date.fromisoformat(today()) + timedelta(days=1)).isoformat()
    with connect(app) as db:
        dates = dict(db.execute("SELECT id, focus_date FROM tasks").fetchall())
        assert db.execute("SELECT COUNT(*) FROM day_closes").fetchone()[0] == 1
    assert dates == {done: today(), left: tomorrow}
    assert "＋ Unfinished thing" not in html       # already planned for tomorrow: not suggested again


def test_close_day_without_moving(app, client, auth):
    auth.register_and_login()
    left = add_task(app, "Leave it", focus_date=today())
    client.post("/plan/close", data={})
    with connect(app) as db:
        assert db.execute("SELECT focus_date FROM tasks WHERE id = ?", (left,)).fetchone()[0] == today()


def test_closing_twice_counts_once_and_gives_xp(app, client, auth):
    auth.register_and_login()
    client.post("/plan/close", headers=HTMX)
    second = client.post("/plan/close", headers=HTMX)
    assert "+5 XP" not in second.headers.get("HX-Trigger", "")
    from achievements import player_progress
    with app.app_context(), connect(app) as db:
        progress = player_progress(db, 1)
    assert progress["stats"]["days_closed"] == 1
    assert ("days closed", 1, 5) in progress["breakdown"]


# ── Morning reminder ────────────────────────────────────────────
class FakeSender:
    def __init__(self):
        self.sent = []

    def __call__(self, subscription, payload):
        self.sent.append(payload)
        return "sent"


@pytest.fixture
def subscribed(app, auth):
    auth.register_and_login()
    with connect(app) as db:
        db.execute("UPDATE users SET morning_time = '07:30'")
        db.execute("INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) "
                   "VALUES (1, 'https://fcm.googleapis.com/x', 'k', 'a')")


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_morning_reminder_sent_once(app, subscribed):
    sender = FakeSender()
    with app.app_context(), connect(app) as db:
        run_reminders(db, utc("2026-11-21T07:29:00"), sender)
        run_reminders(db, utc("2026-11-21T07:30:00"), sender)
        run_reminders(db, utc("2026-11-21T07:31:00"), sender)
    assert len(sender.sent) == 1
    assert "top 3" in sender.sent[0]["title"] and sender.sent[0]["url"] == "/"


def test_no_morning_reminder_if_already_planned(app, subscribed):
    with connect(app) as db:
        db.execute("INSERT INTO tasks (user_id, title, focus_date) VALUES (1, 'Planned', '2026-11-21')")
    sender = FakeSender()
    with app.app_context(), connect(app) as db:
        run_reminders(db, utc("2026-11-21T07:30:00"), sender)
    assert sender.sent == []


def test_morning_and_evening_are_separate(app, subscribed):
    with connect(app) as db:
        db.execute("UPDATE users SET reminder_time = '20:00'")
    sender = FakeSender()
    with app.app_context(), connect(app) as db:
        run_reminders(db, utc("2026-11-21T07:30:00"), sender)
        run_reminders(db, utc("2026-11-21T20:00:00"), sender)
    assert [p["tag"] for p in sender.sent] == ["morning-plan", "daily-reminder"]


def test_save_both_reminder_times(app, client, auth):
    auth.register_and_login()
    response = client.post("/reminders/time", data={"morning_time": "07:00", "reminder_time": "21:00"},
                           follow_redirects=True)
    html = response.get_data(as_text=True)
    assert "Morning plan: 7:00 AM" in html and "Evening check-in: 9:00 PM" in html
    client.post("/reminders/time", data={"morning_time": "19:00"})          # not a morning time
    with connect(app) as db:
        assert tuple(db.execute("SELECT morning_time, reminder_time FROM users").fetchone()) == ("07:00", "21:00")
