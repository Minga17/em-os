"""Daily reminder notifications (web push).

How it works:
1. On the Settings page a person picks a reminder time and taps
   "Turn on for this device". The phone gives us a "subscription":
   a private address where we can deliver notifications to that phone.
2. The background loop in jobs.py wakes up every minute. For everyone whose
   reminder time has come (on their own clock) and who hasn't checked in
   today, it sends one notification, at most once per day.

Sending needs the `pywebpush` package and VAPID keys (see make_vapid_keys.py).
If either is missing (for example on a laptop that blocks the package),
the rest of the app still works: reminders are just switched off.
"""
import json
from datetime import date, datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, flash, g, jsonify, redirect, request, url_for

from auth import login_required
from db import get_db
from streaks import current_streak

bp = Blueprint("reminders", __name__, url_prefix="/reminders")

# Reminder times people can pick: every 30 minutes from 6:00 AM to 11:00 PM
REMINDER_TIMES = [f"{h:02d}:{m:02d}" for h in range(6, 24) for m in (0, 30) if (h, m) != (23, 30)]

SEND_WINDOW_MINUTES = 120   # if the server was busy or restarting, still send up to 2 hours late

# Only real browser push services. Anything else could be used to make our
# server send requests to places it shouldn't.
PUSH_HOSTS = (
    "fcm.googleapis.com",                  # Chrome, Edge, Android
    "updates.push.services.mozilla.com",   # Firefox
    "web.push.apple.com",                  # Safari, iPhone
    ".push.apple.com",
    ".notify.windows.com",                 # older Edge
)


def time_label(hhmm):
    """'20:30' -> '8:30 PM'"""
    hour, minute = map(int, hhmm.split(":"))
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


def is_push_endpoint(url):
    parsed = urlparse(url or "")
    host = parsed.hostname or ""
    return parsed.scheme == "https" and any(
        host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


# ── Sending ─────────────────────────────────────────────────────
def load_webpush():
    """Import pywebpush only when needed, so the app runs without it."""
    try:
        from pywebpush import WebPushException, webpush
        return webpush, WebPushException
    except Exception:            # not installed, or blocked by the computer
        return None


def push_ready(app=None):
    app = app or current_app
    return bool(app.config.get("VAPID_PUBLIC_KEY") and app.config.get("VAPID_PRIVATE_KEY")
                and load_webpush())


def send_push(subscription, payload):
    """Deliver one notification. Returns 'sent', 'gone' (phone unsubscribed) or 'failed'."""
    loaded = load_webpush()
    if not loaded:
        return "failed"
    webpush, WebPushException = loaded
    try:
        webpush(
            subscription_info={"endpoint": subscription["endpoint"],
                               "keys": {"p256dh": subscription["p256dh"],
                                        "auth": subscription["auth"]}},
            data=json.dumps(payload),
            vapid_private_key=current_app.config["VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": current_app.config["VAPID_SUBJECT"]},
            ttl=4 * 60 * 60,     # if the phone is off, deliver within 4 hours or not at all
            timeout=10,
        )
        return "sent"
    except WebPushException as error:
        status = getattr(error.response, "status_code", None)
        if status in (404, 410):
            return "gone"
        current_app.logger.warning("Push failed (%s): %s", status, error)
        return "failed"
    except Exception as error:   # network problems etc.
        current_app.logger.warning("Push failed: %s", error)
        return "failed"


def send_to_user(db, user_id, payload, sender=None):
    """Send to every device of one user. Removes devices that unsubscribed."""
    sender = sender or send_push
    sent = 0
    for sub in db.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?",
                          (user_id,)).fetchall():
        result = sender(sub, payload)
        if result == "sent":
            sent += 1
        elif result == "gone":
            db.execute("DELETE FROM push_subscriptions WHERE id = ?", (sub["id"],))
            db.commit()
    return sent


# ── What the reminder says ──────────────────────────────────────
def reminder_message(db, user_id, today):
    """The notification text, or None if they already checked in today."""
    checkin_dates = {date.fromisoformat(r[0]) for r in db.execute(
        "SELECT log_date FROM checkins WHERE user_id = ?", (user_id,))}
    if today in checkin_dates:
        return None

    habits_left = db.execute(
        """SELECT COUNT(*) FROM habits h
           WHERE h.user_id = ? AND h.is_active = 1
             AND NOT EXISTS (SELECT 1 FROM habit_logs l
                             WHERE l.habit_id = h.id AND l.log_date = ?)""",
        (user_id, today.isoformat()),
    ).fetchone()[0]
    habits_text = (f" {habits_left} habit{'s' if habits_left != 1 else ''} left today."
                   if habits_left else "")

    streak = current_streak(checkin_dates, today)
    if streak >= 2:
        title = f"🔥 Keep your {streak}-day streak alive!"
        body = "Check in today before midnight." + habits_text
    else:
        title = "How did today go?"
        body = "Take 1 minute to check in and earn +10 XP." + habits_text
    return {"title": title, "body": body.strip(), "url": "/checkin/", "tag": "daily-reminder"}


# ── Who gets a reminder right now ───────────────────────────────
def run_reminders(db, now_utc=None, sender=None):
    """Send every reminder that is due. Safe to run every minute, from several workers."""
    now_utc = now_utc or datetime.now(timezone.utc)
    sent_to = 0
    users = db.execute(
        """SELECT u.id, u.timezone, u.reminder_time, u.last_reminded_on FROM users u
           WHERE u.reminder_time IS NOT NULL
             AND EXISTS (SELECT 1 FROM push_subscriptions s WHERE s.user_id = u.id)"""
    ).fetchall()

    for user in users:
        try:
            tz = ZoneInfo(user["timezone"] or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            tz = ZoneInfo("UTC")
        local = now_utc.astimezone(tz)
        today = local.date()
        if user["last_reminded_on"] == today.isoformat():
            continue                                   # already reminded today

        hour, minute = map(int, user["reminder_time"].split(":"))
        minutes_late = (local.hour * 60 + local.minute) - (hour * 60 + minute)
        if not 0 <= minutes_late < SEND_WINDOW_MINUTES:
            continue                                   # not time yet (or way too late)

        # Claim today's reminder first. If two server workers get here at the
        # same moment, only one of them wins, so nobody gets it twice.
        claimed = db.execute(
            """UPDATE users SET last_reminded_on = ?
               WHERE id = ? AND (last_reminded_on IS NULL OR last_reminded_on != ?)""",
            (today.isoformat(), user["id"], today.isoformat()),
        ).rowcount
        db.commit()
        if not claimed:
            continue

        message = reminder_message(db, user["id"], today)
        if message and send_to_user(db, user["id"], message, sender):
            sent_to += 1
    return sent_to


# ── Pages and buttons ───────────────────────────────────────────
@bp.route("/time", methods=["POST"])
@login_required
def set_time():
    choice = request.form.get("reminder_time", "")
    if choice and choice not in REMINDER_TIMES:
        flash("Please pick a time from the list.", "error")
        return redirect(url_for("settings.index"))
    db = get_db()
    db.execute("UPDATE users SET reminder_time = ? WHERE id = ?", (choice or None, g.user["id"]))
    db.commit()
    if choice:
        flash(f"🔔 Daily reminder set for {time_label(choice)}.", "success")
    else:
        flash("Daily reminder turned off.", "success")
    return redirect(url_for("settings.index") + "#reminders")


@bp.route("/subscribe", methods=["POST"])
@login_required
def subscribe():
    data = request.get_json(silent=True) if request.is_json else None
    if not isinstance(data, dict):
        return jsonify(error="Expected JSON."), 400
    endpoint = data.get("endpoint")
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    p256dh, auth_key = keys.get("p256dh"), keys.get("auth")
    if not (isinstance(endpoint, str) and len(endpoint) <= 1000 and is_push_endpoint(endpoint)
            and isinstance(p256dh, str) and 0 < len(p256dh) <= 200
            and isinstance(auth_key, str) and 0 < len(auth_key) <= 100):
        return jsonify(error="That doesn't look like a browser push subscription."), 400

    db = get_db()
    db.execute(
        """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) VALUES (?, ?, ?, ?)
           ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id,
               p256dh = excluded.p256dh, auth = excluded.auth""",
        (g.user["id"], endpoint, p256dh, auth_key),
    )
    # First device? Pick a sensible reminder time so it just works.
    db.execute("UPDATE users SET reminder_time = COALESCE(reminder_time, '20:00') WHERE id = ?",
               (g.user["id"],))
    db.commit()
    return jsonify(ok=True, **device_status(db))


def device_status(db):
    """What the Settings page shows: reminder time and how many devices are signed up."""
    row = db.execute(
        """SELECT reminder_time,
                  (SELECT COUNT(*) FROM push_subscriptions WHERE user_id = users.id) AS devices
           FROM users WHERE id = ?""", (g.user["id"],)).fetchone()
    return {"reminder_time": row["reminder_time"], "devices": row["devices"]}


@bp.route("/unsubscribe", methods=["POST"])
@login_required
def unsubscribe():
    data = request.get_json(silent=True) if request.is_json else None
    endpoint = data.get("endpoint") if isinstance(data, dict) else None
    if not isinstance(endpoint, str):
        return jsonify(error="Expected JSON with an endpoint."), 400
    db = get_db()
    db.execute("DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?",
               (g.user["id"], endpoint))
    db.commit()
    return jsonify(ok=True, **device_status(db))


@bp.route("/test", methods=["POST"])
@login_required
def test():
    if not push_ready():
        return jsonify(error="Reminders aren't set up on this server yet."), 503
    sent = send_to_user(get_db(), g.user["id"], {
        "title": "🔔 EM OS reminders are on!",
        "body": "This is how your daily reminder will look.",
        "url": "/", "tag": "test",
    })
    return jsonify(sent=sent)
