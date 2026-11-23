from flask import (Blueprint, current_app, flash, g, redirect, render_template, request,
                   session, url_for)
from i18n import LANGUAGES, _, browser_language, force_language
from werkzeug.security import check_password_hash, generate_password_hash

from auth import login_required, sign_out_other_devices
from db import get_db
from reminders import MORNING_TIMES, REMINDER_TIMES, push_ready, time_label
from themes import THEMES, theme_of, unlocked
from timeutil import all_timezones, is_valid_timezone, user_today

bp = Blueprint("settings", __name__, url_prefix="/settings")


def current_password_ok(password):
    """Check a typed password against the logged-in user's stored hash."""
    row = get_db().execute(
        "SELECT password_hash FROM users WHERE id = ?", (g.user["id"],)
    ).fetchone()
    return row is not None and check_password_hash(row["password_hash"], password)


@bp.route("/", methods=["GET"])
@login_required
def index():
    db = get_db()
    reminder_time, morning_time = db.execute(
        "SELECT reminder_time, morning_time FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    devices = db.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?",
                         (g.user["id"],)).fetchone()[0]
    from achievements import player_progress           # (here to avoid an import loop)
    level = player_progress(db, g.user["id"])["level"]
    return render_template(
        "settings/index.html", timezones=all_timezones(), local_today=user_today(),
        themes=THEMES, current_theme=theme_of(g.user), level=level,
        reminder_time=reminder_time, devices=devices,
        reminder_choices=[(t, time_label(t)) for t in REMINDER_TIMES],
        morning_time=morning_time,
        morning_choices=[(t, time_label(t)) for t in MORNING_TIMES],
        vapid_public_key=current_app.config["VAPID_PUBLIC_KEY"] if push_ready() else None,
    )


@bp.route("/profile", methods=["POST"])
@login_required
def profile():
    name = request.form.get("name", "").strip()
    timezone = request.form.get("timezone", "")
    if not name:
        flash(_("Name can't be empty."), "error")
    elif not is_valid_timezone(timezone):
        flash(_('Please pick a valid time zone.'), "error")
    else:
        db = get_db()
        db.execute("UPDATE users SET name = ?, timezone = ? WHERE id = ?",
                   (name, timezone, g.user["id"]))
        db.commit()
        flash(_('Profile saved.'), "success")
    return redirect(url_for("settings.index"))


@bp.route("/password", methods=["POST"])
@login_required
def password():
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    if not current_password_ok(current):
        flash(_('Your current password is incorrect.'), "error")
    elif len(new) < 8:
        flash(_('New password must be at least 8 characters.'), "error")
    elif new != confirm:
        flash(_("The new passwords don't match."), "error")
    else:
        db = get_db()
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (generate_password_hash(new), g.user["id"]))
        db.commit()
        sign_out_other_devices(g.user["id"])
        flash(_('Password changed. 🔒 Any other devices have been logged out.'), "success")
    return redirect(url_for("settings.index"))


@bp.route("/language", methods=["POST"])
@login_required
def language():
    choice = request.form.get("language", "")
    if choice not in LANGUAGES and choice != "":
        flash(_("Please pick a language from the list."), "error")
        return redirect(url_for("settings.index") + "#language")
    db = get_db()
    db.execute("UPDATE users SET language = ? WHERE id = ?", (choice or None, g.user["id"]))
    db.commit()
    with force_language(choice or browser_language()):
        flash(_("Language saved."), "success")
    return redirect(url_for("settings.index") + "#language")


@bp.route("/theme", methods=["POST"])
@login_required
def theme():
    from achievements import player_progress           # (here to avoid an import loop)
    choice = request.form.get("theme", "")
    level = player_progress(get_db(), g.user["id"])["level"]
    if choice not in THEMES:
        flash(_('Pick a theme from the list.'), "error")
    elif not unlocked(choice, level):
        flash(_('{name} unlocks at level {level}. Keep going!', name=_(THEMES[choice]['name']), level=THEMES[choice]['level']), "error")
    else:
        db = get_db()
        db.execute("UPDATE users SET theme = ? WHERE id = ?", (choice, g.user["id"]))
        db.commit()
        flash(_('{emoji} {name} theme on.', emoji=THEMES[choice]['emoji'], name=_(THEMES[choice]['name'])), "success")
    return redirect(url_for("settings.index") + "#theme")


@bp.route("/logout-others", methods=["POST"])
@login_required
def logout_others():
    sign_out_other_devices(g.user["id"])
    flash(_('Logged out of all other devices. This one stays logged in.'), "success")
    return redirect(url_for("settings.index") + "#security")


@bp.route("/delete", methods=["POST"])
@login_required
def delete_account():
    if request.form.get("confirm") != "DELETE":
        flash(_('Type DELETE (in capitals) to confirm.'), "error")
    elif not current_password_ok(request.form.get("password", "")):
        flash(_('Incorrect password. Your account was NOT deleted.'), "error")
    else:
        db = get_db()
        # ON DELETE CASCADE removes every check-in, habit, task, goal... of this user
        db.execute("DELETE FROM users WHERE id = ?", (g.user["id"],))
        db.commit()
        session.clear()
        flash(_('Your account and all your data have been permanently deleted. 👋'), "success")
        return redirect(url_for("auth.login"))
    return redirect(url_for("settings.index"))
