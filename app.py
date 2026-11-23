import os
from datetime import date

from flask import Flask, g, redirect, render_template, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from achievements import bp as achievements_bp, player_progress
from admin import bp as admin_bp
from backup import backup_ready, send_backup
from feedback import bp as feedback_bp
from focus import bp as focus_bp
from records import bp as records_bp
from recap import bp as recap_bp
from insights import bp as insights_bp, insight_of_the_day
from quickadd import bp as quick_bp
from importer import bp as importer_bp
from freezes import bp as freezes_bp
from jobs import any_jobs, start_background_jobs
from auth import bp as auth_bp, login_required
from checkin import bp as checkin_bp
from dashboard import bp as dashboard_bp
from export import bp as export_bp
from goals import bp as goals_bp
from habits import bp as habits_bp
from review import bp as review_bp
from projects import bp as projects_bp
from reminders import bp as reminders_bp, push_ready, run_reminders
from tasks import bp as tasks_bp
from db import close_db, get_db, init_db
import i18n
from i18n import _, format_day
from settings import bp as settings_bp
from timeutil import user_today
from plan import bp as plan_bp, day_plan
from today import today_summary
from welcome import bp as welcome_bp, needs_welcome


def setup_error_tracking(app):
    """Sentry emails you when something breaks online. Private data is never sent."""
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn or app.testing:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            send_default_pii=False,          # no emails, IP addresses or cookies
            max_request_body_size="never",   # never send what people typed (moods, passwords...)
            include_local_variables=False,   # never send values from inside the code
            traces_sample_rate=0,            # errors only, no performance tracking
            environment="production" if os.environ.get("RENDER") else "local",
        )
    except Exception:                        # not installed: the app still works
        app.logger.warning("Sentry isn't installed, so error tracking is off.")


def create_app(test_config=None):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
    app.config["DATABASE"] = os.path.join(app.instance_path, "em_os.db")
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,      # JavaScript can't read the login cookie
        SESSION_COOKIE_SAMESITE="Lax",     # other websites can't submit forms as you
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",  # HTTPS only (online)
        # Daily reminders: keys made once with make_vapid_keys.py, kept in Render's settings
        VAPID_PUBLIC_KEY=os.environ.get("VAPID_PUBLIC_KEY", ""),
        VAPID_PRIVATE_KEY=os.environ.get("VAPID_PRIVATE_KEY", ""),
        VAPID_SUBJECT=os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com"),
        # Email (Gmail app password), the owner's address, and the backup password
        MAIL_SERVER=os.environ.get("MAIL_SERVER", "smtp.gmail.com"),
        MAIL_PORT=int(os.environ.get("MAIL_PORT", "587")),
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME", ""),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD", ""),
        ADMIN_EMAILS=os.environ.get("ADMIN_EMAILS", ""),
        BACKUP_PASSWORD=os.environ.get("BACKUP_PASSWORD", ""),
        APP_URL=os.environ.get("APP_URL", ""),          # e.g. https://em-os.onrender.com
    )
    if test_config:
        app.config.update(test_config)   # tests use their own temporary database
    os.makedirs(app.instance_path, exist_ok=True)
    setup_error_tracking(app)
    if os.environ.get("RENDER"):
        # Render sits in front of the app; trust it to tell us the real https:// address
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)          # right after auth: it needs to know who's logged in
    app.register_blueprint(freezes_bp)        # uses streak freezes once a day, if needed
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
    app.register_blueprint(reminders_bp)
    app.register_blueprint(feedback_bp)
    app.register_blueprint(welcome_bp)
    app.register_blueprint(plan_bp)
    app.register_blueprint(focus_bp)
    app.register_blueprint(records_bp)
    app.register_blueprint(recap_bp)
    app.register_blueprint(insights_bp)
    app.register_blueprint(quick_bp)
    app.register_blueprint(importer_bp)
    i18n.init_app(app)

    # Reminders and backups run in the background (only online, where they're set up)
    reloader_parent = app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
    if not app.testing and not reloader_parent and any_jobs(app):
        start_background_jobs(app)

    @app.cli.command("send-reminders")
    def send_reminders_command():
        """Send any reminders that are due right now (for testing)."""
        print(f"Reminders sent to {run_reminders(get_db())} people.")

    @app.cli.command("backup-now")
    def backup_now_command():
        """Email a backup right now (to check backups work)."""
        if not backup_ready(app):
            print("Backups aren't set up: need BACKUP_PASSWORD, ADMIN_EMAILS, email settings and pyzipper.")
            return
        print(f"Backup sent ({send_backup(date.today().isoformat()) / 1024:.0f} KB).")

    from notify import pending_celebrations
    from themes import THEMES, theme_of

    @app.context_processor
    def theme_and_celebrations():
        theme = theme_of(g.get("user"))
        # take_celebrations is only called by base.html, so instant (htmx) pieces never use them up
        return {"theme": theme, "theme_bar": THEMES[theme]["bar"],
                "take_celebrations": pending_celebrations}

    from task_logic import repeat_label
    app.add_template_filter(repeat_label, "repeat_label")

    @app.template_filter("day_label")
    def day_label(iso_date):
        """Turn '2026-11-15' into 'Today', 'Yesterday', or a weekday name."""
        d = date.fromisoformat(iso_date)
        days_ago = (user_today() - d).days
        if days_ago == 0:
            return _("Today")
        if days_ago == 1:
            return _("Yesterday")
        return format_day(d, "weekday")

    @app.route("/")
    @login_required
    def home():
        if needs_welcome(g.user):
            return redirect(url_for("welcome.index"))
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
        reminder_tip = push_ready() and db.execute(
            "SELECT reminder_time IS NULL FROM users WHERE id = ?", (user_id,)).fetchone()[0]

        return render_template(
            "home.html",
            progress=progress,
            summary=summary,
            active_goals=active_goals,
            next_goal=next_goal,
            today_checkin=today_checkin,
            recent=recent,
            task_stats=task_stats,
            reminder_tip=reminder_tip,
            plan=day_plan(db, user_id),
            insight=insight_of_the_day(db, user_id, summary["today"]),
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
