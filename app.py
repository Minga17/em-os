import os
from datetime import date

from flask import Flask, g, render_template

from achievements import bp as achievements_bp, player_progress
from auth import bp as auth_bp, login_required
from checkin import bp as checkin_bp
from dashboard import bp as dashboard_bp
from export import bp as export_bp
from goals import bp as goals_bp
from habits import bp as habits_bp
from review import bp as review_bp
from projects import bp as projects_bp
from tasks import bp as tasks_bp
from db import close_db, get_db, init_db
from settings import bp as settings_bp
from timeutil import user_today
from today import today_summary


def create_app(test_config=None):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
    app.config["DATABASE"] = os.path.join(app.instance_path, "em_os.db")
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,      # JavaScript can't read the login cookie
        SESSION_COOKIE_SAMESITE="Lax",     # other websites can't submit forms as you
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",  # HTTPS only (online)
    )
    if test_config:
        app.config.update(test_config)   # tests use their own temporary database
    os.makedirs(app.instance_path, exist_ok=True)

    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    app.register_blueprint(auth_bp)
    app.register_blueprint(checkin_bp)
    app.register_blueprint(habits_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(goals_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(achievements_bp)

    @app.template_filter("day_label")
    def day_label(iso_date):
        """Turn '2026-11-15' into 'Today', 'Yesterday', or a weekday name."""
        d = date.fromisoformat(iso_date)
        days_ago = (user_today() - d).days
        if days_ago == 0:
            return "Today"
        if days_ago == 1:
            return "Yesterday"
        return d.strftime("%A")

    @app.route("/")
    @login_required
    def home():
        db = get_db()
        user_id = g.user["id"]
        today = user_today().isoformat()

        today_checkin = db.execute(
            "SELECT score FROM checkins WHERE user_id = ? AND log_date = ?",
            (user_id, today),
        ).fetchone()

        recent = db.execute(
            """
            SELECT log_date, energy, mood, focus, focused_hours, score
            FROM checkins
            WHERE user_id = ?
            ORDER BY log_date DESC
            LIMIT 7
            """,
            (user_id,),
        ).fetchall()

        task_stats = db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN due_date <  ? THEN 1 ELSE 0 END), 0) AS overdue,
                COALESCE(SUM(CASE WHEN due_date =  ? THEN 1 ELSE 0 END), 0) AS due_today,
                COUNT(*) AS open_tasks
            FROM tasks
            WHERE user_id = ? AND status != 'done'
            """,
            (today, today, user_id),
        ).fetchone()

        active_goals = db.execute(
            "SELECT COUNT(*) FROM goals WHERE user_id = ? AND status = 'active'",
            (user_id,),
        ).fetchone()[0]

        next_goal = db.execute(
            """
            SELECT title, target_date FROM goals
            WHERE user_id = ? AND status = 'active' AND target_date IS NOT NULL
            ORDER BY target_date
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        progress = player_progress(db, user_id)
        summary = today_summary(db, user_id)

        return render_template(
            "home.html",
            progress=progress,
            summary=summary,
            active_goals=active_goals,
            next_goal=next_goal,
            today_checkin=today_checkin,
            recent=recent,
            task_stats=task_stats,
        )

    # ── App install (PWA) files: must be served from the site root ──
    @app.route("/sw.js")
    def service_worker():
        response = app.send_static_file("sw.js")
        response.headers["Content-Type"] = "application/javascript"
        response.headers["Cache-Control"] = "no-cache"   # phones always check for a new version
        return response

    @app.route("/manifest.webmanifest")
    def manifest():
        response = app.send_static_file("manifest.webmanifest")
        response.headers["Content-Type"] = "application/manifest+json"
        return response

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
