from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from auth import login_required
from db import get_db
from streaks import best_streak, current_streak
from timeutil import user_today

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


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    db = get_db()
    user_id = g.user["id"]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        icon = request.form.get("icon", "").strip() or "✅"
        if not name:
            flash("Habit name is required.", "error")
        elif len(name) > 60:
            flash("Keep the habit name under 60 characters.", "error")
        else:
            db.execute(
                "INSERT INTO habits (user_id, name, icon) VALUES (?, ?, ?)",
                (user_id, name, icon[:4]),
            )
            db.commit()
            flash(f"Habit added: {icon} {name}", "success")
        return redirect(url_for("habits.index"))

    habits = db.execute(
        "SELECT * FROM habits WHERE user_id = ? AND is_active = 1 ORDER BY created_at",
        (user_id,),
    ).fetchall()

    # ONE query for all logs, instead of one query per habit
    rows = db.execute(
        """
        SELECT hl.habit_id, hl.log_date
        FROM habit_logs hl
        JOIN habits h ON h.id = hl.habit_id
        WHERE h.user_id = ?
        """,
        (user_id,),
    ).fetchall()

    done_by_habit = defaultdict(set)
    for row in rows:
        done_by_habit[row["habit_id"]].add(date.fromisoformat(row["log_date"]))

    today = user_today()
    last_7 = [today - timedelta(days=i) for i in range(6, -1, -1)]

    cards = []
    for habit in habits:
        done = done_by_habit[habit["id"]]
        cards.append({
            "habit": habit,
            "done_today": today in done,
            "current": current_streak(done, today),
            "best": best_streak(done),
            "week": [(day, day in done) for day in last_7],
        })

    return render_template("habits/index.html", cards=cards)


@bp.route("/<int:habit_id>/toggle", methods=["POST"])
@login_required
def toggle(habit_id):
    get_own_habit(habit_id)
    db = get_db()
    today = user_today().isoformat()

    already_done = db.execute(
        "SELECT 1 FROM habit_logs WHERE habit_id = ? AND log_date = ?",
        (habit_id, today),
    ).fetchone()

    if already_done:
        db.execute(
            "DELETE FROM habit_logs WHERE habit_id = ? AND log_date = ?",
            (habit_id, today),
        )
    else:
        db.execute(
            "INSERT INTO habit_logs (habit_id, log_date) VALUES (?, ?)",
            (habit_id, today),
        )
    db.commit()
    return redirect(url_for("habits.index"))


@bp.route("/<int:habit_id>/archive", methods=["POST"])
@login_required
def archive(habit_id):
    habit = get_own_habit(habit_id)
    db = get_db()
    db.execute("UPDATE habits SET is_active = 0 WHERE id = ?", (habit_id,))
    db.commit()
    flash(f"Archived: {habit['name']}", "success")
    return redirect(url_for("habits.index"))
