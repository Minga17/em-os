from datetime import timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from auth import login_required
from db import get_db
from freezes import award_freeze
from notify import toast
from scoring import compute_score
from timeutil import user_today
from today import today_summary

bp = Blueprint("checkin", __name__, url_prefix="/checkin")


def parse_rating(value, label, errors):
    """Turn a form value into a whole number 1-5, or record an error."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        errors.append(f"{label} is required.")
        return None
    if not 1 <= number <= 5:
        errors.append(f"{label} must be between 1 and 5.")
        return None
    return number


@bp.route("/", methods=["GET", "POST"])
@login_required
def today():
    db = get_db()
    user_id = g.user["id"]
    today_str = user_today().isoformat()

    if request.method == "POST":
        errors = []
        energy = parse_rating(request.form.get("energy"), "Energy", errors)
        mood = parse_rating(request.form.get("mood"), "Mood", errors)
        focus = parse_rating(request.form.get("focus"), "Focus", errors)

        try:
            hours = float(request.form.get("focused_hours") or 0)
            if not 0 <= hours <= 24:
                errors.append("Focused hours must be between 0 and 24.")
        except ValueError:
            errors.append("Focused hours must be a number.")
            hours = 0

        accomplished = request.form.get("accomplished", "").strip()
        wins = request.form.get("wins", "").strip()
        blockers = request.form.get("blockers", "").strip()
        priorities = request.form.get("priorities", "").strip()

        if errors:
            for error in errors:
                flash(error, "error")
        else:
            score = compute_score(energy, mood, focus, hours, accomplished, wins)
            db.execute(
                """
                INSERT INTO checkins
                    (user_id, log_date, energy, mood, focus, focused_hours,
                     accomplished, wins, blockers, priorities, score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (user_id, log_date) DO UPDATE SET
                    energy        = excluded.energy,
                    mood          = excluded.mood,
                    focus         = excluded.focus,
                    focused_hours = excluded.focused_hours,
                    accomplished  = excluded.accomplished,
                    wins          = excluded.wins,
                    blockers      = excluded.blockers,
                    priorities    = excluded.priorities,
                    score         = excluded.score,
                    updated_at    = datetime('now')
                """,
                (user_id, today_str, energy, mood, focus, hours,
                 accomplished, wins, blockers, priorities, score),
            )
            db.commit()
            flash(f"Check-in saved! Today's score: {score}/100", "success")
            earned = award_freeze(db, user_id, user_today())
            if earned:
                flash(earned, "achievement")
            return redirect(url_for("home"))

        values = request.form  # keep what they typed when there's an error
    else:
        existing = db.execute(
            "SELECT * FROM checkins WHERE user_id = ? AND log_date = ?",
            (user_id, today_str),
        ).fetchone()
        if existing:
            values = dict(existing)
        else:
            # Start today with yesterday's priorities pre-filled
            yesterday = (user_today() - timedelta(days=1)).isoformat()
            prev = db.execute(
                "SELECT priorities FROM checkins WHERE user_id = ? AND log_date = ?",
                (user_id, yesterday),
            ).fetchone()
            values = {"priorities": prev["priorities"] if prev else ""}

    return render_template("checkin/today.html", values=values, today=today_str)


@bp.route("/quick", methods=["POST"])
@login_required
def quick():
    """3-tap check-in from the Today screen: just energy, mood and focus."""
    db = get_db()
    user_id = g.user["id"]
    today_str = user_today().isoformat()
    errors = []
    energy = parse_rating(request.form.get("energy"), "Energy", errors)
    mood = parse_rating(request.form.get("mood"), "Mood", errors)
    focus = parse_rating(request.form.get("focus"), "Focus", errors)

    if errors:
        if request.headers.get("HX-Request"):
            toast("Pick energy, mood and focus (1 to 5).")
        else:
            for error in errors:
                flash(error, "error")
    else:
        existing = db.execute("SELECT * FROM checkins WHERE user_id = ? AND log_date = ?",
                              (user_id, today_str)).fetchone()
        if existing:
            # Keep their hours and notes, only change the three ratings
            score = compute_score(energy, mood, focus, existing["focused_hours"],
                                  existing["accomplished"], existing["wins"])
            db.execute("""UPDATE checkins SET energy = ?, mood = ?, focus = ?, score = ?,
                          updated_at = datetime('now') WHERE id = ?""",
                       (energy, mood, focus, score, existing["id"]))
        else:
            score = compute_score(energy, mood, focus, 0, "", "")
            db.execute("""INSERT INTO checkins (user_id, log_date, energy, mood, focus, score)
                          VALUES (?, ?, ?, ?, ?, ?)""", (user_id, today_str, energy, mood, focus, score))
            toast(f"+10 XP · 📝 Checked in! Score {score}/100")
        db.commit()
        earned = award_freeze(db, user_id, user_today())
        if not request.headers.get("HX-Request"):
            flash(f"Check-in saved! Today's score: {score}/100", "success")
            if earned:
                flash(earned, "achievement")
        elif earned:
            toast(earned)

    if request.headers.get("HX-Request"):
        summary = today_summary(db, user_id)
        if summary["all_done"]:
            toast("🎉 Everything done today. Amazing!")
        today_checkin = db.execute("SELECT score FROM checkins WHERE user_id = ? AND log_date = ?",
                                   (user_id, today_str)).fetchone()
        return render_template("today/_checkin_done.html", today_checkin=today_checkin, summary=summary)
    return redirect(url_for("home"))
