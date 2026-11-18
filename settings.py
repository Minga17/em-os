from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from auth import login_required
from db import get_db
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
    return render_template("settings/index.html", timezones=all_timezones(),
                           local_today=user_today())


@bp.route("/profile", methods=["POST"])
@login_required
def profile():
    name = request.form.get("name", "").strip()
    timezone = request.form.get("timezone", "")
    if not name:
        flash("Name can't be empty.", "error")
    elif not is_valid_timezone(timezone):
        flash("Please pick a valid time zone.", "error")
    else:
        db = get_db()
        db.execute("UPDATE users SET name = ?, timezone = ? WHERE id = ?",
                   (name, timezone, g.user["id"]))
        db.commit()
        flash("Profile saved.", "success")
    return redirect(url_for("settings.index"))


@bp.route("/password", methods=["POST"])
@login_required
def password():
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    if not current_password_ok(current):
        flash("Your current password is incorrect.", "error")
    elif len(new) < 8:
        flash("New password must be at least 8 characters.", "error")
    elif new != confirm:
        flash("The new passwords don't match.", "error")
    else:
        db = get_db()
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (generate_password_hash(new), g.user["id"]))
        db.commit()
        flash("Password changed. 🔒", "success")
    return redirect(url_for("settings.index"))


@bp.route("/delete", methods=["POST"])
@login_required
def delete_account():
    if request.form.get("confirm") != "DELETE":
        flash('Type DELETE (in capitals) to confirm.', "error")
    elif not current_password_ok(request.form.get("password", "")):
        flash("Incorrect password. Your account was NOT deleted.", "error")
    else:
        db = get_db()
        # ON DELETE CASCADE removes every check-in, habit, task, goal... of this user
        db.execute("DELETE FROM users WHERE id = ?", (g.user["id"],))
        db.commit()
        session.clear()
        flash("Your account and all your data have been permanently deleted. 👋", "success")
        return redirect(url_for("auth.login"))
    return redirect(url_for("settings.index"))
