from datetime import date, timedelta

from flask import Blueprint, g, render_template, request

from auth import login_required
from db import get_db

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

PERIODS = [7, 30, 90]
WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def checkin_summary(db, user_id, start, end):
    """Averages and totals for check-ins between two dates (inclusive)."""
    return db.execute(
        """
        SELECT COUNT(*)                     AS days_logged,
               ROUND(AVG(score), 1)         AS avg_score,
               ROUND(AVG(energy), 1)        AS avg_energy,
               ROUND(AVG(mood), 1)          AS avg_mood,
               ROUND(AVG(focus), 1)         AS avg_focus,
               ROUND(SUM(focused_hours), 1) AS total_hours
        FROM checkins
        WHERE user_id = ? AND log_date BETWEEN ? AND ?
        """,
        (user_id, start.isoformat(), end.isoformat()),
    ).fetchone()


def change(current, previous):
    """Difference between two numbers, or None if either is missing."""
    if current is None or previous is None:
        return None
    return round(current - previous, 1)


@bp.route("/")
@login_required
def index():
    db = get_db()
    user_id = g.user["id"]

    # Which period? /dashboard/?days=7 — only allow our three options
    days = request.args.get("days", 30, type=int)
    if days not in PERIODS:
        days = 30

    today = date.today()
    start = today - timedelta(days=days - 1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)

    # ── 1. Summary numbers, compared with the period before ──────
    now = checkin_summary(db, user_id, start, today)
    before = checkin_summary(db, user_id, prev_start, prev_end)
    deltas = {
        "score": change(now["avg_score"], before["avg_score"]),
        "hours": change(now["total_hours"], before["total_hours"]),
    }

    # ── 2. Daily score + hours chart (every day, even empty ones) ─
    rows = db.execute(
        """
        SELECT log_date, score, focused_hours
        FROM checkins
        WHERE user_id = ? AND log_date BETWEEN ? AND ?
        """,
        (user_id, start.isoformat(), today.isoformat()),
    ).fetchall()
    by_date = {row["log_date"]: row for row in rows}

    max_hours = max([row["focused_hours"] for row in rows] + [1])
    daily = []
    for i in range(days):
        day = start + timedelta(days=i)
        row = by_date.get(day.isoformat())
        daily.append({
            "date": day,
            "score": row["score"] if row else None,
            "hours": row["focused_hours"] if row else None,
            "hours_pct": round(row["focused_hours"] / max_hours * 100) if row else 0,
        })

    # ── 3. Best day of the week ─────────────────────────────────
    weekday_rows = db.execute(
        """
        SELECT CAST(strftime('%w', log_date) AS INTEGER) AS weekday,
               ROUND(AVG(score), 1) AS avg_score,
               COUNT(*)             AS days
        FROM checkins
        WHERE user_id = ? AND log_date BETWEEN ? AND ?
        GROUP BY weekday
        """,
        (user_id, start.isoformat(), today.isoformat()),
    ).fetchall()
    weekday_scores = {row["weekday"]: row for row in weekday_rows}
    weekdays = [
        {"name": WEEKDAYS[i],
         "avg": weekday_scores[i]["avg_score"] if i in weekday_scores else None,
         "days": weekday_scores[i]["days"] if i in weekday_scores else 0}
        for i in [1, 2, 3, 4, 5, 6, 0]          # Monday first
    ]
    best_day = max((w for w in weekdays if w["avg"] is not None),
                   key=lambda w: w["avg"], default=None)

    # ── 4. Habit completion rates ───────────────────────────────
    habit_rows = db.execute(
        """
        SELECT h.name, h.icon,
               date(h.created_at)  AS created,
               COUNT(hl.log_date)  AS done
        FROM habits h
        LEFT JOIN habit_logs hl
               ON hl.habit_id = h.id AND hl.log_date BETWEEN ? AND ?
        WHERE h.user_id = ? AND h.is_active = 1
        GROUP BY h.id
        ORDER BY h.created_at
        """,
        (start.isoformat(), today.isoformat(), user_id),
    ).fetchall()
    habits = []
    for h in habit_rows:
        # Don't count days before the habit existed
        first_day = max(start, date.fromisoformat(h["created"]))
        possible = max((today - first_day).days + 1, 1)
        habits.append({
            "name": h["name"],
            "icon": h["icon"],
            "done": h["done"],
            "possible": possible,
            "rate": min(round(h["done"] * 100 / possible), 100),
        })

    # ── 5. Tasks finished per week (last 8 weeks) ────────────────
    this_monday = today - timedelta(days=today.weekday())
    first_monday = this_monday - timedelta(weeks=7)
    week_rows = db.execute(
        """
        SELECT date(completed_at, 'weekday 0', '-6 days') AS week_start,
               COUNT(*) AS finished
        FROM tasks
        WHERE user_id = ? AND status = 'done' AND date(completed_at) >= ?
        GROUP BY week_start
        """,
        (user_id, first_monday.isoformat()),
    ).fetchall()
    finished_by_week = {row["week_start"]: row["finished"] for row in week_rows}
    weeks = []
    for i in range(8):
        monday = first_monday + timedelta(weeks=i)
        weeks.append({"start": monday, "finished": finished_by_week.get(monday.isoformat(), 0)})
    max_week = max([w["finished"] for w in weeks] + [1])

    return render_template(
        "dashboard/index.html",
        days=days, periods=PERIODS,
        now=now, before=before, deltas=deltas,
        daily=daily, weekdays=weekdays, best_day=best_day,
        habits=habits, weeks=weeks, max_week=max_week,
    )

