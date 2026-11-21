"""Welcome screen for new people: pick a starter template, get going in under a minute.

Nobody lands on an empty app. They choose what they're working on, keep the
habits they like, and EM OS fills in their habits and a first goal.
"""
from datetime import timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from auth import login_required
from db import get_db
from timeutil import user_today

bp = Blueprint("welcome", __name__, url_prefix="/welcome")

# key: (icon, name, description, habits [(icon, name)], goal (area icon, area, title, why, days))
TEMPLATES = {
    "student": ("🎓", "Student", "Classes, studying and staying on top of deadlines", [
        ("📚", "Study for 1 hour"),
        ("📖", "Read 20 minutes"),
        ("📝", "Review today's notes"),
        ("😴", "In bed by 11 PM"),
    ], ("🎓", "Education", "Finish this semester strong", "Good grades open doors", 90)),
    "code": ("💻", "Learning to code", "Daily practice until it clicks", [
        ("💻", "Code for 30 minutes"),
        ("🧩", "Solve one coding problem"),
        ("📝", "Write down what I learned"),
        ("📖", "Read docs or a tutorial"),
    ], ("💼", "Career", "Build and share my first project", "Show what I can do", 60)),
    "fitness": ("💪", "Fitness & health", "Move more, eat better, sleep well", [
        ("🏃", "Exercise 30 minutes"),
        ("💧", "Drink 8 glasses of water"),
        ("🥗", "Eat one healthy meal"),
        ("😴", "Sleep 7+ hours"),
    ], ("❤️", "Health", "Feel stronger in 90 days", "More energy every day", 90)),
    "routine": ("🌱", "Better routines", "Calmer mornings, focused days", [
        ("⏰", "Wake up on time"),
        ("🧘", "5 minutes of quiet"),
        ("📵", "No phone the first hour"),
        ("📓", "Write 3 lines in a journal"),
    ], ("🌱", "Personal", "Build a calm daily routine", "Feel in control of my day", 30)),
}


def needs_welcome(user):
    return user is not None and not user["onboarded"]


def finish_onboarding(db, user_id):
    db.execute("UPDATE users SET onboarded = 1 WHERE id = ?", (user_id,))
    db.commit()


@bp.route("/", methods=["GET"])
@login_required
def index():
    key = request.args.get("template")
    if key and key not in TEMPLATES:
        abort(404)
    return render_template("welcome/index.html", templates=TEMPLATES,
                           chosen=key, template=TEMPLATES.get(key))


@bp.route("/", methods=["POST"])
@login_required
def start():
    key = request.form.get("template", "")
    if key not in TEMPLATES:
        flash("Please pick one of the starter plans.", "error")
        return redirect(url_for("welcome.index"))
    _, _, _, habits, goal = TEMPLATES[key]
    picked = {int(i) for i in request.form.getlist("habit") if i.isdigit()}
    db = get_db()
    user_id = g.user["id"]

    for index, (icon, name) in enumerate(habits):
        if index in picked:
            db.execute("INSERT INTO habits (user_id, name, icon) VALUES (?, ?, ?)", (user_id, name, icon))

    if request.form.get("goal"):
        area_icon, area_name, title, why, days = goal
        db.execute("INSERT OR IGNORE INTO life_areas (user_id, name, icon) VALUES (?, ?, ?)",
                   (user_id, area_name, area_icon))
        area_id = db.execute("SELECT id FROM life_areas WHERE user_id = ? AND name = ?",
                             (user_id, area_name)).fetchone()[0]
        db.execute("INSERT INTO goals (user_id, area_id, title, why, target_date) VALUES (?, ?, ?, ?, ?)",
                   (user_id, area_id, title, why, (user_today() + timedelta(days=days)).isoformat()))

    finish_onboarding(db, user_id)
    if picked:
        flash("🎉 You're all set! Tap a habit below to earn your first XP.", "success")
    else:
        flash("🎉 You're all set! Add a habit whenever you're ready.", "success")
    return redirect(url_for("home"))


@bp.route("/skip", methods=["POST"])
@login_required
def skip():
    finish_onboarding(get_db(), g.user["id"])
    return redirect(url_for("home"))
