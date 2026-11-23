import functools
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
)
from i18n import _, current_language, translate
from itsdangerous import BadData, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db
from mailer import external_url, send_email
from timeutil import all_timezones, is_valid_timezone, local_today_for

bp = Blueprint("auth", __name__, url_prefix="/auth")

RESET_LINK_HOURS = 1
VERIFY_LINK_DAYS = 7
EMAIL_COOLDOWN_SECONDS = 60          # at most one account email per minute per person


@bp.before_app_request
def load_logged_in_user():
    """Before every request, look up who is logged in (if anyone)."""
    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        user = get_db().execute(
            "SELECT id, name, email, timezone, email_verified, session_version, onboarded, theme, language "
            "FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        # "Log out of all devices" bumps session_version, so older logins stop working
        if user is not None and session.get("sv", 0) == user["session_version"]:
            g.user = user
        else:
            session.clear()


def login_required(view):
    """Decorator: send visitors to the login page if they aren't logged in."""
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            flash(_('Please log in first.'), "error")
            return redirect(url_for("auth.login"))
        return view(**kwargs)
    return wrapped_view


def log_in(user):
    session.clear()
    session["user_id"] = user["id"]
    session["sv"] = user["session_version"]


def sign_out_other_devices(user_id):
    """Every other logged-in phone or computer gets logged out. This one stays in."""
    db = get_db()
    db.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (user_id,))
    db.commit()
    if session.get("user_id") == user_id:
        session["sv"] = db.execute("SELECT session_version FROM users WHERE id = ?",
                                   (user_id,)).fetchone()[0]


# ── Signed links (password reset, email confirmation) ───────────
def make_token(purpose, data):
    return URLSafeTimedSerializer(current_app.secret_key, salt=purpose).dumps(data)


def read_token(purpose, token, max_age):
    try:
        return URLSafeTimedSerializer(current_app.secret_key, salt=purpose).loads(token, max_age=max_age)
    except BadData:
        return None


def password_fingerprint(password_hash):
    """Changes whenever the password changes, so a reset link only works once."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def may_send_email(user_id):
    """Allow one account email per minute per person, so nobody can spam an inbox."""
    db = get_db()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=EMAIL_COOLDOWN_SECONDS)).isoformat()
    allowed = db.execute(
        "UPDATE users SET email_sent_at = ? WHERE id = ? AND (email_sent_at IS NULL OR email_sent_at < ?)",
        (now.isoformat(), user_id, cutoff),
    ).rowcount
    db.commit()
    return bool(allowed)


def email_language(user):
    language = user["language"] if "language" in user.keys() else None
    return language or current_language()


def send_verification_email(user):
    token = make_token("verify-email", {"uid": user["id"], "email": user["email"]})
    link = external_url(url_for("auth.verify_email", token=token))
    lang = email_language(user)
    return send_email(user["email"], translate(lang, "Confirm your EM OS email"), translate(lang, """Hi {name},

Welcome to EM OS! Please confirm your email address:

{link}

Confirming lets you reset your password if you ever forget it.
This link works for {days} days.

If you didn't create an EM OS account, you can ignore this email.
""", name=user["name"], link=link, days=VERIFY_LINK_DAYS), background=True)


# ── Sign up, log in, log out ────────────────────────────────────
@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        timezone_name = request.form.get("timezone", "UTC")
        error = None
        if not name:
            error = _("Name is required.")
        elif not email or "@" not in email or len(email) > 254:
            error = _("Please enter a valid email address.")
        elif len(password) < 8:
            error = _("Password must be at least 8 characters.")
        elif not is_valid_timezone(timezone_name):
            error = _("Please pick a valid time zone.")
        if error is None:
            try:
                db = get_db()
                cursor = db.execute(
                    "INSERT INTO users (name, email, password_hash, timezone, language) VALUES (?, ?, ?, ?, ?)",
                    (name, email, generate_password_hash(password), timezone_name, current_language()),
                )
                db.execute("INSERT OR IGNORE INTO activity_days (user_id, day) VALUES (?, ?)",
                           (cursor.lastrowid, local_today_for(timezone_name).isoformat()))
                db.commit()
            except sqlite3.IntegrityError:
                error = _("That email is already registered.")
            else:
                user = db.execute("SELECT id, name, email, language FROM users WHERE id = ?",
                                  (cursor.lastrowid,)).fetchone()
                may_send_email(user["id"])
                send_verification_email(user)
                flash(_('Account created! Please log in. We also sent you an email to confirm your address.'),
                      "success")
                return redirect(url_for("auth.login"))
        flash(error, "error")
    return render_template("auth/register.html", timezones=all_timezones())


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = get_db().execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash(_('Incorrect email or password.'), "error")
        else:
            log_in(user)
            flash(_('Welcome back, {name}!', name=user['name']), "success")
            return redirect(url_for("home"))
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash(_("You've been logged out."), "success")
    return redirect(url_for("auth.login"))


# ── Forgot password ─────────────────────────────────────────────
@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_db().execute("SELECT id, name, email, password_hash, language FROM users WHERE email = ?",
                                (email,)).fetchone()
        if user is not None and may_send_email(user["id"]):
            token = make_token("password-reset", {
                "uid": user["id"], "pw": password_fingerprint(user["password_hash"])})
            link = external_url(url_for("auth.reset_password", token=token))
            lang = email_language(user)
            send_email(user["email"], translate(lang, "Reset your EM OS password"), translate(lang, """Hi {name},

Someone (hopefully you) asked to reset your EM OS password. Choose a new one here:

{link}

This link works for 1 hour and only once.

If you didn't ask for this, ignore this email. Your password stays the same.
""", name=user["name"], link=link), background=True)
        # Same answer whether or not the account exists, so nobody can check who uses EM OS
        flash(_("If that email has an EM OS account, we've sent a link to reset the password. Check your inbox (and spam folder)."), "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html")


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    data = read_token("password-reset", token, max_age=RESET_LINK_HOURS * 3600)
    db = get_db()
    user = None
    if isinstance(data, dict):
        user = db.execute("SELECT id, password_hash FROM users WHERE id = ?",
                          (data.get("uid"),)).fetchone()
    if user is None or data.get("pw") != password_fingerprint(user["password_hash"]):
        flash(_('This reset link has expired or was already used. Please ask for a new one.'), "error")
        return redirect(url_for("auth.forgot"))

    if request.method == "POST":
        new = request.form.get("new_password", "")
        if len(new) < 8:
            flash(_('Password must be at least 8 characters.'), "error")
        elif new != request.form.get("confirm_password", ""):
            flash(_("The passwords don't match."), "error")
        else:
            # New password, email proven, and every old login is signed out
            db.execute("""UPDATE users SET password_hash = ?, email_verified = 1,
                          session_version = session_version + 1 WHERE id = ?""",
                       (generate_password_hash(new), user["id"]))
            db.commit()
            session.clear()
            flash(_('Password changed! 🔒 Please log in with your new password.'), "success")
            return redirect(url_for("auth.login"))

    response = current_app.make_response(render_template("auth/reset.html"))
    response.headers["Referrer-Policy"] = "no-referrer"     # keep the link private
    return response


# ── Confirm email ───────────────────────────────────────────────
@bp.route("/verify/<token>")
def verify_email(token):
    data = read_token("verify-email", token, max_age=VERIFY_LINK_DAYS * 86400)
    db = get_db()
    updated = 0
    if isinstance(data, dict):
        updated = db.execute("UPDATE users SET email_verified = 1 WHERE id = ? AND email = ?",
                             (data.get("uid"), data.get("email"))).rowcount
        db.commit()
    if updated:
        flash(_('✅ Email confirmed. Thanks!'), "success")
    else:
        flash(_('This confirmation link has expired. Log in and ask for a new one.'), "error")
    return redirect(url_for("home") if g.user else url_for("auth.login"))


@bp.route("/verify/resend", methods=["POST"])
@login_required
def resend_verification():
    if g.user["email_verified"]:
        flash(_('Your email is already confirmed.'), "success")
    elif may_send_email(g.user["id"]):
        send_verification_email(g.user)
        flash(_('Sent! Check {email} (and your spam folder).', email=g.user['email']), "success")
    else:
        flash(_('We just sent one. Please wait a minute before asking again.'), "error")
    return redirect(url_for("home"))
