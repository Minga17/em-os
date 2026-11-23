"""Insights: real patterns are found, random noise isn't, and the wording stays careful."""
import random
import sqlite3
from datetime import timedelta

import pytest

from insights import build_insights, correlation, insight_of_the_day, welch_t
from timeutil import local_today_for


def connect(app):
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def today():
    return local_today_for("UTC")


def add_habit(db, name="Run", icon="🏃"):
    return db.execute("INSERT INTO habits (user_id, name, icon) VALUES (1, ?, ?)", (name, icon)).lastrowid


def add_day(db, n, energy=3, mood=3, focus=3, score=60, hours=2, habits=()):
    day = (today() - timedelta(days=n)).isoformat()
    db.execute("""INSERT INTO checkins (user_id, log_date, energy, mood, focus, focused_hours, score)
                  VALUES (1, ?, ?, ?, ?, ?, ?)""", (day, energy, mood, focus, hours, score))
    for habit_id in habits:
        db.execute("INSERT INTO habit_logs (habit_id, log_date) VALUES (?, ?)", (habit_id, day))


def insights(app):
    with app.app_context(), connect(app) as db:
        return build_insights(db, 1, today())


def texts(result):
    return " ".join(i["text"] for i in result["items"])


# ── The math ────────────────────────────────────────────────────
def test_welch_t_and_correlation():
    assert abs(welch_t([5, 5, 4, 5, 4], [2, 3, 2, 2, 3])) > 4
    assert abs(welch_t([3, 4, 2, 3, 4], [4, 3, 3, 2, 4])) < 1
    assert welch_t([3, 3, 3], [3, 3, 3]) == 0
    assert correlation([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1)
    assert correlation([1, 2, 3, 4], [5, 5, 5, 5]) == 0


# ── Locked until there's enough data ────────────────────────────
def test_locked_before_14_checkins(app, client, auth):
    auth.register_and_login()
    with connect(app) as db:
        for n in range(1, 10):
            add_day(db, n)
    html = client.get("/insights/").get_data(as_text=True)
    assert "Unlocks after 14 check-ins" in html and "You have 9 so far" in html
    assert "Did you know?" not in client.get("/").get_data(as_text=True)


# ── Finds real patterns ─────────────────────────────────────────
def test_finds_a_habit_pattern(app, client, auth):
    auth.register_and_login()
    rng = random.Random(1)
    with connect(app) as db:
        run = add_habit(db)
        for n in range(1, 41):
            ran = n % 2 == 0
            add_day(db, n, focus=rng.choice([4, 5]) if ran else rng.choice([2, 3]),
                    mood=3, energy=3, score=rng.randint(55, 65), habits=[run] if ran else [])
    result = insights(app)
    first = result["items"][0]
    assert first["key"] == f"habit:{run}:focus"
    assert "Your focus is" in first["text"] and "higher on days you do 🏃 Run" in first["text"]
    assert "based on 40 days" in first["detail"]
    html = client.get("/insights/").get_data(as_text=True)
    assert "higher on days you do 🏃 Run" in html


def test_ignores_random_noise(app, auth):
    auth.register_and_login()
    rng = random.Random(7)
    with connect(app) as db:
        run = add_habit(db)
        for n in range(1, 41):
            add_day(db, n, energy=rng.randint(2, 4), mood=rng.randint(2, 4), focus=rng.randint(2, 4),
                    score=rng.randint(50, 70), hours=rng.randint(1, 4), habits=[run] if rng.random() < 0.5 else [])
    result = insights(app)
    assert not result["locked"]
    assert result["items"] == []


class FakeDB:
    """Just enough of a database for habit_insights, for fast statistical checks."""
    def __init__(self, habits, logs):
        self.habits, self.logs = habits, logs

    def execute(self, sql, params):
        rows = self.habits if "FROM habits" in sql else [(d,) for d in self.logs[params[0]]]

        class Result(list):
            def fetchall(self):
                return list(self)
        return Result(rows)


def random_person(seed, n=40, habits=3):
    rng = random.Random(seed)
    days = {}
    for i in range(1, n + 1):
        d = today() - timedelta(days=i)
        days[d.isoformat()] = {"date": d, "energy": rng.randint(2, 4), "mood": rng.randint(2, 4),
                               "focus": rng.randint(2, 4), "hours": rng.randint(1, 4),
                               "score": rng.randint(50, 70), "focus_session": False, "open_tasks": 3}
    hs = [{"id": h, "icon": "x", "name": f"H{h}", "schedule": "daily", "days": "", "goal_count": 1}
          for h in range(1, habits + 1)]
    logs = {h["id"]: [k for k in days if rng.random() < 0.5] for h in hs}
    return days, FakeDB(hs, logs)


def test_random_people_rarely_see_fake_habit_patterns():
    """The key promise: with 3 habits x 4 measures to search, pure luck finds a 'pattern'
    for about half of people. The permutation test keeps that to a small fraction."""
    from insights import habit_insights
    fooled = sum(bool(habit_insights(db, 1, days)) for days, db in (random_person(s) for s in range(100)))
    assert fooled <= 10


def test_random_people_rarely_see_fake_weekday_patterns():
    from insights import weekday_insight
    fooled = sum(bool(weekday_insight(random_person(s, n=60)[0])) for s in range(200))
    assert fooled <= 16                     # about 5%, with room for chance


def test_needs_enough_days_on_both_sides(app, auth):
    """Done on only 3 days: too few to say anything, however big the gap."""
    auth.register_and_login()
    with connect(app) as db:
        run = add_habit(db)
        for n in range(1, 31):
            ran = n <= 3
            add_day(db, n, focus=5 if ran else 2, habits=[run] if ran else [])
    assert not any(i["key"].startswith("habit:") for i in insights(app)["items"])


def test_best_weekday(app, auth):
    auth.register_and_login()
    rng = random.Random(3)
    with connect(app) as db:
        for n in range(1, 50):
            weekday = (today() - timedelta(days=n)).weekday()
            add_day(db, n, score=85 + rng.randint(-3, 3) if weekday == 1 else 55 + rng.randint(-5, 5))
    text = texts(insights(app))
    assert "Tuesdays tend to be your best days" in text


def test_task_load_and_mood(app, auth):
    auth.register_and_login()
    with connect(app) as db:
        for n in range(1, 31):
            busy = n <= 15
            add_day(db, n, mood=2 if busy else 4)
        # 6 tasks open during the last 15 days only
        for i in range(6):
            db.execute("INSERT INTO tasks (user_id, title, created_at) VALUES (1, ?, ?)",
                       (f"T{i}", (today() - timedelta(days=15)).isoformat() + " 08:00:00"))
    assert "lower on days with 5 or more open tasks" in texts(insights(app))


def test_two_week_trend(app, auth):
    auth.register_and_login()
    with connect(app) as db:
        for n in range(1, 28):
            add_day(db, n, score=(80 if n < 14 else 60) + (n % 3))
    assert "up 20 points over the last 2 weeks" in texts(insights(app))


# ── Careful wording ─────────────────────────────────────────────
FORBIDDEN = ["causes", "because", "depress", "anxiety", "disorder", "diagnos", "symptom", "you should"]


def test_wording_never_claims_cause_or_diagnosis(app, auth):
    auth.register_and_login()
    rng = random.Random(1)
    with connect(app) as db:
        run = add_habit(db)
        for n in range(1, 41):
            ran = n % 2 == 0
            low = n < 14
            add_day(db, n, focus=5 if ran else 2, mood=1 if low else 4, energy=5 if ran else 2,
                    score=40 if low else 70 + rng.randint(0, 5), hours=4 if ran else 1, habits=[run] if ran else [])
    result = insights(app)
    words = (texts(result) + " " + " ".join(i["detail"] for i in result["items"])
             + " " + (result["support"]["text"] if result["support"] else "")).lower()
    assert result["items"]
    for word in FORBIDDEN:
        assert word not in words, word


def test_gentle_support_link_when_mood_is_low(app, client, auth):
    auth.register_and_login()
    with connect(app) as db:
        for n in range(1, 21):
            add_day(db, n, mood=1 if n < 14 else 4)
    html = client.get("/insights/").get_data(as_text=True)
    assert "Your mood has been low lately" in html and "https://findahelpline.com" in html


def test_no_support_note_normally(app, client, auth):
    auth.register_and_login()
    with connect(app) as db:
        for n in range(1, 21):
            add_day(db, n, mood=4)
    assert "findahelpline" not in client.get("/insights/").get_data(as_text=True)


# ── Votes, Today card, privacy ──────────────────────────────────
@pytest.fixture
def with_pattern(app, auth):
    auth.register_and_login()
    with connect(app) as db:
        run = add_habit(db)
        for n in range(1, 41):
            ran = n % 2 == 0
            add_day(db, n, focus=5 if ran else 2, habits=[run] if ran else [])
    return f"habit:{run}:focus"


def test_hide_and_bring_back(app, client, with_pattern):
    client.post("/insights/vote", data={"key": with_pattern, "helpful": "0"})
    assert "days you do 🏃 Run" not in client.get("/insights/").get_data(as_text=True)
    client.post("/insights/reset")
    assert "days you do 🏃 Run" in client.get("/insights/").get_data(as_text=True)


def test_helpful_vote_is_remembered(app, client, with_pattern):
    client.post("/insights/vote", data={"key": with_pattern, "helpful": "1"})
    assert "👍 Thanks!" in client.get("/insights/").get_data(as_text=True)
    with connect(app) as db:
        assert db.execute("SELECT helpful FROM insight_votes").fetchone()[0] == 1


@pytest.mark.parametrize("data", [{"key": "x", "helpful": "2"}, {"key": "", "helpful": "1"}, {}])
def test_bad_votes_rejected(app, client, with_pattern, data):
    assert client.post("/insights/vote", data=data).status_code == 400


def test_today_shows_one_insight(app, client, with_pattern):
    html = client.get("/").get_data(as_text=True)
    assert "Did you know?" in html and "days you do 🏃 Run" in html
    with app.app_context(), connect(app) as db:
        assert insight_of_the_day(db, 1, today())["key"] == with_pattern


def test_insights_are_private(app, client, auth, with_pattern):
    auth.logout()
    auth.register_and_login(name="Other", email="other@example.com")
    html = client.get("/insights/").get_data(as_text=True)
    assert "Run" not in html and "You have 0 so far" in html
    with connect(app) as db:                 # same focus pattern, but none of Ema's habits
        for n in range(1, 41):
            db.execute("INSERT INTO checkins (user_id, log_date, energy, mood, focus, score) VALUES (2, ?, 3, 3, ?, 60)",
                       ((today() - timedelta(days=n)).isoformat(), 5 if n % 2 == 0 else 2))
    html = client.get("/insights/").get_data(as_text=True)
    assert "Unlocks" not in html and "Run" not in html


def test_new_data_updates_insights_the_same_day(app, client, auth):
    """Results are remembered for speed, but never go stale when something changes."""
    auth.register_and_login()
    with connect(app) as db:
        run = add_habit(db)
        for n in range(1, 41):
            add_day(db, n, focus=3)
    assert "days you do 🏃 Run" not in client.get("/insights/").get_data(as_text=True)
    with connect(app) as db:
        for n in range(2, 41, 2):
            db.execute("UPDATE checkins SET focus = 5, updated_at = datetime('now', '+1 second') WHERE log_date = ?",
                       ((today() - timedelta(days=n)).isoformat(),))
            db.execute("INSERT INTO habit_logs (habit_id, log_date) VALUES (?, ?)",
                       (run, (today() - timedelta(days=n)).isoformat()))
    assert "days you do 🏃 Run" in client.get("/insights/").get_data(as_text=True)


def test_insights_need_login(client):
    assert client.get("/insights/").status_code == 302
    assert client.post("/insights/vote", data={"key": "x", "helpful": "1"}).status_code == 302
