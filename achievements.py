"""Achievements, XP and levels.

XP is never stored: it's calculated from your real data (check-ins, habits,
tasks...), so it can never get out of sync. Only the moment a badge was
unlocked is stored, so badges stay earned and we know which ones are new.
"""
from collections import defaultdict
from datetime import date

from flask import Blueprint, flash, g, render_template, request

from i18n import N_, _, _n

from auth import login_required
from db import get_db
from habit_model import all_statuses, frozen_days
from streaks import best_streak
from timeutil import user_today_for
from notify import celebrate as party, send_toasts, toast
from timeutil import user_now_str

bp = Blueprint("achievements", __name__, url_prefix="/achievements")

# ── Points for each kind of action ──────────────────────────────
XP_RULES = [
    # (stat,          xp each, label)
    ("checkins",       10, N_("check-ins")),
    ("habit_ticks",     5, N_("habit ticks")),
    ("tasks_done",     10, N_("tasks finished")),
    ("goals_achieved", 50, N_("goals achieved")),
    ("reviews",        20, N_("weekly reviews")),
    ("days_closed",     5, N_("days closed")),
    ("focus_sessions",  5, N_("focus sessions")),
    ("badges",         25, N_("badges unlocked")),
]

LEVEL_TITLES = [N_("Beginner"), N_("Explorer"), N_("Builder"), N_("Achiever"), N_("Pro"),
                N_("Expert"), N_("Master"), N_("Grandmaster"), N_("Legend")]

# ── Every badge: (code, icon, name, description, stat, target, group) ──
ACHIEVEMENTS = [
    ("first_checkin",  "📝", N_("First Step"),         N_("Do your first check-in"),              "checkins",          1,  N_("Consistency")),
    ("checkins_7",     "🗓️", N_("Week Warrior"),       N_("Log 7 check-ins"),                     "checkins",          7,  N_("Consistency")),
    ("checkins_30",    "📅", N_("Monthly Master"),     N_("Log 30 check-ins"),                    "checkins",         30,  N_("Consistency")),
    ("checkins_100",   "💯", N_("Centurion"),          N_("Log 100 check-ins"),                   "checkins",        100,  N_("Consistency")),
    ("streak_3",       "🔥", N_("On Fire"),            N_("Reach a 3-day check-in streak"),            "checkin_streak",    3,  N_("Consistency")),
    ("streak_7",       "⚡", N_("Unstoppable"),        N_("Reach a 7-day check-in streak"),            "checkin_streak",    7,  N_("Consistency")),
    ("streak_30",      "🏔️", N_("Iron Will"),          N_("Reach a 30-day check-in streak"),           "checkin_streak",   30,  N_("Consistency")),
    ("habit_streak_7", "🌱", N_("Habit Builder"),      N_("Reach a 7-day streak on any habit"),  "habit_streak",      7,  N_("Habits")),
    ("habit_streak_30","🌳", N_("Habit Master"),       N_("Reach a 30-day streak on any habit"), "habit_streak",     30,  N_("Habits")),
    ("habit_ticks_100","🎖️", N_("Hundred Habits"),     N_("Tick off 100 habits in total"),        "habit_ticks",     100,  N_("Habits")),
    ("high_score",     "🚀", N_("In the Zone"),        N_("Score 90 or more in a day"),           "max_score",        90,  N_("Daily performance")),
    ("perfect_day",    "⭐", N_("Perfect Day"),        N_("Score a perfect 100"),                 "max_score",       100,  N_("Daily performance")),
    ("deep_work",      "🧠", N_("Deep Work"),          N_("Log 6+ focused hours in one day"),     "max_hours",         6,  N_("Daily performance")),
    ("first_task",     "✅", N_("Getting Things Done"),N_("Finish your first task"),              "tasks_done",        1,  N_("Productivity")),
    ("tasks_25",       "🧹", N_("Task Crusher"),       N_("Finish 25 tasks"),                     "tasks_done",       25,  N_("Productivity")),
    ("tasks_100",      "🏭", N_("Productivity Machine"),N_("Finish 100 tasks"),                   "tasks_done",      100,  N_("Productivity")),
    ("first_project",  "📁", N_("Architect"),          N_("Create your first project"),           "projects",          1,  N_("Productivity")),
    ("first_goal",     "🎯", N_("Dreamer"),            N_("Set your first goal"),                 "goals",             1,  N_("Goals & growth")),
    ("goal_achieved",  "🏆", N_("Goal Getter"),        N_("Achieve a goal"),                      "goals_achieved",    1,  N_("Goals & growth")),
    ("goals_5",        "👑", N_("Visionary"),          N_("Achieve 5 goals"),                     "goals_achieved",    5,  N_("Goals & growth")),
    ("close_7",        "🌙", N_("Wind Down"),          N_("Close your day 7 times"),              "days_closed",       7,  N_("Consistency")),
    ("first_focus",    "🍅", N_("First Focus"),        N_("Finish a 10+ minute focus session"),   "focus_sessions",    1,  N_("Productivity")),
    ("focus_25",       "🎧", N_("Deep Diver"),         N_("Finish 25 focus sessions"),            "focus_sessions",   25,  N_("Productivity")),
    ("first_review",   "🪞", N_("Reflective"),         N_("Write your first weekly review"),      "reviews",           1,  N_("Goals & growth")),
    ("reviews_4",      "📖", N_("Steady Reflector"),   N_("Write 4 weekly reviews"),              "reviews",           4,  N_("Goals & growth")),
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

    frozen = frozen_days(db, user_id)
    habits = all_statuses(db, user_id, user_today_for(db, user_id), active_only=False)

    return {
        "checkins": checkin_row[0],
        "max_score": checkin_row[1] or 0,
        "max_hours": checkin_row[2] or 0,
        "checkin_streak": best_streak(checkin_dates, frozen=frozen),
        "habit_ticks": one("""SELECT COUNT(*) FROM habit_logs l JOIN habits h ON h.id = l.habit_id
                              WHERE h.user_id = ? AND l.amount >= h.goal_count"""),
        "habit_streak": max((h["best"] for h in habits if h["schedule"] != "weekly"), default=0),
        "tasks_done": one("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'done'"),
        "projects": one("SELECT COUNT(*) FROM projects WHERE user_id = ?"),
        "goals": one("SELECT COUNT(*) FROM goals WHERE user_id = ?"),
        "goals_achieved": one("SELECT COUNT(*) FROM goals WHERE user_id = ? AND status = 'achieved'"),
        "reviews": one("SELECT COUNT(*) FROM weekly_reviews WHERE user_id = ?"),
        "days_closed": one("SELECT COUNT(*) FROM day_closes WHERE user_id = ?"),
        "focus_sessions": one("SELECT COUNT(*) FROM focus_sessions WHERE user_id = ? AND minutes >= 10"),
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
        "title": _(LEVEL_TITLES[min(level, len(LEVEL_TITLES)) - 1]),
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
        return [_n("🏆 You unlocked {n} achievement! {icons}", "🏆 You unlocked {n} achievements! {icons}",
                   len(new_badges), icons=icons)]
    return [_("{icon} Achievement unlocked: {name}!", icon=badge[1], name=_(badge[2])) for badge in new_badges]


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
            and request.blueprint not in ("auth", "settings", "achievements", "reminders", "feedback", "admin", "insights")):
        new_badges = check_achievements(g.user["id"])
        if request.headers.get("HX-Request"):           # instant action: pop-up instead
            for message in badge_messages(new_badges):
                toast(message)
        else:
            celebrate(new_badges)
        check_level_up(get_db(), g.user["id"])
    return send_toasts(response)


def check_level_up(db, user_id):
    """Show the level-up screen once for every new level (never for old ones)."""
    progress = player_progress(db, user_id)
    seen = db.execute("SELECT level_seen FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    if seen is None or progress["level"] < seen:          # first time (or XP went down): just remember
        db.execute("UPDATE users SET level_seen = ? WHERE id = ?", (progress["level"], user_id))
        db.commit()
        return None
    if progress["level"] > seen:
        db.execute("UPDATE users SET level_seen = ? WHERE id = ?", (progress["level"], user_id))
        db.commit()
        details = {"level": progress["level"], "title": progress["title"],
                   "xp": progress["xp"], "to_next": progress["to_next"],
                   "unlocks": unlocks_at(progress["level"])}
        party("levelUp", **details)
        party("confetti")
        return details
    return None


def unlocks_at(level):
    """What a new level unlocks (shown on the level-up screen)."""
    from themes import THEMES                           # (here to avoid an import loop)
    return [_("{emoji} {name} theme", emoji=t["emoji"], name=_(t["name"])) for t in THEMES.values() if t["level"] == level]


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
        groups[_(group)].append({
            "icon": icon, "name": _(name), "description": _(description),
            "unlocked_at": unlocked.get(code),
            "current": min(stats[stat], target), "target": target,
            "percent": min(stats[stat], target) * 100 // target,
        })

    return render_template("achievements/index.html", progress=progress,
                           groups=groups, unlocked_count=len(unlocked),
                           total=len(ACHIEVEMENTS))
