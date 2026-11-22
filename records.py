"""Hall of Fame 🏅: your personal records and lifetime totals."""
from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, g, render_template

from achievements import player_progress
from auth import login_required
from db import get_db
from habit_model import all_statuses, frozen_days
from streaks import day_streak, week_start
from timeutil import user_today

bp = Blueprint("records", __name__, url_prefix="/records")

MIN_DAYS_FOR_BEST_WEEK = 3      # a week needs 3+ check-ins to count as "best week"


def record(icon, title, value, unit, when=None, detail=None, today=None):
    """One record card. `when` (a date) marks records set in the last 7 days as new."""
    is_new = bool(when and today and (today - when).days < 7)
    return {"icon": icon, "title": title, "value": value, "unit": unit,
            "when": when, "detail": detail, "new": is_new}


def best_row(db, sql, params):
    row = db.execute(sql, params).fetchone()
    return row if row and row[1] else None


def gather_records(db, user_id, today):
    records = []
    checkins = db.execute("SELECT log_date, score, focused_hours FROM checkins WHERE user_id = ?",
                          (user_id,)).fetchall()
    dates = {date.fromisoformat(r["log_date"]) for r in checkins}

    streak = day_streak(dates, today, frozen_days(db, user_id))
    if streak.best:
        records.append(record("🔥", "Longest check-in streak", streak.best, "days",
                              detail=f"Right now: {streak.current} days"))

    habits = all_statuses(db, user_id, today, active_only=False)
    daily = [h for h in habits if h["kind"] == "build" and h["schedule"] != "weekly" and h["best"]]
    if daily:
        top = max(daily, key=lambda h: h["best"])
        records.append(record("🌳", "Longest habit streak", top["best"], "days",
                              detail=f"{top['icon']} {top['name']}"))
    quits = [h for h in habits if h["kind"] == "quit" and h["best"]]
    if quits:
        top = max(quits, key=lambda h: h["best"])
        records.append(record("🛡️", "Longest time clean", top["best"], "days",
                              detail=f"{top['icon']} {top['name']}"))

    row = best_row(db, "SELECT log_date, score FROM checkins WHERE user_id = ? "
                       "ORDER BY score DESC, log_date LIMIT 1", (user_id,))
    if row:
        when = date.fromisoformat(row[0])
        records.append(record("⭐", "Best day", row[1], "/100", when, today=today))

    weeks = defaultdict(list)
    for r in checkins:
        weeks[week_start(date.fromisoformat(r["log_date"]))].append(r["score"])
    full_weeks = {w: sum(s) / len(s) for w, s in weeks.items() if len(s) >= MIN_DAYS_FOR_BEST_WEEK}
    if full_weeks:
        week = max(full_weeks, key=lambda w: (full_weeks[w], w))
        records.append(record("🗓️", "Best week", round(full_weeks[week]), "avg score",
                              week, detail=f"Week of {week.strftime('%b')} {week.day}", today=today))

    row = best_row(db, "SELECT log_date, focused_hours FROM checkins WHERE user_id = ? "
                       "ORDER BY focused_hours DESC, log_date LIMIT 1", (user_id,))
    if row:
        records.append(record("🧠", "Most focused day", round(row[1], 1), "hours",
                              date.fromisoformat(row[0]), today=today))

    row = best_row(db, """SELECT date(completed_at) AS d, COUNT(*) FROM tasks
                          WHERE user_id = ? AND status = 'done' AND completed_at IS NOT NULL
                          GROUP BY d ORDER BY COUNT(*) DESC, d LIMIT 1""", (user_id,))
    if row:
        records.append(record("✅", "Most tasks in a day", row[1], "tasks", date.fromisoformat(row[0]), today=today))

    row = best_row(db, """SELECT l.log_date, COUNT(*) FROM habit_logs l JOIN habits h ON h.id = l.habit_id
                          WHERE h.user_id = ? AND l.amount >= h.goal_count
                          GROUP BY l.log_date ORDER BY COUNT(*) DESC, l.log_date LIMIT 1""", (user_id,))
    if row:
        records.append(record("🎯", "Most habits in a day", row[1], "habits", date.fromisoformat(row[0]), today=today))

    row = best_row(db, "SELECT log_date, minutes FROM focus_sessions WHERE user_id = ? AND minutes IS NOT NULL "
                       "ORDER BY minutes DESC, id LIMIT 1", (user_id,))
    if row:
        records.append(record("⏱", "Longest focus session", row[1], "minutes", date.fromisoformat(row[0]), today=today))
    return records


@bp.route("/")
@login_required
def index():
    db = get_db()
    user_id = g.user["id"]
    today = user_today()
    progress = player_progress(db, user_id)
    stats = progress["stats"]
    focus_hours = db.execute("SELECT COALESCE(SUM(minutes), 0) FROM focus_sessions WHERE user_id = ?",
                             (user_id,)).fetchone()[0] / 60
    first_day = db.execute(
        """SELECT MIN(d) FROM (SELECT MIN(day) AS d FROM activity_days WHERE user_id = :u
                              UNION ALL SELECT MIN(log_date) FROM checkins WHERE user_id = :u
                              UNION ALL SELECT date(created_at) FROM users WHERE id = :u)""",
        {"u": user_id}).fetchone()[0]
    totals = [
        ("📝", "Check-ins", stats["checkins"]),
        ("🔥", "Habits done", stats["habit_ticks"]),
        ("✅", "Tasks finished", stats["tasks_done"]),
        ("⏱", "Hours focused", round(focus_hours, 1)),
        ("🌙", "Days closed", stats["days_closed"]),
        ("🏆", "Goals achieved", stats["goals_achieved"]),
    ]
    return render_template("records/index.html", records=gather_records(db, user_id, today),
                           totals=totals, progress=progress,
                           days_with_emos=(today - date.fromisoformat(first_day)).days + 1 if first_day else 1)
