from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from auth import login_required
from db import get_db

bp = Blueprint("projects", __name__, url_prefix="/projects")

STATUSES = ["planned", "active", "paused", "complete"]


def get_own_project(project_id):
    """Fetch a project only if it belongs to the logged-in user, otherwise 404."""
    project = get_db().execute(
        "SELECT * FROM projects WHERE id = ? AND user_id = ?",
        (project_id, g.user["id"]),
    ).fetchone()
    if project is None:
        abort(404)
    return project


def read_project_form():
    """Read and check the project form. Returns (data, errors)."""
    data = {
        "title": request.form.get("title", "").strip(),
        "description": request.form.get("description", "").strip(),
        "status": request.form.get("status", "active"),
        "next_action": request.form.get("next_action", "").strip(),
        "github_url": request.form.get("github_url", "").strip(),
    }
    errors = []
    if not data["title"]:
        errors.append("Project title is required.")
    if data["status"] not in STATUSES:
        errors.append("Pick a valid status.")
    if data["github_url"] and not data["github_url"].startswith("https://"):
        errors.append("GitHub link must start with https://")

    data: dict = {
        "title": request.form.get("title", "").strip(),
        "description": request.form.get("description", "").strip(),
        "status": request.form.get("status", "active"),
        "next_action": request.form.get("next_action", "").strip(),
        "github_url": request.form.get("github_url", "").strip(),
        "goal_id": None
    }
    raw_goal = request.form.get("goal_id", "")
    if raw_goal:
        owned = get_db().execute(
            "SELECT id FROM goals WHERE id = ? AND user_id = ?",
            (raw_goal, g.user["id"]),
        ).fetchone()
        if owned:
            data["goal_id"] = owned["id"]
        else:
            errors.append("That goal doesn't exist.")
    return data, errors


def user_goals():
    """Goals for the dropdown (only the user's own, still in play)."""
    return get_db().execute(
        "SELECT id, title FROM goals WHERE user_id = ? AND status IN ('active', 'paused') ORDER BY title",
        (g.user["id"],),
    ).fetchall()


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    db = get_db()
    user_id = g.user["id"]

    if request.method == "POST":
        data, errors = read_project_form()
        if errors:
            for error in errors:
                flash(error, "error")
        else:
            db.execute(
                """
                INSERT INTO projects
                    (user_id, title, description, status, next_action, github_url, goal_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, data["title"], data["description"], data["status"],
                 data["next_action"], data["github_url"], data["goal_id"]),
            )
            db.commit()
            flash(f"Project added: {data['title']}", "success")
        return redirect(url_for("projects.index"))

    projects = db.execute(
        """
        SELECT p.*,
               gl.title AS goal_title,
               COUNT(t.id) AS total_tasks,
               COALESCE(SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END), 0) AS done_tasks
        FROM projects p
        LEFT JOIN tasks t  ON t.project_id = p.id
        LEFT JOIN goals gl ON gl.id = p.goal_id
        WHERE p.user_id = ?
        GROUP BY p.id
        ORDER BY CASE p.status
                   WHEN 'active' THEN 1 WHEN 'planned' THEN 2
                   WHEN 'paused' THEN 3 ELSE 4 END,
                 p.title
        """,
        (user_id,),
    ).fetchall()

    return render_template("projects/index.html", projects=projects, statuses=STATUSES,
                           goals=user_goals())


@bp.route("/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
def edit(project_id):
    project = get_own_project(project_id)

    if request.method == "POST":
        data, errors = read_project_form()
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("projects/edit.html", project=data,
                                   project_id=project_id, statuses=STATUSES,
                                   goals=user_goals())
        db = get_db()
        db.execute(
            """
            UPDATE projects
            SET title = ?, description = ?, status = ?, next_action = ?,
                github_url = ?, goal_id = ?, updated_at = datetime('now')
            WHERE id = ? AND user_id = ?
            """,
            (data["title"], data["description"], data["status"], data["next_action"],
             data["github_url"], data["goal_id"], project_id, g.user["id"]),
        )
        db.commit()
        flash("Project updated.", "success")
        return redirect(url_for("projects.index"))

    return render_template("projects/edit.html", project=project,
                           project_id=project_id, statuses=STATUSES,
                           goals=user_goals())


@bp.route("/<int:project_id>/delete", methods=["POST"])
@login_required
def delete(project_id):
    project = get_own_project(project_id)
    db = get_db()
    db.execute("DELETE FROM projects WHERE id = ? AND user_id = ?", (project_id, g.user["id"]))
    db.commit()
    flash(f"Deleted project: {project['title']}. Its tasks were kept, unassigned.", "success")
    return redirect(url_for("projects.index"))