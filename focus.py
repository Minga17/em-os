"""Focus timer ⏱ (Pomodoro): focus for 25 minutes, then rest.

The server keeps the clock, not the phone. So the timer survives a reload or
a locked screen, and the minutes can't be faked. Finished minutes are added
to today's focused hours (and the check-in score) automatically.
"""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from i18n import _, _n, N_, translate, translate_n

from auth import login_required
from db import get_db
from scoring import compute_score
from timeutil import user_today, user_today_for

bp = Blueprint("focus", __name__, url_prefix="/focus")

LENGTHS = [15, 25, 50]          # minutes to choose from
MIN_MINUTES_TO_KEEP = 1         # stopped before a minute? Not worth logging
SESSION_COUNTS_FROM = 10        # sessions of 10+ minutes earn XP and count for badges


def utcnow():
    return datetime.now(timezone.utc)


def ends_at(session):
    return datetime.fromisoformat(session["started_at"]) + timedelta(minutes=session["planned_minutes"])


def running_session(db, user_id):
    return db.execute("SELECT * FROM focus_sessions WHERE user_id = ? AND ended_at IS NULL "
                      "ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()


def focus_minutes_on(db, user_id, day):
    return db.execute("SELECT COALESCE(SUM(minutes), 0) FROM focus_sessions "
                      "WHERE user_id = ? AND log_date = ? AND minutes IS NOT NULL",
                      (user_id, day)).fetchone()[0]


def add_to_checkin(db, user_id, day, minutes):
    """If that day already has a check-in, add the hours and update its score."""
    row = db.execute("SELECT * FROM checkins WHERE user_id = ? AND log_date = ?", (user_id, day)).fetchone()
    if row is None:
        return False                    # the check-in form will start with these hours
    hours = min(24, round((row["focused_hours"] or 0) + minutes / 60, 2))
    score = compute_score(row["energy"], row["mood"], row["focus"], hours, row["accomplished"], row["wins"])
    db.execute("UPDATE checkins SET focused_hours = ?, score = ?, updated_at = datetime('now') WHERE id = ?",
               (hours, score, row["id"]))
    return True


def finish(db, session, now=None):
    """End a session. Counts the real time spent, never more than planned. Returns minutes kept."""
    now = now or utcnow()
    elapsed = (now - datetime.fromisoformat(session["started_at"])).total_seconds() / 60
    minutes = int(min(elapsed, session["planned_minutes"]))
    if minutes < MIN_MINUTES_TO_KEEP:
        db.execute("DELETE FROM focus_sessions WHERE id = ?", (session["id"],))
        db.commit()
        return 0
    db.execute("UPDATE focus_sessions SET ended_at = ?, minutes = ? WHERE id = ? AND ended_at IS NULL",
               (now.isoformat(), minutes, session["id"]))
    add_to_checkin(db, session["user_id"], session["log_date"], minutes)
    db.commit()
    return minutes


def finish_overdue(db, now=None, user_id=None, sender=None):
    """Sessions whose time is up: finish them (and send a notification if we can).
    Runs every minute in the background, and whenever someone opens the focus page."""
    now = now or utcnow()
    where, params = ("AND user_id = ?", (user_id,)) if user_id else ("", ())
    finished = 0
    for session in db.execute(f"SELECT * FROM focus_sessions WHERE ended_at IS NULL {where}", params).fetchall():
        if ends_at(session) <= now:
            minutes = finish(db, session, now)
            finished += 1
            if sender and minutes and not session["notified"]:
                db.execute("UPDATE focus_sessions SET notified = 1 WHERE id = ?", (session["id"],))
                db.commit()
                from reminders import send_to_user          # (here to avoid an import loop)
                lang = db.execute("SELECT language FROM users WHERE id = ?",
                                  (session["user_id"],)).fetchone()[0] or "en"
                send_to_user(db, session["user_id"], {
                    "title": translate_n(lang, "⏰ Focus done: {n} minute!", "⏰ Focus done: {n} minutes!", minutes),
                    "body": translate(lang, "Logged to today. Take a 5-minute break, you earned it."),
                    "url": "/focus/", "tag": "focus"}, sender)
    return finished


def own_session(session_id):
    session = get_db().execute("SELECT * FROM focus_sessions WHERE id = ? AND user_id = ?",
                               (session_id, g.user["id"])).fetchone()
    if session is None:
        abort(404)
    return session


@bp.route("/")
@login_required
def index():
    db = get_db()
    user_id = g.user["id"]
    finish_overdue(db, user_id=user_id)
    running = running_session(db, user_id)
    today = user_today().isoformat()
    week_start = (user_today() - timedelta(days=user_today().weekday())).isoformat()
    sessions = db.execute(
        """SELECT f.*, t.title AS task_title FROM focus_sessions f LEFT JOIN tasks t ON t.id = f.task_id
           WHERE f.user_id = ? AND f.log_date = ? AND f.minutes IS NOT NULL ORDER BY f.id DESC""",
        (user_id, today)).fetchall()
    tasks = db.execute(
        """SELECT id, title FROM tasks WHERE user_id = ? AND status != 'done'
           ORDER BY focus_date = ? DESC, due_date IS NULL, due_date, priority LIMIT 20""",
        (user_id, today)).fetchall()
    return render_template(
        "focus/index.html", running=running, lengths=LENGTHS, tasks=tasks, sessions=sessions,
        today_minutes=focus_minutes_on(db, user_id, today),
        week_minutes=db.execute("SELECT COALESCE(SUM(minutes), 0) FROM focus_sessions "
                                "WHERE user_id = ? AND log_date >= ?", (user_id, week_start)).fetchone()[0],
        ends_at_ms=int(ends_at(running).timestamp() * 1000) if running else None,
        started_ms=int(datetime.fromisoformat(running["started_at"]).timestamp() * 1000) if running else None,
        chosen_task=request.args.get("task", type=int),
    )


@bp.route("/start", methods=["POST"])
@login_required
def start():
    db = get_db()
    user_id = g.user["id"]
    minutes = request.form.get("minutes", type=int)
    if minutes not in LENGTHS:
        flash(_('Pick 15, 25 or 50 minutes.'), "error")
        return redirect(url_for("focus.index"))
    task_id = request.form.get("task_id", type=int)
    if task_id and not db.execute("SELECT 1 FROM tasks WHERE id = ? AND user_id = ?",
                                  (task_id, user_id)).fetchone():
        task_id = None                  # not theirs: just ignore it
    old = running_session(db, user_id)
    if old:
        finish(db, old)                 # only one session at a time
    db.execute("""INSERT INTO focus_sessions (user_id, task_id, label, planned_minutes, started_at, log_date)
                  VALUES (?, ?, ?, ?, ?, ?)""",
               (user_id, task_id, request.form.get("label", "").strip()[:80], minutes,
                utcnow().isoformat(), user_today_for(db, user_id).isoformat()))
    db.commit()
    return redirect(url_for("focus.index"))


@bp.route("/<int:session_id>/finish", methods=["POST"])
@login_required
def stop(session_id):
    session = own_session(session_id)
    if session["ended_at"]:
        return redirect(url_for("focus.index"))
    minutes = finish(get_db(), session)
    if minutes:
        flash(_('⏱ {minutes} minutes of focus logged to today. Nice work!', minutes=minutes), "success")
    else:
        flash(_('Session cancelled. Nothing was logged.'), "success")
    return redirect(url_for("focus.index"))


@bp.route("/<int:session_id>/cancel", methods=["POST"])
@login_required
def cancel(session_id):
    session = own_session(session_id)
    if not session["ended_at"]:
        db = get_db()
        db.execute("DELETE FROM focus_sessions WHERE id = ?", (session_id,))
        db.commit()
        flash(_('Session cancelled. Nothing was logged.'), "success")
    return redirect(url_for("focus.index"))
