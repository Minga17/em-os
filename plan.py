"""The daily ritual: plan your top 3 in the morning, close your day in the evening.

Top 3: up to three tasks picked for today (tasks.focus_date = today).
Close your day: after 6 PM, see how the day went, move unfinished top-3
tasks to tomorrow, and close the day (+5 XP).
"""
from datetime import timedelta

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from auth import login_required
from db import get_db
from notify import celebrate, toast
from timeutil import user_now, user_now_str, user_today
from task_logic import complete_task, completion_messages, reopen_task
from today import today_summary

bp = Blueprint("plan", __name__, url_prefix="/plan")

TOP_N = 3
EVENING_HOUR = 18       # "Close your day" shows from 6 PM on the person's own clock


def day_plan(db, user_id):
    """Everything the top-3 card and the close-your-day card need."""
    today = user_today()
    today_str = today.isoformat()
    top = db.execute(
        """SELECT id, title, status = 'done' AS done FROM tasks
           WHERE user_id = ? AND focus_date = ? ORDER BY id""", (user_id, today_str)).fetchall()
    suggestions = []
    if len(top) < TOP_N:
        suggestions = db.execute(
            """SELECT id, title, due_date, priority FROM tasks
               WHERE user_id = ? AND status != 'done' AND (focus_date IS NULL OR focus_date < ?)  -- not today's or tomorrow's
               ORDER BY due_date IS NULL, due_date, priority, id LIMIT 5""",
            (user_id, today_str)).fetchall()
    closed = db.execute("SELECT 1 FROM day_closes WHERE user_id = ? AND day = ?",
                        (user_id, today_str)).fetchone() is not None
    tomorrow = db.execute(
        "SELECT title FROM tasks WHERE user_id = ? AND focus_date = ? AND status != 'done' ORDER BY id",
        (user_id, (today + timedelta(days=1)).isoformat())).fetchall()
    hour = user_now().hour
    return {
        "top": top,
        "top_done": sum(t["done"] for t in top),
        "suggestions": suggestions,
        "can_add": len(top) < TOP_N,
        "morning": hour < 12,
        "evening": hour >= EVENING_HOUR,
        "closed": closed,
        "tomorrow": tomorrow,
        "today": today_str,
        "sunday": today.weekday() == 6,
    }


def own_task(task_id):
    task = get_db().execute("SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                            (task_id, g.user["id"])).fetchone()
    if task is None:
        abort(404)
    return task


def top_count(db):
    return db.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND focus_date = ?",
                      (g.user["id"], user_today().isoformat())).fetchone()[0]


def respond():
    """Instant tap: send back the updated cards. Otherwise go back to Today."""
    if request.headers.get("HX-Request"):
        db = get_db()
        return render_template("today/_plan.html", plan=day_plan(db, g.user["id"]),
                               summary=today_summary(db, g.user["id"]))
    return redirect(url_for("home"))


# ── Morning: pick today's top 3 ─────────────────────────────────
@bp.route("/add", methods=["POST"])
@login_required
def add():
    title = request.form.get("title", "").strip()[:200]
    db = get_db()
    if not title:
        toast("Type what you want to get done first.")
    elif top_count(db) >= TOP_N:
        toast(f"Your top {TOP_N} is full. Finish or remove one first.")
    else:
        db.execute("INSERT INTO tasks (user_id, title, focus_date) VALUES (?, ?, ?)",
                   (g.user["id"], title, user_today().isoformat()))
        db.commit()
    return respond()


@bp.route("/<int:task_id>/pick", methods=["POST"])
@login_required
def pick(task_id):
    task = own_task(task_id)
    db = get_db()
    if task["status"] != "done" and top_count(db) < TOP_N:
        db.execute("UPDATE tasks SET focus_date = ? WHERE id = ?", (user_today().isoformat(), task_id))
        db.commit()
    return respond()


@bp.route("/<int:task_id>/unpick", methods=["POST"])
@login_required
def unpick(task_id):
    own_task(task_id)
    db = get_db()
    db.execute("UPDATE tasks SET focus_date = NULL WHERE id = ?", (task_id,))
    db.commit()
    return respond()


@bp.route("/<int:task_id>/toggle", methods=["POST"])
@login_required
def toggle(task_id):
    task = own_task(task_id)
    db = get_db()
    if task["status"] == "done":
        reopen_task(db, task)
    else:
        next_repeat = complete_task(db, task, user_today(), user_now_str())
        toast(f"+10 XP · ✅ {task['title']}")
        for message in completion_messages(db, task, next_repeat):
            toast(message)
    db.commit()
    plan = day_plan(db, g.user["id"])
    if task["status"] != "done" and plan["top"] and plan["top_done"] == len(plan["top"]):
        toast(f"🎯 Top {len(plan['top'])} done! Great day.")
    return respond()


# ── Evening: close your day ─────────────────────────────────────
@bp.route("/close", methods=["POST"])
@login_required
def close():
    db = get_db()
    today = user_today()
    if request.form.get("carry"):
        db.execute("""UPDATE tasks SET focus_date = ?
                      WHERE user_id = ? AND focus_date = ? AND status != 'done'""",
                   ((today + timedelta(days=1)).isoformat(), g.user["id"], today.isoformat()))
    added = db.execute("INSERT OR IGNORE INTO day_closes (user_id, day) VALUES (?, ?)",
                       (g.user["id"], today.isoformat())).rowcount
    db.commit()
    if added:
        toast("🌙 Day closed. +5 XP. Rest well!")
        celebrate("confetti")
    return respond()
