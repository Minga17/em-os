import sqlite3
from collections import defaultdict
from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from auth import login_required
from db import get_db

bp = Blueprint("goals", __name__, url_prefix="/goals")

STATUSES = ["active", "achieved", "paused", "dropped"]

DEFAULT_AREAS = [
    ("Career", "💼", "#4a9eff"),
    ("Health", "💪", "#22c55e"),
    ("Learning", "📚", "#a855f7"),
    ("Finance", "💰", "#f59e0b"),
    ("Relationships", "❤️", "#ef4444"),
    ("Personal", "🌱", "#06b6d4"),
]


# ── Helpers ────────────────────────────────────────────────────
def ensure_default_areas(user_id):
    """Give a new user a starter set of life areas (only if they have none)."""
    db = get_db()
    has_any = db.execute(
        "SELECT 1 FROM life_areas WHERE user_id = ? LIMIT 1", (user_id,)
    ).fetchone()
    if not has_any:
        db.executemany(
            "INSERT INTO life_areas (user_id, name, icon, color, sort_order) VALUES (?, ?, ?, ?, ?)",
            [(user_id, name, icon, color, position)
             for position, (name, icon, color) in enumerate(DEFAULT_AREAS)],
        )
        db.commit()


def user_areas():
    return get_db().execute(
        "SELECT * FROM life_areas WHERE user_id = ? ORDER BY sort_order, name",
        (g.user["id"],),
    ).fetchall()


def get_own_goal(goal_id):
    goal = get_db().execute(
        "SELECT * FROM goals WHERE id = ? AND user_id = ?", (goal_id, g.user["id"])
    ).fetchone()
    if goal is None:
        abort(404)
    return goal


def get_own_area(area_id):
    area = get_db().execute(
        "SELECT * FROM life_areas WHERE id = ? AND user_id = ?", (area_id, g.user["id"])
    ).fetchone()
    if area is None:
        abort(404)
    return area


def read_goal_form():
    """Read and check the goal form. Returns (data, errors)."""
    errors = []
    data = {
        "title": request.form.get("title", "").strip(),
        "why": request.form.get("why", "").strip(),
        "status": request.form.get("status", "active"),
        "target_date": request.form.get("target_date", "").strip() or None,
        "area_id": None,
        "progress": 0,
    }

    if not data["title"]:
        errors.append("Goal title is required.")
    if data["status"] not in STATUSES:
        errors.append("Pick a valid status.")

    try:
        data["progress"] = int(request.form.get("progress") or 0)
        if not 0 <= data["progress"] <= 100:
            raise ValueError
    except ValueError:
        errors.append("Progress must be between 0 and 100.")

    if data["target_date"]:
        try:
            date.fromisoformat(data["target_date"])
        except ValueError:
            errors.append("Target date must be a real date.")

    raw_area = request.form.get("area_id", "")
    if raw_area:
        owned = get_db().execute(
            "SELECT id FROM life_areas WHERE id = ? AND user_id = ?",
            (raw_area, g.user["id"]),
        ).fetchone()
        if owned:
            data["area_id"] = owned["id"]
        else:
            errors.append("That life area doesn't exist.")

    return data, errors


# ── Goals page ─────────────────────────────────────────────────
@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    db = get_db()
    user_id = g.user["id"]
    ensure_default_areas(user_id)

    if request.method == "POST":
        data, errors = read_goal_form()
        if errors:
            for error in errors:
                flash(error, "error")
        else:
            db.execute(
                """
                INSERT INTO goals (user_id, area_id, title, why, target_date, status, progress)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, data["area_id"], data["title"], data["why"],
                 data["target_date"], data["status"], data["progress"]),
            )
            db.commit()
            flash(f"Goal added: {data['title']} 🎯", "success")
        return redirect(url_for("goals.index"))

    # Each goal + how many tasks its linked projects have (and how many are done)
    goals = db.execute(
        """
        SELECT g.*,
               COUNT(DISTINCT p.id)              AS project_count,
               COUNT(t.id)                       AS total_tasks,
               COALESCE(SUM(t.status = 'done'), 0) AS done_tasks
        FROM goals g
        LEFT JOIN projects p ON p.goal_id = g.id
        LEFT JOIN tasks t    ON t.project_id = p.id
        WHERE g.user_id = ?
        GROUP BY g.id
        ORDER BY CASE g.status
                   WHEN 'active' THEN 1 WHEN 'paused' THEN 2
                   WHEN 'achieved' THEN 3 ELSE 4 END,
                 g.target_date IS NULL, g.target_date
        """,
        (user_id,),
    ).fetchall()

    # Linked project names, in ONE query
    projects_by_goal = defaultdict(list)
    for row in db.execute(
        "SELECT goal_id, title FROM projects WHERE user_id = ? AND goal_id IS NOT NULL ORDER BY title",
        (user_id,),
    ):
        projects_by_goal[row["goal_id"]].append(row["title"])

    today = date.today()
    goals_by_area = defaultdict(list)
    for goal in goals:
        if goal["total_tasks"]:
            percent = goal["done_tasks"] * 100 // goal["total_tasks"]
            auto = True
        else:
            percent = goal["progress"]
            auto = False

        days_left = None
        if goal["target_date"]:
            days_left = (date.fromisoformat(goal["target_date"]) - today).days

        goals_by_area[goal["area_id"]].append({
            "goal": goal,
            "percent": percent,
            "auto": auto,
            "days_left": days_left,
            "projects": projects_by_goal[goal["id"]],
        })

    areas = user_areas()
    sections = [{"area": area, "items": goals_by_area[area["id"]]} for area in areas]
    if goals_by_area[None]:
        sections.append({"area": None, "items": goals_by_area[None]})

    return render_template("goals/index.html", sections=sections, areas=areas,
                           statuses=STATUSES)


@bp.route("/<int:goal_id>/edit", methods=["GET", "POST"])
@login_required
def edit(goal_id):
    goal = get_own_goal(goal_id)

    if request.method == "POST":
        data, errors = read_goal_form()
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("goals/edit.html", goal=data, goal_id=goal_id,
                                   areas=user_areas(), statuses=STATUSES)
        db = get_db()
        db.execute(
            """
            UPDATE goals
            SET title = ?, why = ?, status = ?, target_date = ?, area_id = ?,
                progress = ?, updated_at = datetime('now')
            WHERE id = ? AND user_id = ?
            """,
            (data["title"], data["why"], data["status"], data["target_date"],
             data["area_id"], data["progress"], goal_id, g.user["id"]),
        )
        db.commit()
        if data["status"] == "achieved" and goal["status"] != "achieved":
            flash(f"🎉 Goal achieved: {data['title']}!", "success")
        else:
            flash("Goal updated.", "success")
        return redirect(url_for("goals.index"))

    return render_template("goals/edit.html", goal=goal, goal_id=goal_id,
                           areas=user_areas(), statuses=STATUSES)


@bp.route("/<int:goal_id>/delete", methods=["POST"])
@login_required
def delete(goal_id):
    goal = get_own_goal(goal_id)
    db = get_db()
    db.execute("DELETE FROM goals WHERE id = ? AND user_id = ?", (goal_id, g.user["id"]))
    db.commit()
    flash(f"Deleted goal: {goal['title']}. Linked projects were kept.", "success")
    return redirect(url_for("goals.index"))


# ── Life areas ─────────────────────────────────────────────────
@bp.route("/areas", methods=["POST"])
@login_required
def add_area():
    name = request.form.get("name", "").strip()
    icon = request.form.get("icon", "").strip() or "📁"
    if not name:
        flash("Area name is required.", "error")
    else:
        db = get_db()
        next_position = db.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM life_areas WHERE user_id = ?",
            (g.user["id"],),
        ).fetchone()[0]
        try:
            db.execute(
                "INSERT INTO life_areas (user_id, name, icon, sort_order) VALUES (?, ?, ?, ?)",
                (g.user["id"], name, icon[:4], next_position),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash(f"You already have an area called {name}.", "error")
        else:
            flash(f"Area added: {icon} {name}", "success")
    return redirect(url_for("goals.index"))


@bp.route("/areas/<int:area_id>/delete", methods=["POST"])
@login_required
def delete_area(area_id):
    area = get_own_area(area_id)
    db = get_db()
    db.execute("DELETE FROM life_areas WHERE id = ? AND user_id = ?", (area_id, g.user["id"]))
    db.commit()
    flash(f"Deleted area: {area['name']}. Its goals moved to 'No area'.", "success")
    return redirect(url_for("goals.index"))