"""Everything the "Today" screen shows: greeting, progress ring, streak and habits."""
from datetime import date

from habit_model import all_statuses, frozen_days
from streaks import day_streak
from i18n import _
from timeutil import user_now, user_today


def greeting():
    """Good morning / afternoon / evening, on the user's own clock."""
    hour = user_now().hour
    if hour < 12:
        return _("Good morning")
    if hour < 18:
        return _("Good afternoon")
    return _("Good evening")


def habit_items(db, user_id, today):
    """Every active habit with its status today (see habit_model.py)."""
    return all_statuses(db, user_id, today)


def habits_left(items):
    """Habits still to do today (quit habits and days off don't count)."""
    return [h for h in items if h["due"] and h["kind"] == "build" and not h["counts_done"]]


def today_summary(db, user_id):
    """Numbers for the progress ring and the streak flame."""
    today = user_today()
    items = habit_items(db, user_id, today)
    checkin_dates = {date.fromisoformat(r[0]) for r in db.execute(
        "SELECT log_date FROM checkins WHERE user_id = ?", (user_id,))}
    checked_in = today in checkin_dates
    streak = day_streak(checkin_dates, today, frozen_days(db, user_id))

    # The ring counts each habit due today + the daily check-in as one "step" each
    ring = [h for h in items if h["due"] and h["kind"] == "build"]
    steps_total = len(ring) + 1
    steps_done = sum(h["counts_done"] for h in ring) + (1 if checked_in else 0)

    return {
        "today": today,
        "greeting": greeting(),
        "habits": [h for h in items if h["due"] or h["kind"] == "quit"],
        "resting": [h for h in items if not h["due"] and h["kind"] == "build"],
        "checked_in": checked_in,
        "streak": streak.current,
        "at_risk": streak.at_risk,
        "freezes": db.execute("SELECT freezes FROM users WHERE id = ?", (user_id,)).fetchone()[0],
        "steps_done": steps_done,
        "steps_total": steps_total,
        "percent": steps_done * 100 // steps_total,
        "all_done": steps_done == steps_total,
    }
