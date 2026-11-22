"""What a habit is, and how it's doing. Every page asks this module, so they always agree.

A habit has:
- kind:     "build" (do it) or "quit" (stop doing it: we count days clean)
- schedule: "daily", "days" (certain weekdays only) or "weekly" (N times a week)
- goal_count + unit: e.g. 8 "glasses" a day. 1 means a simple tick.
"""
import math
from collections import defaultdict
from datetime import date, timedelta

from streaks import Streak, clean_streak, day_streak, week_start, week_streak

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SCHEDULES = ("daily", "days", "weekly")
KINDS = ("build", "quit")


def scheduled_days(habit):
    """Weekdays a 'days' habit is on, as numbers (Monday = 0)."""
    return {int(d) for d in (habit["days"] or "").split(",") if d.strip().isdigit() and int(d) < 7}


def is_scheduled(habit, day):
    if habit["schedule"] == "days":
        return day.weekday() in scheduled_days(habit)
    return True


def schedule_text(habit):
    if habit["kind"] == "quit":
        return "Quit"
    if habit["schedule"] == "days":
        days = sorted(scheduled_days(habit))
        if days == [0, 1, 2, 3, 4]:
            return "Weekdays"
        if days == [5, 6]:
            return "Weekends"
        return " ".join(DAY_NAMES[d] for d in days)
    if habit["schedule"] == "weekly":
        return f"{habit['per_week']}× a week"
    return "Every day"


def load_logs(db, user_id, active_only=True):
    """{habit_id: {date: amount}} and {habit_id: {slip dates}}, in 2 queries."""
    active = "AND h.is_active = 1" if active_only else ""
    amounts = defaultdict(dict)
    for habit_id, log_date, amount in db.execute(
            f"""SELECT l.habit_id, l.log_date, l.amount FROM habit_logs l
                JOIN habits h ON h.id = l.habit_id WHERE h.user_id = ? {active}""", (user_id,)):
        amounts[habit_id][date.fromisoformat(log_date)] = amount
    slips = defaultdict(set)
    for habit_id, log_date in db.execute(
            f"""SELECT s.habit_id, s.log_date FROM habit_slips s
                JOIN habits h ON h.id = s.habit_id WHERE h.user_id = ? {active}""", (user_id,)):
        slips[habit_id].add(date.fromisoformat(log_date))
    return amounts, slips


def frozen_days(db, user_id):
    return {date.fromisoformat(r[0]) for r in db.execute(
        "SELECT day FROM frozen_days WHERE user_id = ?", (user_id,))}


def status(habit, amounts, slips, frozen, today):
    """Everything worth knowing about one habit today."""
    goal = max(habit["goal_count"] or 1, 1)
    done_days = {d for d, n in amounts.items() if n >= goal}
    amount_today = amounts.get(today, 0)
    s = {
        "id": habit["id"], "name": habit["name"], "icon": habit["icon"],
        "kind": habit["kind"], "schedule": habit["schedule"],
        "schedule_text": schedule_text(habit),
        "goal": goal, "unit": habit["unit"] or "", "amount": amount_today,
        "done": amount_today >= goal,
        "counter": goal > 1,
        "due": True,           # does it belong on Today's list?
        "counts_done": False,  # does it count as done for the ring?
        "slipped": today in slips,
        "week_done": 0, "per_week": habit["per_week"],
    }

    if habit["kind"] == "quit":
        start = date.fromisoformat(str(habit["created_at"])[:10])
        streak = clean_streak(min(start, today), slips, today)
        s.update(due=False, done=not s["slipped"])
        if s["slipped"]:
            label = "Fresh start today 💪"
        elif streak.current == 0:
            label = "Day one. You've got this 💪"
        else:
            label = f"{streak.current} day{'s' if streak.current != 1 else ''} clean"
    elif habit["schedule"] == "weekly":
        streak = week_streak(done_days, today, habit["per_week"])
        this_week = week_start(today)
        s["week_done"] = sum(1 for d in done_days if this_week <= d <= today)
        week_met = s["week_done"] >= habit["per_week"]
        s["counts_done"] = s["done"] or week_met
        label = f"{s['week_done']}/{habit['per_week']} this week"
        if week_met:
            label = f"✅ {label}"
        if streak.current:
            label += f" · 🔥 {streak.current}-week streak"
    else:
        streak = day_streak(done_days, today, frozen,
                            scheduled=(lambda d: is_scheduled(habit, d)))
        s["due"] = is_scheduled(habit, today)
        s["counts_done"] = s["done"]
        label = f"🔥 {streak.current}-day streak" if streak.current else "Start a streak today"
        if streak.at_risk:
            label = f"⚠️ {streak.current}-day streak: missed last time, don't miss twice"

    s.update(streak=streak.current, best=streak.best, at_risk=streak.at_risk, label=label)
    if s["counter"] and habit["kind"] == "build":
        s["progress"] = f"{amount_today}/{goal}{(' ' + s['unit']) if s['unit'] else ''}"
    return s


def all_statuses(db, user_id, today, active_only=True):
    habits = db.execute(
        f"""SELECT * FROM habits WHERE user_id = ? {'AND is_active = 1' if active_only else ''}
            ORDER BY created_at, id""", (user_id,)).fetchall()
    amounts, slips = load_logs(db, user_id, active_only)
    frozen = frozen_days(db, user_id)
    return [status(h, amounts[h["id"]], slips[h["id"]], frozen, today) for h in habits]


def week_dots(habit, amounts, slips, frozen, days):
    """One dot per day for the Habits page: on / off (day off) / frozen / slip / none."""
    goal = max(habit["goal_count"] or 1, 1)
    start = date.fromisoformat(str(habit["created_at"])[:10])
    dots = []
    for day in days:
        if habit["kind"] == "quit":
            state = "slip" if day in slips else ("on" if day >= start else "")
        elif amounts.get(day, 0) >= goal:
            state = "on"
        elif amounts.get(day, 0) > 0:
            state = "part"
        elif day in frozen and habit["schedule"] != "weekly":
            state = "frozen"
        elif not is_scheduled(habit, day):
            state = "off"
        else:
            state = ""
        dots.append((day, state))
    return dots


def completion(habit, amounts, slips, start, end):
    """(done, possible) between two dates, for the dashboard and weekly review."""
    created = date.fromisoformat(str(habit["created_at"])[:10])
    start = max(start, created)
    if start > end:
        return 0, 0
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    goal = max(habit["goal_count"] or 1, 1)
    if habit["kind"] == "quit":
        return sum(1 for d in days if d not in slips), len(days)
    done = sum(1 for d in days if amounts.get(d, 0) >= goal)
    if habit["schedule"] == "weekly":
        possible = max(1, math.ceil(habit["per_week"] * len(days) / 7))
    else:
        possible = sum(1 for d in days if is_scheduled(habit, d))
    return min(done, possible), possible


def completion_rates(db, user_id, start, end):
    """Each active habit's done / possible between two dates (dashboard, weekly review)."""
    habits = db.execute("SELECT * FROM habits WHERE user_id = ? AND is_active = 1 ORDER BY created_at, id",
                        (user_id,)).fetchall()
    amounts, slips = load_logs(db, user_id)
    rows = []
    for h in habits:
        done, possible = completion(h, amounts[h["id"]], slips[h["id"]], start, end)
        rows.append({"name": h["name"], "icon": h["icon"], "kind": h["kind"], "done": done,
                     "possible": possible, "rate": round(done * 100 / possible) if possible else 0})
    return rows
