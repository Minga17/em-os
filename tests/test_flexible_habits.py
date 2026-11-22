"""Flexible habits and streak freezes, through the real pages."""
import sqlite3
from datetime import date, timedelta

import pytest

from freezes import apply_freezes, award_freeze
from timeutil import local_today_for

HTMX = {"HX-Request": "true"}


def connect(app):
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def today():
    return local_today_for("UTC")


def add(client, **form):
    data = {"name": "Habit", "icon": "✅", **form}
    return client.post("/habits/", data=data, follow_redirects=True)


def habit(app, name):
    with connect(app) as db:
        return db.execute("SELECT * FROM habits WHERE name = ?", (name,)).fetchone()


# ── Adding habits with options ──────────────────────────────────
def test_add_simple_habit_still_works(app, client, auth):
    auth.register_and_login()
    add(client, name="Read")
    h = habit(app, "Read")
    assert (h["kind"], h["schedule"], h["goal_count"]) == ("build", "daily", 1)


def test_add_form_shows_all_options(client, auth):
    auth.register_and_login()
    html = client.get("/habits/").get_data(as_text=True)
    for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        assert f"<span>{day}</span>" in html
    assert 'name="per_week"' in html and 'name="goal_count"' in html and 'value="quit"' in html


def test_edit_form_shows_chosen_days(app, client, auth):
    auth.register_and_login()
    add(client, name="Gym", schedule="days", days=["0", "4"])
    html = client.get(f"/habits/{habit(app, 'Gym')['id']}/edit").get_data(as_text=True)
    assert html.count("checked><span>") == 2              # Mon and Fri ticked, nothing else


def test_add_certain_days_habit(app, client, auth):
    auth.register_and_login()
    add(client, name="Gym", schedule="days", days=["0", "2", "4"])
    assert habit(app, "Gym")["days"] == "0,2,4"
    assert "Mon Wed Fri" in client.get("/habits/").get_data(as_text=True)


def test_add_weekly_and_counting_and_quit(app, client, auth):
    auth.register_and_login()
    add(client, name="Run", schedule="weekly", per_week="3")
    add(client, name="Water", goal_count="8", unit="glasses")
    add(client, name="No smoking", kind="quit", schedule="days", goal_count="5")
    assert habit(app, "Run")["per_week"] == 3
    assert (habit(app, "Water")["goal_count"], habit(app, "Water")["unit"]) == (8, "glasses")
    quit = habit(app, "No smoking")
    assert (quit["kind"], quit["schedule"], quit["goal_count"]) == ("quit", "daily", 1)


@pytest.mark.parametrize("form, error", [
    ({"schedule": "days"}, "at least one day"),
    ({"schedule": "weekly", "per_week": "7"}, "1 to 6"),
    ({"goal_count": "0"}, "between 1 and 100"),
    ({"goal_count": "lots"}, "whole numbers"),
    ({"kind": "hack"}, "pick from the options"),
    ({"schedule": "hourly"}, "pick from the options"),
])
def test_bad_habit_options_rejected(app, client, auth, form, error):
    auth.register_and_login()
    assert error in add(client, name="X", **form).get_data(as_text=True)
    assert habit(app, "X") is None


def test_edit_habit(app, client, auth):
    auth.register_and_login()
    add(client, name="Read")
    h = habit(app, "Read")
    assert "Edit habit" in client.get(f"/habits/{h['id']}/edit").get_data(as_text=True)
    client.post(f"/habits/{h['id']}/edit", data={"name": "Read books", "icon": "📚",
                                                  "schedule": "weekly", "per_week": "4"})
    edited = habit(app, "Read books")
    assert (edited["icon"], edited["schedule"], edited["per_week"]) == ("📚", "weekly", 4)


def test_cant_edit_or_tap_someone_elses_habit(app, client, auth):
    auth.register_and_login()
    add(client, name="Mine")
    habit_id = habit(app, "Mine")["id"]
    auth.logout()
    auth.register_and_login(name="Other", email="other@example.com")
    assert client.get(f"/habits/{habit_id}/edit").status_code == 404
    for action in ("toggle", "minus", "slip"):
        assert client.post(f"/habits/{habit_id}/{action}").status_code == 404


# ── Counting habits ─────────────────────────────────────────────
def test_counting_habit_taps_add_up(app, client, auth):
    auth.register_and_login()
    add(client, name="Water", icon="💧", goal_count="3", unit="glasses")
    hid = habit(app, "Water")["id"]
    first = client.post(f"/habits/{hid}/toggle", headers=HTMX)
    assert "1/3 glasses" in first.get_data(as_text=True)
    assert "HX-Trigger" not in first.headers or "+5 XP" not in first.headers["HX-Trigger"]
    client.post(f"/habits/{hid}/toggle", headers=HTMX)
    third = client.post(f"/habits/{hid}/toggle", headers=HTMX)
    assert "+5 XP" in third.headers["HX-Trigger"] and "3/3" in third.headers["HX-Trigger"]
    assert "<strong>1/2</strong>" in third.get_data(as_text=True)     # ring: habit done, check-in not
    client.post(f"/habits/{hid}/minus", headers=HTMX)
    with connect(app) as db:
        assert db.execute("SELECT amount FROM habit_logs").fetchone()[0] == 2


def test_minus_to_zero_removes_the_day(app, client, auth):
    auth.register_and_login()
    add(client, name="Water", goal_count="8")
    hid = habit(app, "Water")["id"]
    client.post(f"/habits/{hid}/toggle")
    client.post(f"/habits/{hid}/minus")
    client.post(f"/habits/{hid}/minus")
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM habit_logs").fetchone()[0] == 0


def test_partial_days_dont_count_as_ticks_or_streaks(app, client, auth):
    auth.register_and_login()
    add(client, name="Water", goal_count="8")
    hid = habit(app, "Water")["id"]
    with connect(app) as db:
        for n in range(1, 4):
            db.execute("INSERT INTO habit_logs (habit_id, log_date, amount) VALUES (?, ?, 5)",
                       (hid, (today() - timedelta(days=n)).isoformat()))
    from achievements import gather_stats
    with app.app_context(), connect(app) as db:
        stats = gather_stats(db, 1)
    assert stats["habit_ticks"] == 0 and stats["habit_streak"] == 0


# ── Certain days and times a week ───────────────────────────────
def test_day_off_habits_leave_todays_list(app, client, auth):
    auth.register_and_login()
    off_day = str((today().weekday() + 1) % 7)
    add(client, name="Only tomorrow", schedule="days", days=[off_day])
    add(client, name="Every day")
    html = client.get("/").get_data(as_text=True)
    assert "Day off today: ✅ Only tomorrow" in html
    assert "0/2" in html                         # ring: the daily habit + check-in only


def test_weekly_habit_counts_as_done_once_target_met(app, client, auth):
    auth.register_and_login()
    add(client, name="Run", schedule="weekly", per_week="1")
    hid = habit(app, "Run")["id"]
    client.post(f"/habits/{hid}/toggle")
    with connect(app) as db:                     # pretend the run happened earlier this week
        db.execute("UPDATE habit_logs SET log_date = ?", ((today() - timedelta(days=today().weekday())).isoformat(),))
    html = client.get("/").get_data(as_text=True)
    if today().weekday() > 0:                    # (on Monday, earlier this week = today)
        assert "✅ 1/1 this week" in html and "1/2" in html


# ── Quit habits ─────────────────────────────────────────────────
def test_quit_habit_days_clean_and_slips(app, client, auth):
    auth.register_and_login()
    add(client, name="No smoking", icon="🚭", kind="quit")
    hid = habit(app, "No smoking")["id"]
    with connect(app) as db:
        db.execute("UPDATE habits SET created_at = datetime('now', '-12 days')")
    html = client.get("/").get_data(as_text=True)
    assert "12 days clean" in html and "0/1" in html     # not part of the ring
    response = client.post(f"/habits/{hid}/slip", headers=HTMX)
    assert "Fresh start today" in response.get_data(as_text=True)
    assert "doesn&#39;t erase" in response.headers["HX-Trigger"] or "doesn't erase" in response.headers["HX-Trigger"]
    client.post(f"/habits/{hid}/slip")                   # undo
    assert "12 days clean" in client.get("/").get_data(as_text=True)


def test_quit_habits_cant_be_ticked(app, client, auth):
    auth.register_and_login()
    add(client, name="No sugar", kind="quit")
    assert client.post(f"/habits/{habit(app, 'No sugar')['id']}/toggle").status_code == 400
    add(client, name="Read")
    assert client.post(f"/habits/{habit(app, 'Read')['id']}/slip").status_code == 400


# ── Streak freezes ──────────────────────────────────────────────
def checkins(app, *days_back):
    with connect(app) as db:
        for n in days_back:
            db.execute("INSERT INTO checkins (user_id, log_date, energy, mood, focus, score) VALUES (1, ?, 3, 3, 3, 50)",
                       ((today() - timedelta(days=n)).isoformat(),))


def run_freezes(app, last_checked_days_ago):
    with connect(app) as db:
        db.execute("UPDATE users SET freezes_checked_on = ?",
                   ((today() - timedelta(days=last_checked_days_ago)).isoformat(),))
    with app.app_context(), connect(app) as db:
        return apply_freezes(db, 1, today())


def test_everyone_starts_with_one_freeze(app, client, auth):
    auth.register_and_login()
    assert "🧊 ×1" in client.get("/").get_data(as_text=True)


def test_freeze_saves_a_streak_after_two_misses(app, client, auth):
    auth.register_and_login()
    checkins(app, 3, 4, 5)                       # missed 2 days ago and yesterday
    used = run_freezes(app, 3)
    assert used == [today() - timedelta(days=1)]
    with connect(app) as db:
        assert db.execute("SELECT freezes FROM users").fetchone()[0] == 0
    client.post("/checkin/quick", data={"energy": 3, "mood": 3, "focus": 3})
    assert "4-day streak" in client.get("/").get_data(as_text=True)


def test_one_miss_doesnt_use_a_freeze(app, client, auth):
    auth.register_and_login()
    checkins(app, 2, 3)                          # only yesterday missed
    assert run_freezes(app, 3) == []


def test_no_freezes_left_means_streak_breaks(app, client, auth):
    auth.register_and_login()
    with connect(app) as db:
        db.execute("UPDATE users SET freezes = 0")
    checkins(app, 3, 4)
    assert run_freezes(app, 3) == []


def test_freezes_run_once_when_opening_the_app(app, client, auth):
    auth.register_and_login()
    checkins(app, 3, 4)
    with connect(app) as db:
        db.execute("UPDATE users SET freezes_checked_on = ?", ((today() - timedelta(days=3)).isoformat(),))
    with client.session_transaction() as session:
        session.pop("freezes_on", None)
    html = client.get("/", follow_redirects=True).get_data(as_text=True)
    assert "streak freeze saved your streak" in html
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM frozen_days").fetchone()[0] == 1


def test_freeze_protects_habit_streaks_too(app, client, auth):
    auth.register_and_login()
    add(client, name="Read")
    hid = habit(app, "Read")["id"]
    with connect(app) as db:
        for n in (3, 4, 5):
            db.execute("INSERT INTO habit_logs (habit_id, log_date) VALUES (?, ?)",
                       (hid, (today() - timedelta(days=n)).isoformat()))
        db.execute("INSERT INTO frozen_days (user_id, day) VALUES (1, ?)",
                   ((today() - timedelta(days=1)).isoformat(),))
    client.post(f"/habits/{hid}/toggle")
    assert "4-day streak" in client.get("/").get_data(as_text=True)


def test_earn_a_freeze_every_7_days(app, client, auth):
    auth.register_and_login()
    checkins(app, *range(1, 7))                  # 6 days before today
    response = client.post("/checkin/quick", data={"energy": 3, "mood": 3, "focus": 3}, headers=HTMX)
    assert "earned a streak freeze (2/2)" in response.headers["HX-Trigger"]
    with connect(app) as db:
        assert db.execute("SELECT freezes FROM users").fetchone()[0] == 2
    client.post("/checkin/quick", data={"energy": 4, "mood": 4, "focus": 4})   # same day again: no double
    with app.app_context(), connect(app) as db:
        assert award_freeze(db, 1, today()) is None
        assert db.execute("SELECT freezes FROM users").fetchone()[0] == 2


def test_cant_hold_more_than_two(app, client, auth):
    auth.register_and_login()
    with connect(app) as db:
        db.execute("UPDATE users SET freezes = 2")
    checkins(app, *range(0, 7))
    with app.app_context(), connect(app) as db:
        assert award_freeze(db, 1, today()) is None
        assert db.execute("SELECT freezes FROM users").fetchone()[0] == 2


def test_missed_yesterday_warning_on_today(app, client, auth):
    auth.register_and_login()
    checkins(app, 2, 3, 4)
    html = client.get("/").get_data(as_text=True)
    assert "You missed yesterday" in html and "3-day streak" in html


# ── Other pages follow the same rules ───────────────────────────
def test_dashboard_and_review_use_schedules(app, client, auth):
    auth.register_and_login()
    add(client, name="Gym", schedule="days", days=[str(today().weekday())])
    hid = habit(app, "Gym")["id"]
    client.post(f"/habits/{hid}/toggle")
    client.post("/checkin/quick", data={"energy": 3, "mood": 3, "focus": 3})   # dashboard needs a check-in
    assert "100%" in client.get("/dashboard/").get_data(as_text=True)
    assert "1/1" in client.get("/review/").get_data(as_text=True)


def test_export_includes_amounts(app, client, auth):
    auth.register_and_login()
    add(client, name="Water", goal_count="8")
    hid = habit(app, "Water")["id"]
    for _ in range(3):
        client.post(f"/habits/{hid}/toggle")
    csv = client.get("/export/habits.csv").get_data(as_text=True)
    assert "amount" in csv.splitlines()[0] and ",3," in csv
