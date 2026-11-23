from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from i18n import _, _n, N_

from auth import login_required
from db import get_db
from notify import toast
from task_logic import REPEATS, complete_task, completion_messages, reopen_task, store_repeat
from timeutil import user_now_str, user_today

bp = Blueprint("tasks", __name__, url_prefix="/tasks")

STATUSES = ["todo", "doing", "done"]
PRIORITIES = {1: N_("High"), 2: N_("Medium"), 3: N_("Low")}


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


def user_goals():
    """Active goals for the dropdown (only the user's own)."""
    return get_db().execute(
        "SELECT id, title FROM goals WHERE user_id = ? AND status != 'dropped' ORDER BY title",
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
        "goal_id": None,
        "priority": 2,
        "repeat": request.form.get("repeat", ""),
    }
    if data["repeat"] not in REPEATS:
        errors.append(_('Pick how often it repeats from the list.'))

    if not data["title"]:
        errors.append(_('Task title is required.'))
    if data["status"] not in STATUSES:
        errors.append(_('Pick a valid status.'))

    try:
        data["priority"] = int(request.form.get("priority", 2))
        if data["priority"] not in PRIORITIES:
            raise ValueError
    except ValueError:
        errors.append(_('Pick a valid priority.'))

    if data["due_date"]:
        try:
            date.fromisoformat(data["due_date"])
        except ValueError:
            errors.append(_('Due date must be a real date.'))

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
            errors.append(_("That project doesn't exist."))

    raw_goal = request.form.get("goal_id", "")
    if raw_goal:
        owned = get_db().execute(
            "SELECT id FROM goals WHERE id = ? AND user_id = ?", (raw_goal, g.user["id"])
        ).fetchone()
        if owned:
            data["goal_id"] = owned["id"]
        else:
            errors.append(_("That goal doesn't exist."))

    if not errors:
        data["repeat"] = store_repeat(data["repeat"], data["due_date"])
    return data, errors


def say(messages, htmx=False):
    for message in messages:
        toast(message) if htmx else flash(message, "success")


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
                INSERT INTO tasks (user_id, project_id, goal_id, title, notes, priority, due_date,
                                   status, repeat)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, data["project_id"], data["goal_id"], data["title"], data["notes"],
                 data["priority"], data["due_date"], data["status"], data["repeat"]),
            )
            db.commit()
            flash(_('Task added: {title}', title=data['title']), "success")
        return redirect(url_for("tasks.index"))

    # Filters from the URL, e.g. /tasks/?show=done&project=2
    show = request.args.get("show", "open")
    project_filter = request.args.get("project", "")

    sql = """
        SELECT t.*, p.title AS project_title, gl.title AS goal_title
        FROM tasks t
        LEFT JOIN projects p ON p.id = t.project_id
        LEFT JOIN goals gl ON gl.id = t.goal_id
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
        goals=user_goals(),
        repeats=REPEATS,
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
                                   projects=user_projects(), goals=user_goals(),
                                   priorities=PRIORITIES, statuses=STATUSES, repeats=REPEATS)

        db = get_db()
        was_done, now_done = task["status"] == "done", data["status"] == "done"
        db.execute(
            """
            UPDATE tasks
            SET title = ?, notes = ?, status = ?, priority = ?, due_date = ?,
                project_id = ?, goal_id = ?, repeat = ?
            WHERE id = ? AND user_id = ?
            """,
            (data["title"], data["notes"], data["status"] if was_done == now_done else task["status"],
             data["priority"], data["due_date"], data["project_id"], data["goal_id"], data["repeat"],
             task_id, g.user["id"]),
        )
        messages = []
        if now_done and not was_done:
            updated = get_own_task(task_id)
            messages = completion_messages(db, updated, complete_task(db, updated, user_today(), user_now_str()))
        elif was_done and not now_done:
            reopen_task(db, task)
            if data["status"] == "doing":
                db.execute("UPDATE tasks SET status = 'doing' WHERE id = ?", (task_id,))
        db.commit()
        flash(_('Task updated.'), "success")
        say(messages)
        return redirect(url_for("tasks.index"))

    return render_template("tasks/edit.html", task=task, task_id=task_id,
                           projects=user_projects(), goals=user_goals(),
                           priorities=PRIORITIES, statuses=STATUSES, repeats=REPEATS)


@bp.route("/<int:task_id>/toggle", methods=["POST"])
@login_required
def toggle(task_id):
    task = get_own_task(task_id)
    db = get_db()
    if task["status"] == "done":
        reopen_task(db, task)
        db.commit()
    else:
        next_repeat = complete_task(db, task, user_today(), user_now_str())
        db.commit()
        say(completion_messages(db, task, next_repeat))
    return redirect(url_for("tasks.index"))


@bp.route("/<int:task_id>/delete", methods=["POST"])
@login_required
def delete(task_id):
    task = get_own_task(task_id)
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, g.user["id"]))
    db.commit()
    flash(_('Deleted task: {title}', title=task['title']), "success")
    return redirect(url_for("tasks.index"))
