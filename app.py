import os
from datetime import date

from flask import Flask, g, render_template
from auth import bp as auth_bp, login_required
from checkin import bp as checkin_bp
from habits import bp as habits_bp
from projects import bp as projects_bp
from tasks import bp as tasks_bp
from goals import bp as goals_bp
from db import close_db, get_db, init_db


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
    app.config["DATABASE"] = os.path.join(app.instance_path, "em_os.db")
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

    @app.template_filter("day_label")
    def day_label(iso_date):
        """Turn '2026-11-15' into 'Today', 'Yesterday', or a weekday name."""
        d = date.fromisoformat(iso_date)
        days_ago = (date.today() - d).days
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
        today = date.today().isoformat()

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

        habit_stats = db.execute(
            """
            SELECT COUNT(h.id) AS total, COUNT(hl.habit_id) AS done
            FROM habits h
            LEFT JOIN habit_logs hl
                   ON hl.habit_id = h.id AND hl.log_date = ?
            WHERE h.user_id = ? AND h.is_active = 1
            """,
            (today, user_id),
        ).fetchone()

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

        return render_template(
            "home.html",
            today_checkin=today_checkin,
            recent=recent,
            habit_stats=habit_stats,
            task_stats=task_stats,
            active_goals=active_goals,
            next_goal=next_goal,
        )

    return app


if __name__ == "__main__":
    create_app().run(debug=True)