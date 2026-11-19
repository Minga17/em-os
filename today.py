"""Everything the "Today" screen shows: greeting, progress ring, streak and habits."""
from collections import defaultdict
from datetime import date

from streaks import current_streak
from timeutil import user_now, user_today


def greeting():
    """Good morning / afternoon / evening, on the user's own clock."""
    hour = user_now().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def habit_items(db, user_id, today):
    """Every active habit with: done today? current streak?  (2 queries total)"""
    habits = db.execute(
        "SELECT id, name, icon FROM habits WHERE user_id = ? AND is_active = 1 ORDER BY created_at",
        (user_id,),
    ).fetchall()
    done_by_habit = defaultdict(set)
    for habit_id, log_date in db.execute(
        """SELECT hl.habit_id, hl.log_date FROM habit_logs hl
           JOIN habits h ON h.id = hl.habit_id
           WHERE h.user_id = ? AND h.is_active = 1""",
        (user_id,),
    ):
        done_by_habit[habit_id].add(date.fromisoformat(log_date))

    return [{
        "id": h["id"],
        "name": h["name"],
        "icon": h["icon"],
        "done": today in done_by_habit[h["id"]],
        "streak": current_streak(done_by_habit[h["id"]], today),
    } for h in habits]


def today_summary(db, user_id):
    """Numbers for the progress ring and the streak flame."""
    today = user_today()
    habits = habit_items(db, user_id, today)
    checkin_dates = {date.fromisoformat(r[0]) for r in db.execute(
        "SELECT log_date FROM checkins WHERE user_id = ?", (user_id,))}
    checked_in = today in checkin_dates

    # The ring counts every habit + the daily check-in as one "step" each
    steps_total = len(habits) + 1
    steps_done = sum(h["done"] for h in habits) + (1 if checked_in else 0)

    return {
        "today": today,
        "greeting": greeting(),
        "habits": habits,
        "checked_in": checked_in,
        "streak": current_streak(checkin_dates, today),
        "steps_done": steps_done,
        "steps_total": steps_total,
        "percent": steps_done * 100 // steps_total,
        "all_done": steps_done == steps_total,
    }
