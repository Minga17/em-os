import csv
import io

from flask import Blueprint, Response, abort, g, render_template

from i18n import N_

from auth import login_required
from db import get_db
from timeutil import user_today

bp = Blueprint("export", __name__, url_prefix="/export")

# Only these datasets can be exported. Each one: (description, SQL).
# Every query is limited to the logged-in user with  WHERE ... user_id = ?
DATASETS = {
    "checkins": (
        N_("Daily check-ins: ratings, hours, score, notes"),
        """
        SELECT log_date, energy, mood, focus, focused_hours, score,
               accomplished, wins, blockers, priorities
        FROM checkins
        WHERE user_id = ?
        ORDER BY log_date
        """,
    ),
    "habits": (
        N_("Every habit tick, one row per habit per day (amount = how many that day)"),
        """
        SELECT hl.log_date, h.name AS habit, h.icon, hl.amount,
               CASE WHEN h.is_active = 1 THEN 'active' ELSE 'archived' END AS habit_status
        FROM habit_logs hl
        JOIN habits h ON h.id = hl.habit_id
        WHERE h.user_id = ?
        ORDER BY hl.log_date, h.name
        """,
    ),
    "tasks": (
        N_("All tasks with project, priority, dates and status"),
        """
        SELECT t.title, p.title AS project, t.status, t.priority,
               t.due_date, date(t.created_at) AS created, date(t.completed_at) AS completed
        FROM tasks t
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE t.user_id = ?
        ORDER BY t.created_at
        """,
    ),
    "goals": (
        N_("Goals with life area, status, progress and target date"),
        """
        SELECT gl.title, la.name AS life_area, gl.status, gl.progress,
               gl.target_date, gl.why
        FROM goals gl
        LEFT JOIN life_areas la ON la.id = gl.area_id
        WHERE gl.user_id = ?
        ORDER BY gl.created_at
        """,
    ),
    "weekly_reviews": (
        N_("Your weekly reflections"),
        """
        SELECT week_start, rating, went_well, challenges, lessons, next_focus
        FROM weekly_reviews
        WHERE user_id = ?
        ORDER BY week_start
        """,
    ),
}


def safe_cell(value):
    """Stop spreadsheet 'formula injection': text starting with = + - @ runs as a formula in Excel."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


@bp.route("/")
@login_required
def index():
    db = get_db()
    counts = {}
    for name, (_, sql) in DATASETS.items():
        counts[name] = db.execute(f"SELECT COUNT(*) FROM ({sql})", (g.user["id"],)).fetchone()[0]
    return render_template("export/index.html", datasets=DATASETS, counts=counts)


@bp.route("/<name>.csv")
@login_required
def download(name):
    if name not in DATASETS:          # whitelist: unknown names never reach the database
        abort(404)
    _, sql = DATASETS[name]
    cursor = get_db().execute(sql, (g.user["id"],))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([column[0] for column in cursor.description])   # header row
    for row in cursor:
        writer.writerow([safe_cell(value) for value in row])

    filename = f"em-os-{name}-{user_today().isoformat()}.csv"
    return Response(
        "﻿" + buffer.getvalue(),            # BOM so Excel reads emoji/accents correctly
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
