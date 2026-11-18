import sqlite3
from datetime import datetime, timedelta

from achievements import ACHIEVEMENTS, level_info, xp_needed_for

CHECKIN = {"energy": "3", "mood": "3", "focus": "3", "focused_hours": "1"}


def badges(app, email="ema@example.com"):
    db = sqlite3.connect(app.config["DATABASE"])
    return {r[0] for r in db.execute(
        """SELECT a.code FROM user_achievements a JOIN users u ON u.id = a.user_id
           WHERE u.email = ?""", (email,))}


# ── Level maths ─────────────────────────────────────────────────
def test_level_thresholds():
    assert [xp_needed_for(n) for n in range(1, 6)] == [0, 100, 300, 600, 1000]


def test_level_info():
    assert level_info(0)["level"] == 1
    assert level_info(99)["level"] == 1
    assert level_info(100)["level"] == 2
    info = level_info(450)                  # level 3 spans 300..600
    assert info["level"] == 3
    assert info["into_level"] == 150 and info["level_size"] == 300
    assert info["percent"] == 50 and info["to_next"] == 150


def test_every_badge_code_is_unique():
    codes = [a[0] for a in ACHIEVEMENTS]
    assert len(codes) == len(set(codes))


# ── Unlocking ───────────────────────────────────────────────────
def test_first_checkin_unlocks_a_badge_and_celebrates(app, client, auth):
    auth.register_and_login()
    response = client.post("/checkin/", data=CHECKIN, follow_redirects=True)
    assert "Achievement unlocked: First Step" in response.get_data(as_text=True)
    assert "first_checkin" in badges(app)


def test_badges_are_not_awarded_twice(app, client, auth):
    auth.register_and_login()
    client.post("/checkin/", data=CHECKIN, follow_redirects=True)   # first message shown
    response = client.post("/checkin/", data=CHECKIN, follow_redirects=True)
    assert "First Step" not in response.get_data(as_text=True)
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute(
        "SELECT COUNT(*) FROM user_achievements WHERE code = 'first_checkin'").fetchone()[0] == 1


def test_streak_badge_from_past_days(app, client, auth):
    auth.register_and_login()
    db = sqlite3.connect(app.config["DATABASE"])
    today = datetime.now().date()
    db.executemany(
        "INSERT INTO checkins (user_id, log_date, energy, mood, focus, score) VALUES (1, ?, 3, 3, 3, 40)",
        [((today - timedelta(days=d)).isoformat(),) for d in range(1, 8)])
    db.commit()
    page = client.get("/achievements/").get_data(as_text=True)   # page catches up on old data
    assert "Unstoppable" in page
    assert {"streak_3", "streak_7", "checkins_7"} <= badges(app)
    assert "streak_30" not in badges(app)


def test_task_and_goal_badges(app, client, auth):
    auth.register_and_login()
    client.post("/tasks/", data={"title": "T", "priority": "2"})
    client.post("/tasks/1/toggle")
    client.post("/goals/", data={"title": "G"})
    client.post("/goals/1/edit", data={"title": "G", "status": "achieved", "progress": "100"})
    assert {"first_task", "first_goal", "goal_achieved"} <= badges(app)


def test_xp_is_calculated_from_real_data(client, auth):
    auth.register_and_login()
    client.post("/checkin/", data=CHECKIN)             # 10 XP + First Step badge 25 XP
    client.post("/habits/", data={"name": "Read"})
    client.post("/habits/1/toggle")                    # 5 XP
    page = client.get("/achievements/").get_data(as_text=True)
    assert "40 XP total" in page


def test_locked_badges_show_progress(client, auth):
    auth.register_and_login()
    client.post("/checkin/", data=CHECKIN)
    page = client.get("/achievements/").get_data(as_text=True)
    assert "1 / 7" in page          # Week Warrior: 1 of 7 check-ins


def test_badges_are_private(app, client, auth):
    auth.register_and_login(email="alice@example.com")
    client.post("/checkin/", data=CHECKIN)
    auth.logout()
    auth.register_and_login(email="bob@example.com")
    client.get("/achievements/")
    assert badges(app, "bob@example.com") == set()


def test_deleting_account_removes_badges(app, client, auth):
    auth.register_and_login()
    client.post("/checkin/", data=CHECKIN)
    client.post("/settings/delete", data={"confirm": "DELETE", "password": "password123"})
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT COUNT(*) FROM user_achievements").fetchone()[0] == 0


def test_home_shows_level(client, auth):
    auth.register_and_login()
    assert "Lv 1" in client.get("/").get_data(as_text=True)


def test_many_new_badges_become_one_message(app, client, auth):
    auth.register_and_login()
    db = sqlite3.connect(app.config["DATABASE"])
    today = datetime.now().date()
    db.executemany(
        "INSERT INTO checkins (user_id, log_date, energy, mood, focus, score) VALUES (1, ?, 5, 5, 5, 100)",
        [((today - timedelta(days=d)).isoformat(),) for d in range(1, 8)])
    db.commit()
    page = client.get("/achievements/").get_data(as_text=True)
    assert "You unlocked" in page
    assert page.count("Achievement unlocked:") == 0
