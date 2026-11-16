import os
from datetime import date

from flask import Flask, g, render_template

from auth import bp as auth_bp, login_required
from checkin import bp as checkin_bp
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
        today_checkin = db.execute(
            "SELECT score FROM checkins WHERE user_id = ? AND log_date = ?",
            (user_id, date.today().isoformat()),
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
        return render_template("home.html", today_checkin=today_checkin, recent=recent)

    return app


if __name__ == "__main__":
    create_app().run(debug=True)