"""Phase D: repeating tasks, goals from tasks, focus timer, self-filling review, records."""
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

import focus
from task_logic import next_due, step
from timeutil import local_today_for

HTMX = {"HX-Request": "true"}


def connect(app):
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def today():
    return local_today_for("UTC")


def rows(app, sql, *params):
    with connect(app) as db:
        return db.execute(sql, params).fetchall()


def add_task(client, **form):
    return client.post("/tasks/", data={"title": "Task", "priority": "2", **form}, follow_redirects=True)


# ── Repeat dates ────────────────────────────────────────────────
def test_repeat_steps():
    fri = date(2026, 11, 20)
    assert step(fri, "daily") == date(2026, 11, 21)
    assert step(fri, "weekdays") == date(2026, 11, 23)          # skips the weekend
    assert step(fri, "weekly") == date(2026, 11, 27)
    assert step(date(2026, 1, 31), "monthly:31") == date(2026, 2, 28)
    assert step(date(2026, 2, 28), "monthly:31") == date(2026, 3, 31)   # doesn't drift after February
    assert step(date(2026, 12, 15), "monthly:15") == date(2027, 1, 15)


def test_late_finish_jumps_to_the_future():
    assert next_due("2026-11-01", "weekly", date(2026, 11, 20)) == date(2026, 11, 22)
    assert next_due(None, "daily", date(2026, 11, 20)) == date(2026, 11, 21)


# ── Repeating tasks ─────────────────────────────────────────────
def test_finishing_a_repeating_task_creates_the_next(app, client, auth):
    auth.register_and_login()
    due = today().isoformat()
    add_task(client, title="Clean room", due_date=due, repeat="weekly")
    task_id = rows(app, "SELECT id FROM tasks")[0]["id"]
    response = client.post(f"/tasks/{task_id}/toggle", follow_redirects=True)
    assert "Next one: Clean room" in response.get_data(as_text=True)
    tasks = rows(app, "SELECT title, status, due_date, repeat FROM tasks ORDER BY id")
    assert [tuple(t) for t in tasks] == [
        ("Clean room", "done", due, "weekly"),
        ("Clean room", "todo", (today() + timedelta(days=7)).isoformat(), "weekly")]


def test_undo_removes_the_next_repeat(app, client, auth):
    auth.register_and_login()
    add_task(client, title="Pay rent", due_date=today().isoformat(), repeat="monthly")
    task_id = rows(app, "SELECT id FROM tasks")[0]["id"]
    client.post(f"/tasks/{task_id}/toggle")
    client.post(f"/tasks/{task_id}/toggle")              # oops, undo
    assert [tuple(t) for t in rows(app, "SELECT status, next_id FROM tasks")] == [("todo", None)]
    client.post(f"/tasks/{task_id}/toggle")              # finish again: only one next one
    assert len(rows(app, "SELECT id FROM tasks WHERE status = 'todo'")) == 1


def test_monthly_task_remembers_its_day(app, client, auth):
    auth.register_and_login()
    add_task(client, title="Rent", due_date="2026-01-31", repeat="monthly")
    assert rows(app, "SELECT repeat FROM tasks")[0][0] == "monthly:31"


def test_repeating_task_from_todays_top3(app, client, auth):
    auth.register_and_login()
    add_task(client, title="Stretch", repeat="daily", due_date=today().isoformat())
    task_id = rows(app, "SELECT id FROM tasks")[0]["id"]
    client.post(f"/plan/{task_id}/pick")
    response = client.post(f"/plan/{task_id}/toggle", headers=HTMX)
    assert "Next one: Stretch" in response.headers["HX-Trigger"]
    next_task = rows(app, "SELECT due_date, focus_date FROM tasks WHERE status = 'todo'")[0]
    assert tuple(next_task) == ((today() + timedelta(days=1)).isoformat(), None)


def test_finishing_through_edit_also_repeats(app, client, auth):
    auth.register_and_login()
    add_task(client, title="Water plants", repeat="weekdays", due_date=today().isoformat())
    task_id = rows(app, "SELECT id FROM tasks")[0]["id"]
    client.post(f"/tasks/{task_id}/edit", data={"title": "Water plants", "status": "done", "priority": "2",
                                                "repeat": "weekdays", "due_date": today().isoformat()})
    assert len(rows(app, "SELECT id FROM tasks")) == 2
    client.post(f"/tasks/{task_id}/edit", data={"title": "Water plants", "status": "doing", "priority": "2",
                                                "repeat": "weekdays", "due_date": today().isoformat()})
    assert [tuple(t) for t in rows(app, "SELECT status FROM tasks")] == [("doing",)]


def test_bad_repeat_rejected(app, client, auth):
    auth.register_and_login()
    assert "how often it repeats" in add_task(client, repeat="hourly").get_data(as_text=True)
    assert rows(app, "SELECT id FROM tasks") == []


# ── Goals from tasks ────────────────────────────────────────────
def make_goal(app, title="Get an internship"):
    with connect(app) as db:
        return db.execute("INSERT INTO goals (user_id, title) VALUES (1, ?)", (title,)).lastrowid


def test_task_linked_straight_to_goal_drives_progress(app, client, auth):
    auth.register_and_login()
    goal = make_goal(app)
    for title in ("Update resume", "Apply to 5 places"):
        add_task(client, title=title, goal_id=str(goal))
    add_task(client, title="Daily LeetCode", goal_id=str(goal), repeat="daily")   # repeats don't count
    html = client.get("/goals/").get_data(as_text=True)
    assert "0 of 2 tasks done" in html and "Update resume" in html          # next steps
    first = rows(app, "SELECT id FROM tasks WHERE title = 'Update resume'")[0][0]
    client.post(f"/tasks/{first}/toggle")
    assert "50%" in client.get("/goals/").get_data(as_text=True)


def test_last_task_offers_to_mark_goal_achieved(app, client, auth):
    auth.register_and_login()
    goal = make_goal(app)
    add_task(client, title="Only step", goal_id=str(goal))
    task_id = rows(app, "SELECT id FROM tasks")[0][0]
    response = client.post(f"/tasks/{task_id}/toggle", follow_redirects=True)
    assert "Every task for “Get an internship” is done" in response.get_data(as_text=True)
    assert "Mark achieved" in client.get("/goals/").get_data(as_text=True)
    client.post(f"/goals/{goal}/achieve")
    assert rows(app, "SELECT status, progress FROM goals")[0][:] == ("achieved", 100)


def test_project_tasks_still_count_for_goals(app, client, auth):
    auth.register_and_login()
    goal = make_goal(app)
    with connect(app) as db:
        project = db.execute("INSERT INTO projects (user_id, title, goal_id) VALUES (1, 'Portfolio', ?)",
                             (goal,)).lastrowid
        db.execute("INSERT INTO tasks (user_id, project_id, title, status) VALUES (1, ?, 'A', 'done')", (project,))
        db.execute("INSERT INTO tasks (user_id, project_id, title) VALUES (1, ?, 'B')", (project,))
    assert "1 of 2 tasks done" in client.get("/goals/").get_data(as_text=True)


def test_cant_link_or_achieve_someone_elses_goal(app, client, auth):
    auth.register_and_login()
    goal = make_goal(app)
    auth.logout()
    auth.register_and_login(name="Other", email="other@example.com")
    assert "doesn&#39;t exist" in add_task(client, goal_id=str(goal)).get_data(as_text=True)
    assert client.post(f"/goals/{goal}/achieve").status_code == 404
    assert rows(app, "SELECT status FROM goals")[0][0] == "active"


# ── Focus timer ─────────────────────────────────────────────────
class Clock:
    """Lets tests move time forward."""
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(focus, "utcnow", c)
    return c


def test_focus_session_logs_real_minutes(app, client, auth, clock):
    auth.register_and_login()
    client.post("/focus/start", data={"minutes": "25", "label": "Essay"})
    assert "Finish &amp; log it" in client.get("/focus/").get_data(as_text=True)
    session_id = rows(app, "SELECT id FROM focus_sessions")[0][0]
    clock.now += timedelta(minutes=12, seconds=40)
    response = client.post(f"/focus/{session_id}/finish", follow_redirects=True)
    assert "12 minutes of focus logged" in response.get_data(as_text=True)
    assert rows(app, "SELECT minutes, log_date FROM focus_sessions")[0][:] == (12, today().isoformat())


def test_minutes_never_more_than_planned(app, client, auth, clock):
    auth.register_and_login()
    client.post("/focus/start", data={"minutes": "15"})
    clock.now += timedelta(hours=3)
    client.get("/focus/")                                   # finished automatically when time's up
    assert rows(app, "SELECT minutes FROM focus_sessions")[0][0] == 15


def test_focus_adds_hours_to_existing_checkin(app, client, auth, clock):
    auth.register_and_login()
    client.post("/checkin/quick", data={"energy": 3, "mood": 3, "focus": 3})
    before = rows(app, "SELECT focused_hours, score FROM checkins")[0]
    client.post("/focus/start", data={"minutes": "50"})
    clock.now += timedelta(minutes=50)
    client.get("/focus/")
    after = rows(app, "SELECT focused_hours, score FROM checkins")[0]
    assert after["focused_hours"] == round(before["focused_hours"] + 50 / 60, 2)
    assert after["score"] > before["score"]


def test_checkin_starts_with_focus_hours(app, client, auth, clock):
    auth.register_and_login()
    client.post("/focus/start", data={"minutes": "25"})
    clock.now += timedelta(minutes=30)
    client.get("/focus/")
    assert 'value="0.42"' in client.get("/checkin/").get_data(as_text=True)
    client.post("/checkin/quick", data={"energy": 3, "mood": 3, "focus": 3})
    assert rows(app, "SELECT focused_hours FROM checkins")[0][0] == 0.42


def test_cancel_and_tiny_sessions_log_nothing(app, client, auth, clock):
    auth.register_and_login()
    client.post("/focus/start", data={"minutes": "25"})
    session_id = rows(app, "SELECT id FROM focus_sessions")[0][0]
    client.post(f"/focus/{session_id}/cancel")
    client.post("/focus/start", data={"minutes": "25"})
    session_id = rows(app, "SELECT id FROM focus_sessions")[0][0]
    clock.now += timedelta(seconds=30)
    client.post(f"/focus/{session_id}/finish")
    assert rows(app, "SELECT id FROM focus_sessions") == []


def test_only_one_session_at_a_time(app, client, auth, clock):
    auth.register_and_login()
    client.post("/focus/start", data={"minutes": "25"})
    clock.now += timedelta(minutes=5)
    client.post("/focus/start", data={"minutes": "15"})
    assert [tuple(r) for r in rows(app, "SELECT minutes, ended_at IS NULL FROM focus_sessions ORDER BY id")] == [
        (5, 0), (None, 1)]


@pytest.mark.parametrize("minutes", ["7", "999", "", "abc"])
def test_focus_lengths_are_fixed(app, client, auth, minutes):
    auth.register_and_login()
    client.post("/focus/start", data={"minutes": minutes})
    assert rows(app, "SELECT id FROM focus_sessions") == []


def test_cant_finish_someone_elses_session(app, client, auth):
    auth.register_and_login()
    client.post("/focus/start", data={"minutes": "25"})
    session_id = rows(app, "SELECT id FROM focus_sessions")[0][0]
    auth.logout()
    auth.register_and_login(name="Other", email="other@example.com")
    assert client.post(f"/focus/{session_id}/finish").status_code == 404
    assert client.post(f"/focus/{session_id}/cancel").status_code == 404


def test_background_finish_sends_a_notification(app, client, auth, clock):
    auth.register_and_login()
    with connect(app) as db:
        db.execute("INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) "
                   "VALUES (1, 'https://fcm.googleapis.com/x', 'k', 'a')")
    client.post("/focus/start", data={"minutes": "25"})
    sent = []
    clock.now += timedelta(minutes=26)
    with app.app_context(), connect(app) as db:
        focus.finish_overdue(db, sender=lambda sub, payload: sent.append(payload) or "sent")
        focus.finish_overdue(db, sender=lambda sub, payload: sent.append(payload) or "sent")
    assert len(sent) == 1 and "25 minutes" in sent[0]["title"]


def test_focus_earns_xp_and_badge(app, client, auth, clock):
    auth.register_and_login()
    client.post("/focus/start", data={"minutes": "25"})
    clock.now += timedelta(minutes=25)
    client.get("/focus/")
    from achievements import player_progress
    with app.app_context(), connect(app) as db:
        progress = player_progress(db, 1)
    assert ("focus sessions", 1, 5) in progress["breakdown"]
    assert "First Focus" in client.get("/achievements/", follow_redirects=True).get_data(as_text=True)


# ── Weekly review ───────────────────────────────────────────────
def test_review_drafts_answers_from_the_week(app, client, auth):
    auth.register_and_login()
    add_task(client, title="Ship Phase D")
    task_id = rows(app, "SELECT id FROM tasks")[0][0]
    client.post(f"/tasks/{task_id}/toggle")
    client.post("/checkin/quick", data={"energy": 5, "mood": 5, "focus": 5})
    add_task(client, title="Study for exam", due_date=(today() + timedelta(days=3)).isoformat())
    html = client.get("/review/").get_data(as_text=True)
    assert "Drafted from your week" in html
    assert "Finished 1 task: Ship Phase D." in html
    assert f"Best day: {today().strftime('%A')} (score 60)." in html
    assert "Get done: Study for exam." in html


def test_saved_review_replaces_the_draft(app, client, auth):
    auth.register_and_login()
    client.post("/checkin/quick", data={"energy": 5, "mood": 5, "focus": 5})
    client.post("/review/", data={"rating": "4", "went_well": "My own words", "challenges": "",
                                  "lessons": "", "next_focus": ""})
    html = client.get("/review/").get_data(as_text=True)
    assert "My own words" in html and "Drafted from your week" not in html


def test_review_compares_with_last_week(app, client, auth):
    auth.register_and_login()
    monday = today() - timedelta(days=today().weekday())
    with connect(app) as db:
        db.execute("INSERT INTO checkins (user_id, log_date, energy, mood, focus, score) VALUES (1, ?, 2, 2, 2, 40)",
                   ((monday - timedelta(days=3)).isoformat(),))
        db.execute("INSERT INTO checkins (user_id, log_date, energy, mood, focus, score) VALUES (1, ?, 4, 4, 4, 70)",
                   (monday.isoformat(),))
    html = client.get("/review/").get_data(as_text=True)
    assert "▲ 30.0" in html


# ── Records ─────────────────────────────────────────────────────
def test_records_page(app, client, auth):
    auth.register_and_login()
    with connect(app) as db:
        for n, score in ((1, 55), (2, 91), (3, 70), (20, 60)):
            db.execute("INSERT INTO checkins (user_id, log_date, energy, mood, focus, score, focused_hours) "
                       "VALUES (1, ?, 3, 3, 3, ?, ?)", ((today() - timedelta(days=n)).isoformat(), score, n / 2))
    html = client.get("/records/").get_data(as_text=True)
    assert "Hall of Fame" in html
    assert "Longest check-in streak" in html and ">3 <" in html.replace("\n", "")
    assert "Best day" in html and "91" in html and "This week" in html        # set 2 days ago: new
    assert "Most focused day" in html and "10.0" in html
    assert "Lifetime" in html and "Check-ins" in html
    assert "21 days with EM OS" in html                   # counts from the first check-in


def test_records_empty_state(client, auth):
    auth.register_and_login()
    assert "No records yet" in client.get("/records/").get_data(as_text=True)


def test_records_only_show_your_own(app, client, auth):
    auth.register_and_login()
    with connect(app) as db:
        db.execute("INSERT INTO checkins (user_id, log_date, energy, mood, focus, score) VALUES (1, ?, 5, 5, 5, 99)",
                   (today().isoformat(),))
    auth.logout()
    auth.register_and_login(name="Other", email="other@example.com")
    html = client.get("/records/").get_data(as_text=True)
    assert "99" not in html and "No records yet" in html
