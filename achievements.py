"""Achievements, XP and levels.

XP is never stored: it's calculated from your real data (check-ins, habits,
tasks...), so it can never get out of sync. Only the moment a badge was
unlocked is stored, so badges stay earned and we know which ones are new.
"""
from collections import defaultdict
from datetime import date

from flask import Blueprint, flash, g, render_template, request

from auth import login_required
from db import get_db
from streaks import best_streak
from notify import send_toasts, toast
from timeutil import user_now_str

bp = Blueprint("achievements", __name__, url_prefix="/achievements")

# ── Points for each kind of action ──────────────────────────────
XP_RULES = [
    # (stat,          xp each, label)
    ("checkins",       10, "check-ins"),
    ("habit_ticks",     5, "habit ticks"),
    ("tasks_done",     10, "tasks finished"),
    ("goals_achieved", 50, "goals achieved"),
    ("reviews",        20, "weekly reviews"),
    ("days_closed",     5, "days closed"),
    ("badges",         25, "badges unlocked"),
]

LEVEL_TITLES = ["Beginner", "Explorer", "Builder", "Achiever", "Pro",
                "Expert", "Master", "Grandmaster", "Legend"]

# ── Every badge: (code, icon, name, description, stat, target, group) ──
ACHIEVEMENTS = [
    ("first_checkin",  "📝", "First Step",         "Do your first check-in",              "checkins",          1,  "Consistency"),
    ("checkins_7",     "🗓️", "Week Warrior",       "Log 7 check-ins",                     "checkins",          7,  "Consistency"),
    ("checkins_30",    "📅", "Monthly Master",     "Log 30 check-ins",                    "checkins",         30,  "Consistency"),
    ("checkins_100",   "💯", "Centurion",          "Log 100 check-ins",                   "checkins",        100,  "Consistency"),
    ("streak_3",       "🔥", "On Fire",            "Check in 3 days in a row",            "checkin_streak",    3,  "Consistency"),
    ("streak_7",       "⚡", "Unstoppable",        "Check in 7 days in a row",            "checkin_streak",    7,  "Consistency"),
    ("streak_30",      "🏔️", "Iron Will",          "Check in 30 days in a row",           "checkin_streak",   30,  "Consistency"),
    ("habit_streak_7", "🌱", "Habit Builder",      "Keep any habit for 7 days in a row",  "habit_streak",      7,  "Habits"),
    ("habit_streak_30","🌳", "Habit Master",       "Keep any habit for 30 days in a row", "habit_streak",     30,  "Habits"),
    ("habit_ticks_100","🎖️", "Hundred Habits",     "Tick off 100 habits in total",        "habit_ticks",     100,  "Habits"),
    ("high_score",     "🚀", "In the Zone",        "Score 90 or more in a day",           "max_score",        90,  "Daily performance"),
    ("perfect_day",    "⭐", "Perfect Day",        "Score a perfect 100",                 "max_score",       100,  "Daily performance"),
    ("deep_work",      "🧠", "Deep Work",          "Log 6+ focused hours in one day",     "max_hours",         6,  "Daily performance"),
    ("first_task",     "✅", "Getting Things Done","Finish your first task",              "tasks_done",        1,  "Productivity"),
    ("tasks_25",       "🧹", "Task Crusher",       "Finish 25 tasks",                     "tasks_done",       25,  "Productivity"),
    ("tasks_100",      "🏭", "Productivity Machine","Finish 100 tasks",                   "tasks_done",      100,  "Productivity"),
    ("first_project",  "📁", "Architect",          "Create your first project",           "projects",          1,  "Productivity"),
    ("first_goal",     "🎯", "Dreamer",            "Set your first goal",                 "goals",             1,  "Goals & growth"),
    ("goal_achieved",  "🏆", "Goal Getter",        "Achieve a goal",                      "goals_achieved",    1,  "Goals & growth"),
    ("goals_5",        "👑", "Visionary",          "Achieve 5 goals",                     "goals_achieved",    5,  "Goals & growth"),
    ("close_7",        "🌙", "Wind Down",          "Close your day 7 times",              "days_closed",       7,  "Consistency"),
    ("first_review",   "🪞", "Reflective",         "Write your first weekly review",      "reviews",           1,  "Goals & growth"),
    ("reviews_4",      "📖", "Steady Reflector",   "Write 4 weekly reviews",              "reviews",           4,  "Goals & growth"),
]
BY_CODE = {a[0]: a for a in ACHIEVEMENTS}


# ── Stats: everything badges and XP are based on ────────────────
def gather_stats(db, user_id):
    one = lambda sql: db.execute(sql, (user_id,)).fetchone()[0] or 0  # noqa: E731

    checkin_row = db.execute(
        "SELECT COUNT(*), MAX(score), MAX(focused_hours) FROM checkins WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    checkin_dates = [date.fromisoformat(r[0]) for r in db.execute(
        "SELECT log_date FROM checkins WHERE user_id = ?", (user_id,))]

    habit_days = defaultdict(list)
    for habit_id, log_date in db.execute(
        """SELECT hl.habit_id, hl.log_date FROM habit_logs hl
           JOIN habits h ON h.id = hl.habit_id WHERE h.user_id = ?""", (user_id,)):
        habit_days[habit_id].append(date.fromisoformat(log_date))

    return {
        "checkins": checkin_row[0],
        "max_score": checkin_row[1] or 0,
        "max_hours": checkin_row[2] or 0,
        "checkin_streak": best_streak(checkin_dates),
        "habit_ticks": sum(len(days) for days in habit_days.values()),
        "habit_streak": max((best_streak(days) for days in habit_days.values()), default=0),
        "tasks_done": one("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'done'"),
        "projects": one("SELECT COUNT(*) FROM projects WHERE user_id = ?"),
        "goals": one("SELECT COUNT(*) FROM goals WHERE user_id = ?"),
        "goals_achieved": one("SELECT COUNT(*) FROM goals WHERE user_id = ? AND status = 'achieved'"),
        "reviews": one("SELECT COUNT(*) FROM weekly_reviews WHERE user_id = ?"),
        "days_closed": one("SELECT COUNT(*) FROM day_closes WHERE user_id = ?"),
        "badges": one("SELECT COUNT(*) FROM user_achievements WHERE user_id = ?"),
    }


# ── Levels ──────────────────────────────────────────────────────
def xp_needed_for(level):
    """Total XP needed to reach a level: 0, 100, 300, 600, 1000, 1500..."""
    return 50 * level * (level - 1)


def level_info(xp):
    level = 1
    while xp >= xp_needed_for(level + 1):
        level += 1
    start, end = xp_needed_for(level), xp_needed_for(level + 1)
    return {
        "level": level,
        "title": LEVEL_TITLES[min(level, len(LEVEL_TITLES)) - 1],
        "xp": xp,
        "into_level": xp - start,
        "level_size": end - start,
        "to_next": end - xp,
        "percent": (xp - start) * 100 // (end - start),
    }


def player_progress(db, user_id):
    """Stats + XP + level for one user (used by the home page and trophies page)."""
    stats = gather_stats(db, user_id)
    breakdown = [(label, stats[stat], stats[stat] * points)
                 for stat, points, label in XP_RULES]
    xp = sum(total for _, _, total in breakdown)
    return {"stats": stats, "breakdown": breakdown, **level_info(xp)}


# ── Unlocking ───────────────────────────────────────────────────
def check_achievements(user_id):
    """Unlock any newly earned badges. Returns the list of new ones."""
    db = get_db()
    stats = gather_stats(db, user_id)
    have = {r[0] for r in db.execute(
        "SELECT code FROM user_achievements WHERE user_id = ?", (user_id,))}

    new = [a for a in ACHIEVEMENTS
           if a[0] not in have and stats[a[4]] >= a[5]]
    if new:
        db.executemany(
            "INSERT OR IGNORE INTO user_achievements (user_id, code, unlocked_at) VALUES (?, ?, ?)",
            [(user_id, a[0], user_now_str()) for a in new],
        )
        db.commit()
    return new


def badge_messages(new_badges):
    """One message per new badge, or a single summary if there are many."""
    if len(new_badges) > 3:
        icons = " ".join(badge[1] for badge in new_badges)
        return [f"🏆 You unlocked {len(new_badges)} achievements! {icons}"]
    return [f"{icon} Achievement unlocked: {name}!" for code, icon, name, *_ in new_badges]


def celebrate(new_badges):
    """Celebrate on the next page (normal form submit)."""
    for message in badge_messages(new_badges):
        flash(message, "achievement")


@bp.after_app_request
def unlock_after_actions(response):
    """After any successful form submit, check for new badges and celebrate."""
    if (request.method == "POST"
            and g.get("user")
            and response.status_code < 400
            and request.blueprint not in ("auth", "settings", "achievements", "reminders", "feedback", "admin")):
        new_badges = check_achievements(g.user["id"])
        if request.headers.get("HX-Request"):           # instant action: pop-up instead
            for message in badge_messages(new_badges):
                toast(message)
        else:
            celebrate(new_badges)
    return send_toasts(response)


# ── Trophies page ───────────────────────────────────────────────
@bp.route("/")
@login_required
def index():
    db = get_db()
    user_id = g.user["id"]
    celebrate(check_achievements(user_id))          # catch up on older data

    unlocked = dict(db.execute(
        "SELECT code, unlocked_at FROM user_achievements WHERE user_id = ?", (user_id,)
    ).fetchall())
    progress = player_progress(db, user_id)
    stats = progress["stats"]

    groups = defaultdict(list)
    for code, icon, name, description, stat, target, group in ACHIEVEMENTS:
        groups[group].append({
            "icon": icon, "name": name, "description": description,
            "unlocked_at": unlocked.get(code),
            "current": min(stats[stat], target), "target": target,
            "percent": min(stats[stat], target) * 100 // target,
        })

    return render_template("achievements/index.html", progress=progress,
                           groups=groups, unlocked_count=len(unlocked),
                           total=len(ACHIEVEMENTS))
