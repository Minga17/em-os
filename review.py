from datetime import date, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from i18n import _, _n, format_day

from auth import login_required
from dashboard import change, checkin_summary
from db import get_db
from habit_model import completion_rates, frozen_days
from streaks import day_streak
from timeutil import user_today

bp = Blueprint("review", __name__, url_prefix="/review")


def short_list(titles, limit=3):
    shown = ", ".join(titles[:limit])
    if len(titles) <= limit:
        return shown
    return _("{shown} and {n} more", shown=shown, n=len(titles) - limit)


def draft_answers(best_day, finished_tasks, habits, wins, blockers, upcoming):
    """A first draft of the reflection, written from the week's data. Every line is editable."""
    went_well, hard, focus = [], [], []
    if finished_tasks:
        titles = [t["title"] for t in finished_tasks]
        went_well.append(_n("Finished {n} task: {titles}.", "Finished {n} tasks: {titles}.", len(titles),
                            titles=short_list(titles)))
    if best_day:
        day = date.fromisoformat(best_day["log_date"])
        went_well.append(_("Best day: {day} (score {score}).", day=format_day(day, "weekday"), score=best_day["score"]))
    strong = [h for h in habits if h["possible"] and h["rate"] >= 80]
    if strong:
        went_well.append(_("Kept up {habits}.", habits=", ".join(
            f"{h['icon']} {h['name']} ({h['done']}/{h['possible']})" for h in strong[:3])))
    went_well += [text for _, text in wins[:2]]

    hard += [text for _, text in blockers[:2]]
    weak = [h for h in habits if h["possible"] >= 2 and h["rate"] < 50]
    if weak:
        hard.append(_("Slipped on {habits}.", habits=", ".join(
            f"{h['icon']} {h['name']} ({h['done']}/{h['possible']})" for h in weak[:3])))
    if upcoming:
        focus.append(_("Get done: {tasks}.", tasks=short_list([t["title"] for t in upcoming])))
    if weak:
        focus.append(_("Get back on track with {habit}.", habit=f"{weak[0]['icon']} {weak[0]['name']}"))
    return {"went_well": "\n".join(went_well), "challenges": "\n".join(hard),
            "lessons": "", "next_focus": "\n".join(focus)}


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
        flash(_('Weekly review saved. 📝'), "success")
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
    habits = completion_rates(db, user_id, start, min(end, today))

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

    # ── Compared with the week before ────────────────────────────
    before = checkin_summary(db, user_id, start - timedelta(weeks=1), start - timedelta(days=1))
    tasks_before = db.execute(
        "SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'done' AND date(completed_at) BETWEEN ? AND ?",
        (user_id, (start - timedelta(weeks=1)).isoformat(), (start - timedelta(days=1)).isoformat()),
    ).fetchone()[0]
    compare = {
        "score": change(summary["avg_score"], before["avg_score"]),
        "days": summary["days_logged"] - before["days_logged"] if before["days_logged"] else None,
        "hours": change(summary["total_hours"], before["total_hours"]),
        "tasks": len(finished_tasks) - tasks_before if tasks_before else None,
    }
    checkin_dates = {date.fromisoformat(r[0]) for r in db.execute(
        "SELECT log_date FROM checkins WHERE user_id = ? AND log_date <= ?", (user_id, min(end, today).isoformat()))}
    streak = day_streak(checkin_dates, min(end, today), frozen_days(db, user_id)).current
    focus_minutes = db.execute(
        "SELECT COALESCE(SUM(minutes), 0) FROM focus_sessions WHERE user_id = ? AND log_date BETWEEN ? AND ?",
        (user_id, start.isoformat(), end.isoformat())).fetchone()[0]

    # ── A first draft of the answers, if nothing's been written yet ──
    upcoming = db.execute(
        """SELECT title FROM tasks WHERE user_id = ? AND status != 'done' AND due_date IS NOT NULL
           AND due_date <= ? ORDER BY due_date, priority LIMIT 5""",
        (user_id, (end + timedelta(days=7)).isoformat())).fetchall()
    written = review and any(review[k] for k in ("went_well", "challenges", "lessons", "next_focus"))
    answers = dict(review) if written else draft_answers(best_day, finished_tasks, habits, wins, blockers, upcoming)
    drafted = not written and any(answers.values())

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
        compare=compare, streak=streak, focus_minutes=focus_minutes,
        answers=answers, drafted=drafted,
        previous_week=previous_week,
        next_week=next_week if next_week <= today else None,
        is_current=(start == monday_of(today)),
    )
