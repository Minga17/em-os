"""In-app feedback: anyone can send an idea, a problem, or something they love."""
from urllib.parse import urlparse

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from auth import login_required
from db import get_db
from mailer import admin_emails, send_email

bp = Blueprint("feedback", __name__, url_prefix="/feedback")

KINDS = {"idea": "💡 Idea", "problem": "🐞 Problem", "love": "❤️ Something I love"}
MAX_LENGTH = 2000
MAX_PER_DAY = 10


def safe_page(value):
    """Keep only the path of the page they came from (e.g. '/habits/')."""
    path = urlparse(value or "").path
    return path[:200] if path.startswith("/") else None


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        kind = request.form.get("kind", "")
        message = request.form.get("message", "").strip()
        page = safe_page(request.form.get("page"))
        db = get_db()
        sent_today = db.execute(
            "SELECT COUNT(*) FROM feedback WHERE user_id = ? AND created_at >= datetime('now', '-1 day')",
            (g.user["id"],)).fetchone()[0]

        if kind not in KINDS:
            flash("Please choose what kind of feedback this is.", "error")
        elif not message:
            flash("Please write a message.", "error")
        elif len(message) > MAX_LENGTH:
            flash(f"Please keep it under {MAX_LENGTH} characters.", "error")
        elif sent_today >= MAX_PER_DAY:
            flash("Thanks for all the feedback today! Please try again tomorrow.", "error")
        else:
            db.execute("INSERT INTO feedback (user_id, kind, message, page) VALUES (?, ?, ?, ?)",
                       (g.user["id"], kind, message, page))
            db.commit()
            for owner in admin_emails():
                send_email(owner, f"EM OS feedback: {KINDS[kind]}",
                           f"From: {g.user['name']} <{g.user['email']}>\n"
                           f"Page: {page or '-'}\n\n{message}\n", background=True)
            flash("Thank you! 🙏 Your feedback was sent. It really helps make EM OS better.", "success")
            return redirect(url_for("home"))
        return render_template("feedback/index.html", kinds=KINDS, form=request.form,
                               page=page, max_length=MAX_LENGTH)

    return render_template("feedback/index.html", kinds=KINDS, form={},
                           page=safe_page(request.referrer), max_length=MAX_LENGTH)
