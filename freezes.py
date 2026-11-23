"""Streak freezes 🧊

- Everyone starts with 1. You earn 1 more every 7 days of streak (you can hold 2).
- Missing one day never breaks a streak (never miss twice). A freeze is used
  automatically only when you'd miss a SECOND day in a row: that day is frozen,
  and your streak (and your habits' streaks) live on.
"""
from datetime import date, timedelta

from flask import Blueprint, flash, g, session
from i18n import _, format_day

from db import get_db
from habit_model import frozen_days
from streaks import current_streak
from timeutil import user_today

bp = Blueprint("freezes", __name__)

EARN_EVERY = 7
MAX_FREEZES = 2


def checkin_days(db, user_id):
    return {date.fromisoformat(r[0]) for r in db.execute(
        "SELECT log_date FROM checkins WHERE user_id = ?", (user_id,))}


def apply_freezes(db, user_id, today):
    """Use freezes for any day (up to yesterday) that would break the streak. Returns frozen days."""
    user = db.execute("SELECT freezes, freezes_checked_on FROM users WHERE id = ?", (user_id,)).fetchone()
    yesterday = today - timedelta(days=1)
    checked = date.fromisoformat(user["freezes_checked_on"]) if user["freezes_checked_on"] else yesterday - timedelta(days=1)
    if checked >= yesterday:
        return []
    kept = checkin_days(db, user_id) | frozen_days(db, user_id)
    left, used = user["freezes"], []
    day = checked + timedelta(days=1)
    while day <= yesterday:
        # The pattern that breaks a streak: kept, missed, missed. Freeze the second miss.
        if (left > 0 and day not in kept and (day - timedelta(days=1)) not in kept
                and (day - timedelta(days=2)) in kept):
            db.execute("INSERT OR IGNORE INTO frozen_days (user_id, day) VALUES (?, ?)",
                       (user_id, day.isoformat()))
            kept.add(day)
            used.append(day)
            left -= 1
        day += timedelta(days=1)
    db.execute("UPDATE users SET freezes = ?, freezes_checked_on = ? WHERE id = ?",
               (left, yesterday.isoformat(), user_id))
    db.commit()
    return used


def award_freeze(db, user_id, today):
    """After a check-in: every 7 days of streak earns a freeze. Returns a message or None."""
    user = db.execute("SELECT freezes, freeze_awarded_on FROM users WHERE id = ?", (user_id,)).fetchone()
    streak = current_streak(checkin_days(db, user_id), today, frozen_days(db, user_id))
    if (streak == 0 or streak % EARN_EVERY or user["freeze_awarded_on"] == today.isoformat()
            or user["freezes"] >= MAX_FREEZES):
        return None
    db.execute("UPDATE users SET freezes = freezes + 1, freeze_awarded_on = ? WHERE id = ?",
               (today.isoformat(), user_id))
    db.commit()
    return _("🧊 {n}-day streak! You earned a streak freeze ({have}/{max}).",
             n=streak, have=user["freezes"] + 1, max=MAX_FREEZES)


@bp.before_app_request
def use_freezes_once_a_day():
    """When someone opens the app on a new day, protect their streak if needed."""
    user = g.get("user")
    if user is None:
        return
    today = user_today()
    if session.get("freezes_on") == today.isoformat():
        return
    used = apply_freezes(get_db(), user["id"], today)
    session["freezes_on"] = today.isoformat()
    for day in used:
        flash(_('🧊 A streak freeze saved your streak on {day}. Welcome back!', day=format_day(day, 'weekday')), "achievement")
