"""Private analytics and the owner's stats page.

What we record: only WHICH DAYS each person opened the app. No page views,
no clicks, no third-party trackers. Everything else on the stats page is
counted from data the app already has.

Only people listed in ADMIN_EMAILS (with a confirmed email) can see /admin.
Everyone else gets "page not found", so they don't even know it exists.
"""
from datetime import date, timedelta

from flask import Blueprint, abort, g, render_template, session

from db import get_db
from mailer import admin_emails
from timeutil import user_today

bp = Blueprint("admin", __name__, url_prefix="/admin")


def is_admin(user):
    return bool(user and user["email_verified"] and user["email"].lower() in admin_emails())


@bp.before_app_request
def record_active_day():
    """Remember that this person used the app today (once per day, not every click)."""
    user = g.get("user")
    if user is None:
        return
    today = user_today().isoformat()
    if session.get("seen_on") == today:
        return
    db = get_db()
    db.execute("INSERT OR IGNORE INTO activity_days (user_id, day) VALUES (?, ?)", (user["id"], today))
    db.commit()
    session["seen_on"] = today


@bp.app_context_processor
def admin_flag():
    return {"is_admin": is_admin(g.get("user"))}


def percent(part, whole):
    return round(part * 100 / whole) if whole else None


def return_rate(db, days_after):
    """Of people who signed up at least N days ago, how many came back on day N?"""
    row = db.execute("""
        WITH signups AS (SELECT user_id, MIN(day) AS first_day FROM activity_days GROUP BY user_id)
        SELECT COUNT(*) AS eligible,
               SUM(EXISTS (SELECT 1 FROM activity_days a WHERE a.user_id = s.user_id
                           AND a.day = date(s.first_day, ?))) AS came_back,
               SUM(EXISTS (SELECT 1 FROM checkins c WHERE c.user_id = s.user_id
                           AND c.log_date = date(s.first_day, ?))) AS checked_in
        FROM signups s WHERE s.first_day <= date('now', ?)
    """, (f"+{days_after} day", f"+{days_after} day", f"-{days_after} day")).fetchone()
    return {"days": days_after, "eligible": row["eligible"],
            "came_back": row["came_back"] or 0, "checked_in": row["checked_in"] or 0,
            "percent": percent(row["came_back"] or 0, row["eligible"]),
            "checkin_percent": percent(row["checked_in"] or 0, row["eligible"])}


def gather(db, today):
    one = lambda sql, *args: db.execute(sql, args).fetchone()[0] or 0  # noqa: E731
    users = one("SELECT COUNT(*) FROM users")
    week_ago, month_ago = (today - timedelta(days=6)).isoformat(), (today - timedelta(days=29)).isoformat()

    first_days = "SELECT user_id, MIN(day) AS d FROM activity_days GROUP BY user_id"
    daily = dict(db.execute(
        "SELECT day, COUNT(*) FROM activity_days WHERE day >= ? GROUP BY day",
        ((today - timedelta(days=13)).isoformat(),)).fetchall())
    last_14 = [(d, daily.get(d.isoformat(), 0))
               for d in (today - timedelta(days=i) for i in range(13, -1, -1))]

    return {
        "users": users,
        "new_7": one(f"SELECT COUNT(*) FROM ({first_days}) WHERE d >= ?", week_ago),
        "new_30": one(f"SELECT COUNT(*) FROM ({first_days}) WHERE d >= ?", month_ago),
        "active_today": one("SELECT COUNT(*) FROM activity_days WHERE day = ?", today.isoformat()),
        "active_7": one("SELECT COUNT(DISTINCT user_id) FROM activity_days WHERE day >= ?", week_ago),
        "active_30": one("SELECT COUNT(DISTINCT user_id) FROM activity_days WHERE day >= ?", month_ago),
        "verified": percent(one("SELECT COUNT(*) FROM users WHERE email_verified = 1"), users),
        "reminders": percent(one("SELECT COUNT(DISTINCT user_id) FROM push_subscriptions"), users),
        "returns": [return_rate(db, n) for n in (1, 7, 30)],
        "checkins_7": one("SELECT COUNT(*) FROM checkins WHERE log_date >= ?", week_ago),
        "habit_ticks_7": one("""SELECT COUNT(*) FROM habit_logs l JOIN habits h ON h.id = l.habit_id
                                WHERE l.log_date >= ? AND l.amount >= h.goal_count""", week_ago),
        "tasks_done": one("SELECT COUNT(*) FROM tasks WHERE status = 'done'"),
        "goals": one("SELECT COUNT(*) FROM goals"),
        "insights_helpful": one("SELECT COUNT(*) FROM insight_votes WHERE helpful = 1"),
        "insights_hidden": one("SELECT COUNT(*) FROM insight_votes WHERE helpful = 0"),
        "last_14": last_14,
        "max_daily": max([n for _, n in last_14] + [1]),
    }


@bp.route("/")
def index():
    if not is_admin(g.get("user")):
        abort(404)
    db = get_db()
    feedback = db.execute("""
        SELECT f.kind, f.message, f.page, f.created_at, u.name, u.email
        FROM feedback f JOIN users u ON u.id = f.user_id
        ORDER BY f.id DESC LIMIT 50""").fetchall()
    return render_template("admin/index.html", s=gather(db, user_today()), feedback=feedback)


def day_name(d: date):
    return d.strftime("%a %d")


bp.add_app_template_filter(day_name, "short_day")
