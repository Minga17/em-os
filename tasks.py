from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from auth import login_required
from db import get_db
from timeutil import user_now_str, user_today

bp = Blueprint("tasks", __name__, url_prefix="/tasks")

STATUSES = ["todo", "doing", "done"]
PRIORITIES = {1: "High", 2: "Medium", 3: "Low"}


def get_own_task(task_id):
    """Fetch a task only if it belongs to the logged-in user, otherwise 404."""
    task = get_db().execute(
        "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
        (task_id, g.user["id"]),
    ).fetchone()
    if task is None:
        abort(404)
    return task


def user_projects():
    """Projects for the dropdown (only the user's own, not complete)."""
    return get_db().execute(
        "SELECT id, title FROM projects WHERE user_id = ? AND status != 'complete' ORDER BY title",
        (g.user["id"],),
    ).fetchall()


def read_task_form():
    """Read and check the task form. Returns (data, errors)."""
    errors = []
    data = {
        "title": request.form.get("title", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "status": request.form.get("status", "todo"),
        "due_date": request.form.get("due_date", "").strip() or None,
        "project_id": None,
        "priority": 2,
    }

    if not data["title"]:
        errors.append("Task title is required.")
    if data["status"] not in STATUSES:
        errors.append("Pick a valid status.")

    try:
        data["priority"] = int(request.form.get("priority", 2))
        if data["priority"] not in PRIORITIES:
            raise ValueError
    except ValueError:
        errors.append("Pick a valid priority.")

    if data["due_date"]:
        try:
            date.fromisoformat(data["due_date"])
        except ValueError:
            errors.append("Due date must be a real date.")

    raw_project = request.form.get("project_id", "")
    if raw_project:
        # Security: only allow linking to YOUR OWN project
        owned = get_db().execute(
            "SELECT id FROM projects WHERE id = ? AND user_id = ?",
            (raw_project, g.user["id"]),
        ).fetchone()
        if owned:
            data["project_id"] = owned["id"]
        else:
            errors.append("That project doesn't exist.")

    return data, errors


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    db = get_db()
    user_id = g.user["id"]

    if request.method == "POST":
        data, errors = read_task_form()
        if errors:
            for error in errors:
                flash(error, "error")
        else:
            db.execute(
                """
                INSERT INTO tasks (user_id, project_id, title, notes, priority, due_date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, data["project_id"], data["title"], data["notes"],
                 data["priority"], data["due_date"], data["status"]),
            )
            db.commit()
            flash(f"Task added: {data['title']}", "success")
        return redirect(url_for("tasks.index"))

    # Filters from the URL, e.g. /tasks/?show=done&project=2
    show = request.args.get("show", "open")
    project_filter = request.args.get("project", "")

    sql = """
        SELECT t.*, p.title AS project_title
        FROM tasks t
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE t.user_id = ?
    """
    params = [user_id]

    if show == "open":
        sql += " AND t.status != 'done'"
    elif show == "done":
        sql += " AND t.status = 'done'"

    if project_filter == "none":
        sql += " AND t.project_id IS NULL"
    elif project_filter.isdigit():
        sql += " AND t.project_id = ?"
        params.append(int(project_filter))

    sql += """
        ORDER BY t.status = 'done',
                 t.due_date IS NULL, t.due_date,
                 t.priority
    """
    tasks = db.execute(sql, params).fetchall()

    return render_template(
        "tasks/index.html",
        tasks=tasks,
        projects=user_projects(),
        priorities=PRIORITIES,
        show=show,
        project_filter=project_filter,
        today=user_today().isoformat(),
    )


@bp.route("/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def edit(task_id):
    task = get_own_task(task_id)

    if request.method == "POST":
        data, errors = read_task_form()
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("tasks/edit.html", task=data, task_id=task_id,
                                   projects=user_projects(), priorities=PRIORITIES,
                                   statuses=STATUSES)

        completed_at = task["completed_at"]
        if data["status"] == "done" and not completed_at:
            completed_at = user_now_str()
        elif data["status"] != "done":
            completed_at = None

        db = get_db()
        db.execute(
            """
            UPDATE tasks
            SET title = ?, notes = ?, status = ?, priority = ?, due_date = ?,
                project_id = ?, completed_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (data["title"], data["notes"], data["status"], data["priority"],
             data["due_date"], data["project_id"], completed_at, task_id, g.user["id"]),
        )
        db.commit()
        flash("Task updated.", "success")
        return redirect(url_for("tasks.index"))

    return render_template("tasks/edit.html", task=task, task_id=task_id,
                           projects=user_projects(), priorities=PRIORITIES,
                           statuses=STATUSES)


@bp.route("/<int:task_id>/toggle", methods=["POST"])
@login_required
def toggle(task_id):
    task = get_own_task(task_id)
    db = get_db()
    if task["status"] == "done":
        db.execute("UPDATE tasks SET status = 'todo', completed_at = NULL WHERE id = ?", (task_id,))
    else:
        db.execute(
            "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
            (user_now_str(), task_id),
        )
    db.commit()
    return redirect(url_for("tasks.index"))


@bp.route("/<int:task_id>/delete", methods=["POST"])
@login_required
def delete(task_id):
    task = get_own_task(task_id)
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, g.user["id"]))
    db.commit()
    flash(f"Deleted task: {task['title']}", "success")
    return redirect(url_for("tasks.index"))
