from datetime import timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from auth import login_required
from db import get_db
from habit_model import (DAY_NAMES, KINDS, SCHEDULES, frozen_days, load_logs, scheduled_days,
                         status, week_dots)
from notify import celebrate, toast
from timeutil import user_today
from today import today_summary

bp = Blueprint("habits", __name__, url_prefix="/habits")


def get_own_habit(habit_id):
    """Fetch a habit only if it belongs to the logged-in user, otherwise 404."""
    habit = get_db().execute(
        "SELECT * FROM habits WHERE id = ? AND user_id = ?",
        (habit_id, g.user["id"]),
    ).fetchone()
    if habit is None:
        abort(404)
    return habit


def read_habit_form(form):
    """Check the add/edit form. Returns (fields, error)."""
    name = form.get("name", "").strip()
    icon = (form.get("icon", "").strip() or "✅")[:4]
    kind = form.get("kind", "build")
    schedule = form.get("schedule", "daily")
    days = sorted({int(d) for d in form.getlist("days") if d.isdigit() and int(d) < 7})
    unit = form.get("unit", "").strip()[:20]
    try:
        per_week = int(form.get("per_week") or 3)
        goal_count = int(form.get("goal_count") or 1)
    except ValueError:
        return None, "Please use whole numbers."

    if not name:
        return None, "Habit name is required."
    if len(name) > 60:
        return None, "Keep the habit name under 60 characters."
    if kind not in KINDS or schedule not in SCHEDULES:
        return None, "Please pick from the options."
    if kind == "build" and schedule == "days" and not days:
        return None, "Pick at least one day."
    if kind == "build" and schedule == "weekly" and not 1 <= per_week <= 6:
        return None, "Pick 1 to 6 times a week (7 is every day)."
    if not 1 <= goal_count <= 100:
        return None, "The daily amount must be between 1 and 100."
    if kind == "quit":                      # quitting is simply: every day, clean or not
        schedule, days, goal_count, unit = "daily", [], 1, ""
    return {"name": name, "icon": icon, "kind": kind, "schedule": schedule,
            "days": ",".join(map(str, days)), "per_week": per_week,
            "goal_count": goal_count, "unit": unit}, None


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    db = get_db()
    user_id = g.user["id"]

    if request.method == "POST":
        fields, error = read_habit_form(request.form)
        if error:
            flash(error, "error")
        else:
            db.execute(
                """INSERT INTO habits (user_id, name, icon, kind, schedule, days, per_week, goal_count, unit)
                   VALUES (:user_id, :name, :icon, :kind, :schedule, :days, :per_week, :goal_count, :unit)""",
                {"user_id": user_id, **fields},
            )
            db.commit()
            flash(f"Habit added: {fields['icon']} {fields['name']}", "success")
        return redirect(url_for("habits.index"))

    habits = db.execute(
        "SELECT * FROM habits WHERE user_id = ? AND is_active = 1 ORDER BY created_at, id", (user_id,)
    ).fetchall()
    amounts, slips = load_logs(db, user_id)
    frozen = frozen_days(db, user_id)
    today = user_today()
    last_7 = [today - timedelta(days=i) for i in range(6, -1, -1)]

    cards = [{
        "habit": h,
        "s": status(h, amounts[h["id"]], slips[h["id"]], frozen, today),
        "week": week_dots(h, amounts[h["id"]], slips[h["id"]], frozen, last_7),
    } for h in habits]
    return render_template("habits/index.html", cards=cards, day_names=DAY_NAMES,
                           form={}, freezes=db.execute("SELECT freezes FROM users WHERE id = ?",
                                                       (user_id,)).fetchone()[0])


@bp.route("/<int:habit_id>/edit", methods=["GET", "POST"])
@login_required
def edit(habit_id):
    habit = get_own_habit(habit_id)
    if request.method == "POST":
        fields, error = read_habit_form(request.form)
        if error:
            flash(error, "error")
        else:
            db = get_db()
            db.execute("""UPDATE habits SET name = :name, icon = :icon, kind = :kind, schedule = :schedule,
                          days = :days, per_week = :per_week, goal_count = :goal_count, unit = :unit
                          WHERE id = :id""", {"id": habit_id, **fields})
            db.commit()
            flash(f"Saved: {fields['icon']} {fields['name']}", "success")
            return redirect(url_for("habits.index"))
    return render_template("habits/edit.html", habit=habit, day_names=DAY_NAMES,
                           chosen_days=scheduled_days(habit))


def respond(habit_id, message=None):
    """Tapped on Today (htmx): send back just the row and the ring. Otherwise reload the page."""
    if request.headers.get("HX-Request"):
        db = get_db()
        summary = today_summary(db, g.user["id"])
        item = next((h for h in summary["habits"] if h["id"] == habit_id), None)
        if item is not None:
            if message:
                toast(message)
                if summary["all_done"]:
                    toast("🎉 Everything done today. Amazing!")
                    celebrate("confetti")
            return render_template("today/_habit_toggle.html", item=item, summary=summary)
    if message:
        flash(message, "success")
    return redirect(url_for("habits.index"))


@bp.route("/<int:habit_id>/toggle", methods=["POST"])
@login_required
def toggle(habit_id):
    """Simple habits: done / not done. Counting habits (8 glasses): +1."""
    habit = get_own_habit(habit_id)
    if habit["kind"] == "quit":
        abort(400)
    db = get_db()
    today = user_today().isoformat()
    goal = max(habit["goal_count"], 1)
    row = db.execute("SELECT amount FROM habit_logs WHERE habit_id = ? AND log_date = ?",
                     (habit_id, today)).fetchone()
    before = row["amount"] if row else 0

    if goal == 1 and before:
        db.execute("DELETE FROM habit_logs WHERE habit_id = ? AND log_date = ?", (habit_id, today))
        after = 0
    else:
        db.execute("""INSERT INTO habit_logs (habit_id, log_date, amount) VALUES (?, ?, 1)
                      ON CONFLICT(habit_id, log_date) DO UPDATE SET amount = amount + 1""",
                   (habit_id, today))
        after = before + 1
    db.commit()

    message = None
    if before < goal <= after:
        amount = f" {after}/{goal}{' ' + habit['unit'] if habit['unit'] else ''}" if goal > 1 else ""
        message = f"+5 XP · {habit['icon']} {habit['name']}{amount}"
    return respond(habit_id, message)


@bp.route("/<int:habit_id>/minus", methods=["POST"])
@login_required
def minus(habit_id):
    """Counting habits: undo one (tapped by mistake)."""
    get_own_habit(habit_id)
    db = get_db()
    today = user_today().isoformat()
    db.execute("UPDATE habit_logs SET amount = amount - 1 WHERE habit_id = ? AND log_date = ?",
               (habit_id, today))
    db.execute("DELETE FROM habit_logs WHERE habit_id = ? AND log_date = ? AND amount <= 0",
               (habit_id, today))
    db.commit()
    return respond(habit_id)


@bp.route("/<int:habit_id>/slip", methods=["POST"])
@login_required
def slip(habit_id):
    """Quit habits: log a slip today (or take it back)."""
    habit = get_own_habit(habit_id)
    if habit["kind"] != "quit":
        abort(400)
    db = get_db()
    today = user_today().isoformat()
    removed = db.execute("DELETE FROM habit_slips WHERE habit_id = ? AND log_date = ?",
                         (habit_id, today)).rowcount
    if not removed:
        db.execute("INSERT INTO habit_slips (habit_id, log_date) VALUES (?, ?)", (habit_id, today))
    db.commit()
    message = ("Slip removed. Back on track! 💪" if removed else
               "Logged. One slip doesn't erase your progress. Your clean days start again today. 💪")
    return respond(habit_id, message)


@bp.route("/<int:habit_id>/archive", methods=["POST"])
@login_required
def archive(habit_id):
    habit = get_own_habit(habit_id)
    db = get_db()
    db.execute("UPDATE habits SET is_active = 0 WHERE id = ?", (habit_id,))
    db.commit()
    flash(f"Archived: {habit['name']}", "success")
    return redirect(url_for("habits.index"))
