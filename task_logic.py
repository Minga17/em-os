"""Finishing and reopening tasks, in one place (Tasks page, Today's top 3, edit form).

Repeating tasks: when you finish one, the next one is created right away
with its new due date. Undo the finish and that next one disappears again.
"""
import calendar
from datetime import date, timedelta

REPEATS = {
    "": "Doesn't repeat",
    "daily": "Every day",
    "weekdays": "Every weekday (Mon–Fri)",
    "weekly": "Every week",
    "monthly": "Every month",
}


def repeat_label(repeat):
    return REPEATS.get((repeat or "").split(":")[0], "")


def store_repeat(repeat, due_date):
    """Monthly tasks remember their day (e.g. 31), so they don't drift after February."""
    if repeat == "monthly":
        day = date.fromisoformat(due_date).day if due_date else date.today().day
        return f"monthly:{day}"
    return repeat


def step(day, repeat):
    kind, _, anchor = repeat.partition(":")
    if kind == "daily":
        return day + timedelta(days=1)
    if kind == "weekdays":
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day
    if kind == "weekly":
        return day + timedelta(days=7)
    if kind == "monthly":
        year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
        want = int(anchor) if anchor.isdigit() else day.day
        return date(year, month, min(want, calendar.monthrange(year, month)[1]))
    raise ValueError(repeat)


def next_due(due_date, repeat, today):
    """The next due date after finishing: always in the future, even if you were late."""
    day = step(date.fromisoformat(due_date) if due_date else today, repeat)
    while day <= today:
        day = step(day, repeat)
    return day


def complete_task(db, task, today, now_str):
    """Mark done. Returns the next repeat as (title, due date), or None."""
    db.execute("UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?", (now_str, task["id"]))
    if not task["repeat"] or task["next_id"]:
        return None
    due = next_due(task["due_date"], task["repeat"], today)
    next_id = db.execute(
        """INSERT INTO tasks (user_id, project_id, goal_id, title, notes, priority, due_date, repeat)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (task["user_id"], task["project_id"], task["goal_id"], task["title"], task["notes"],
         task["priority"], due.isoformat(), task["repeat"]),
    ).lastrowid
    db.execute("UPDATE tasks SET next_id = ? WHERE id = ?", (next_id, task["id"]))
    return task["title"], due


def reopen_task(db, task):
    """Undo a finish. If it had created the next repeat (still untouched), remove that."""
    db.execute("UPDATE tasks SET status = 'todo', completed_at = NULL, next_id = NULL WHERE id = ?",
               (task["id"],))
    if task["next_id"]:
        db.execute("DELETE FROM tasks WHERE id = ? AND status = 'todo' AND user_id = ?",
                   (task["next_id"], task["user_id"]))


def finished_goals(db, task):
    """Goals that just reached 100% because of this task (active ones only)."""
    goal_ids = {task["goal_id"]}
    if task["project_id"]:
        row = db.execute("SELECT goal_id FROM projects WHERE id = ?", (task["project_id"],)).fetchone()
        goal_ids.add(row["goal_id"] if row else None)
    done = []
    for goal_id in goal_ids - {None}:
        goal = db.execute("SELECT * FROM goals WHERE id = ? AND status = 'active'", (goal_id,)).fetchone()
        if goal:
            total, finished = goal_task_counts(db, goal_id)
            if total and total == finished:
                done.append(goal["title"])
    return done


GOAL_TASKS = """
    SELECT t.* FROM tasks t LEFT JOIN projects p ON p.id = t.project_id
    WHERE (t.goal_id = :goal OR (t.goal_id IS NULL AND p.goal_id = :goal)) AND t.repeat = ''
"""


def goal_task_counts(db, goal_id):
    """(total, done) one-off tasks for a goal: linked directly or through its projects.
    Repeating tasks never end, so they don't count toward 100%."""
    row = db.execute(f"SELECT COUNT(*), COALESCE(SUM(status = 'done'), 0) FROM ({GOAL_TASKS})",
                     {"goal": goal_id}).fetchone()
    return row[0], row[1]


def describe_next(next_repeat):
    title, due = next_repeat
    return f"🔁 Next one: {title} on {due.strftime('%a %b')} {due.day}"


def completion_messages(db, task, next_repeat):
    """What to tell the person after finishing a task."""
    messages = []
    if next_repeat:
        messages.append(describe_next(next_repeat))
    for title in finished_goals(db, task):
        messages.append(f"🎯 Every task for “{title}” is done! Mark the goal achieved on the Goals page.")
    return messages
