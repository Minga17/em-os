"""Weekly recap card 📸: a picture of your week to keep or share.

Only numbers go on the card (no task names, no notes, no moods), so it's safe
to post. Your first name is left off unless you switch it on.
"""
from datetime import date, timedelta

from flask import Blueprint, g, render_template, request

from achievements import player_progress
from auth import login_required
from dashboard import checkin_summary
from db import get_db
from habit_model import completion_rates, frozen_days
from review import parse_week
from streaks import day_streak
from timeutil import user_today

bp = Blueprint("recap", __name__, url_prefix="/recap")


def week_recap(db, user_id, start, today):
    end = start + timedelta(days=6)
    last = min(end, today)
    summary = checkin_summary(db, user_id, start, end)
    habits = [h for h in completion_rates(db, user_id, start, last) if h["possible"]]
    done = sum(h["done"] for h in habits)
    possible = sum(h["possible"] for h in habits)
    tasks = db.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'done' "
                       "AND date(completed_at) BETWEEN ? AND ?", (user_id, start.isoformat(), end.isoformat())).fetchone()[0]
    focus = db.execute("SELECT COALESCE(SUM(minutes), 0) FROM focus_sessions WHERE user_id = ? "
                       "AND log_date BETWEEN ? AND ?", (user_id, start.isoformat(), end.isoformat())).fetchone()[0]
    best = db.execute("SELECT log_date, score FROM checkins WHERE user_id = ? AND log_date BETWEEN ? AND ? "
                      "ORDER BY score DESC, log_date LIMIT 1", (user_id, start.isoformat(), end.isoformat())).fetchone()
    dates = {date.fromisoformat(r[0]) for r in db.execute(
        "SELECT log_date FROM checkins WHERE user_id = ? AND log_date <= ?", (user_id, last.isoformat()))}
    progress = player_progress(db, user_id)
    return {
        "range": f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}, {end.year}",
        "stats": [
            ("🔥", "day streak", day_streak(dates, last, frozen_days(db, user_id)).current),
            ("📝", "check-ins", f"{summary['days_logged']}/7"),
            ("⭐", "avg score", round(summary["avg_score"]) if summary["avg_score"] is not None else "–"),
            ("🎯", "habits done", f"{round(done * 100 / possible)}%" if possible else "–"),
            ("✅", "tasks finished", tasks),
            ("⏱", "hours focused", round(focus / 60, 1) if focus else round(summary["total_hours"] or 0, 1)),
        ],
        "best_day": date.fromisoformat(best["log_date"]).strftime("%A") if best else None,
        "level": progress["level"], "title": progress["title"],
    }


@bp.route("/")
@login_required
def index():
    db = get_db()
    today = user_today()
    start = parse_week(request.args.get("week"))
    recap = week_recap(db, g.user["id"], start, today)
    recap["name"] = g.user["name"].split()[0] if request.args.get("name") == "1" else None
    return render_template("recap/index.html", recap=recap, start=start, show_name=bool(recap["name"]),
                           previous=start - timedelta(weeks=1),
                           following=start + timedelta(weeks=1) if start + timedelta(weeks=1) <= today else None)
