from datetime import date, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from auth import login_required
from dashboard import checkin_summary
from db import get_db
from timeutil import user_today

bp = Blueprint("review", __name__, url_prefix="/review")


def monday_of(day):
    """The Monday of the week that `day` is in."""
    return day - timedelta(days=day.weekday())


def parse_week(value):
    """Turn ?week=2026-11-12 into that week's Monday. Bad or missing → this week."""
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        day = user_today()
    return monday_of(min(day, user_today()))   # no future weeks


@bp.route("/", methods=["GET", "POST"])
@login_required
def week():
    db = get_db()
    user_id = g.user["id"]
    start = parse_week(request.args.get("week"))
    end = start + timedelta(days=6)
    today = user_today()

    # ── Save reflections (upsert, like check-ins) ────────────────
    if request.method == "POST":
        rating = request.form.get("rating", type=int)
        if rating is not None and not 1 <= rating <= 5:
            rating = None
        db.execute(
            """
            INSERT INTO weekly_reviews
                (user_id, week_start, rating, went_well, challenges, lessons, next_focus)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id, week_start) DO UPDATE SET
                rating     = excluded.rating,
                went_well  = excluded.went_well,
                challenges = excluded.challenges,
                lessons    = excluded.lessons,
                next_focus = excluded.next_focus,
                updated_at = datetime('now')
            """,
            (user_id, start.isoformat(), rating,
             request.form.get("went_well", "").strip(),
             request.form.get("challenges", "").strip(),
             request.form.get("lessons", "").strip(),
             request.form.get("next_focus", "").strip()),
        )
        db.commit()
        flash("Weekly review saved. 📝", "success")
        return redirect(url_for("review.week", week=start.isoformat()))

    # ── The week's numbers, calculated from your raw data ────────
    summary = checkin_summary(db, user_id, start, end)

    best_day = db.execute(
        """
        SELECT log_date, score FROM checkins
        WHERE user_id = ? AND log_date BETWEEN ? AND ?
        ORDER BY score DESC, log_date
        LIMIT 1
        """,
        (user_id, start.isoformat(), end.isoformat()),
    ).fetchone()

    notes = db.execute(
        """
        SELECT log_date, accomplished, wins, blockers FROM checkins
        WHERE user_id = ? AND log_date BETWEEN ? AND ?
        ORDER BY log_date
        """,
        (user_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    wins = [(n["log_date"], n["wins"]) for n in notes if n["wins"]]
    blockers = [(n["log_date"], n["blockers"]) for n in notes if n["blockers"]]

    # Days that count for habits: this week, but not after today
    days_so_far = (min(end, today) - start).days + 1
    habits = db.execute(
        """
        SELECT h.icon, h.name, COUNT(hl.log_date) AS done
        FROM habits h
        LEFT JOIN habit_logs hl
               ON hl.habit_id = h.id AND hl.log_date BETWEEN ? AND ?
        WHERE h.user_id = ? AND h.is_active = 1
        GROUP BY h.id
        ORDER BY h.created_at
        """,
        (start.isoformat(), end.isoformat(), user_id),
    ).fetchall()

    finished_tasks = db.execute(
        """
        SELECT t.title, p.title AS project_title
        FROM tasks t
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE t.user_id = ? AND t.status = 'done'
          AND date(t.completed_at) BETWEEN ? AND ?
        ORDER BY t.completed_at
        """,
        (user_id, start.isoformat(), end.isoformat()),
    ).fetchall()

    review = db.execute(
        "SELECT * FROM weekly_reviews WHERE user_id = ? AND week_start = ?",
        (user_id, start.isoformat()),
    ).fetchone()

    past_reviews = db.execute(
        """
        SELECT week_start, rating FROM weekly_reviews
        WHERE user_id = ?
        ORDER BY week_start DESC
        LIMIT 8
        """,
        (user_id,),
    ).fetchall()

    previous_week = start - timedelta(weeks=1)
    next_week = start + timedelta(weeks=1)

    return render_template(
        "review/week.html",
        start=start, end=end, summary=summary, best_day=best_day,
        wins=wins, blockers=blockers, habits=habits, days_so_far=days_so_far,
        finished_tasks=finished_tasks, review=review, past_reviews=past_reviews,
        previous_week=previous_week,
        next_week=next_week if next_week <= today else None,
        is_current=(start == monday_of(today)),
    )
