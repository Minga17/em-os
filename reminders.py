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
from i18n import _, _n, current_language, translate, translate_n

from auth import login_required
from db import get_db
from habit_model import all_statuses, frozen_days
from streaks import current_streak
from today import habits_left as habits_still_due

bp = Blueprint("reminders", __name__, url_prefix="/reminders")

# Times people can pick, every 30 minutes
REMINDER_TIMES = [f"{h:02d}:{m:02d}" for h in range(6, 24) for m in (0, 30) if (h, m) != (23, 30)]  # evening
MORNING_TIMES = [f"{h:02d}:{m:02d}" for h in range(5, 12) for m in (0, 30)]                        # 5:00-11:30 AM

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
    """'20:30' -> '8:30 PM' in English, '20:30' in Spanish, French and Portuguese"""
    hour, minute = map(int, hhmm.split(":"))
    if current_language() != "en":
        return f"{hour:02d}:{minute:02d}"
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


def user_language(db, user_id):
    row = db.execute("SELECT language FROM users WHERE id = ?", (user_id,)).fetchone()
    return (row[0] if row else None) or "en"


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

    from freezes import apply_freezes           # (imported here to avoid an import loop)
    apply_freezes(db, user_id, today)
    lang = user_language(db, user_id)
    habits_left = len(habits_still_due(all_statuses(db, user_id, today)))
    habits_text = (" " + translate_n(lang, "{n} habit left today.", "{n} habits left today.", habits_left)
                   if habits_left else "")

    streak = current_streak(checkin_dates, today, frozen_days(db, user_id))
    if streak >= 2:
        title = translate_n(lang, "🔥 Keep your {n}-day streak alive!", "🔥 Keep your {n}-day streak alive!", streak)
        body = translate(lang, "3 taps to check in before midnight.") + habits_text
    else:
        title = translate(lang, "🌙 How did today go?")
        body = translate(lang, "Check in with 3 taps and close your day.") + habits_text
    return {"title": title, "body": body.strip(), "url": "/", "tag": "daily-reminder"}


def morning_message(db, user_id, today):
    """Morning nudge to plan the day, or None if their top 3 is already set."""
    planned = db.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND focus_date = ?",
                         (user_id, today.isoformat())).fetchone()[0]
    if planned:
        return None
    lang = user_language(db, user_id)
    return {"title": translate(lang, "🌅 Good morning! What are your top 3?"),
            "body": translate(lang, "Pick the 3 things that would make today a win."),
            "url": "/", "tag": "morning-plan"}


# Each kind of reminder: (time column, "already sent today" column, message)
KINDS = [
    ("reminder_time", "last_reminded_on", reminder_message),
    ("morning_time", "last_morning_on", morning_message),
]


# ── Who gets a reminder right now ───────────────────────────────
def run_reminders(db, now_utc=None, sender=None):
    """Send every reminder that is due. Safe to run every minute, from several workers."""
    now_utc = now_utc or datetime.now(timezone.utc)
    return sum(run_kind(db, now_utc, sender, *kind) for kind in KINDS)


def run_kind(db, now_utc, sender, time_column, sent_column, make_message):
    sent_to = 0
    # Column names come from KINDS above, never from users, so this is safe
    users = db.execute(
        f"""SELECT u.id, u.timezone, u.{time_column} AS remind_at, u.{sent_column} AS sent_on
            FROM users u WHERE u.{time_column} IS NOT NULL
              AND EXISTS (SELECT 1 FROM push_subscriptions s WHERE s.user_id = u.id)"""
    ).fetchall()

    for user in users:
        try:
            tz = ZoneInfo(user["timezone"] or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            tz = ZoneInfo("UTC")
        local = now_utc.astimezone(tz)
        today = local.date()
        if user["sent_on"] == today.isoformat():
            continue                                   # already reminded today

        hour, minute = map(int, user["remind_at"].split(":"))
        minutes_late = (local.hour * 60 + local.minute) - (hour * 60 + minute)
        if not 0 <= minutes_late < SEND_WINDOW_MINUTES:
            continue                                   # not time yet (or way too late)

        # Claim today's reminder first. If two server workers get here at the
        # same moment, only one of them wins, so nobody gets it twice.
        claimed = db.execute(
            f"""UPDATE users SET {sent_column} = ?
                WHERE id = ? AND ({sent_column} IS NULL OR {sent_column} != ?)""",
            (today.isoformat(), user["id"], today.isoformat()),
        ).rowcount
        db.commit()
        if not claimed:
            continue

        message = make_message(db, user["id"], today)
        if message and send_to_user(db, user["id"], message, sender):
            sent_to += 1
    return sent_to


# ── Pages and buttons ───────────────────────────────────────────
@bp.route("/time", methods=["POST"])
@login_required
def set_time():
    db = get_db()
    changes = []
    for field, choices in (("reminder_time", REMINDER_TIMES), ("morning_time", MORNING_TIMES)):
        if field not in request.form:
            continue                                   # this form didn't include it
        choice = request.form.get(field, "")
        if choice and choice not in choices:
            flash(_('Please pick a time from the list.'), "error")
            return redirect(url_for("settings.index") + "#reminders")
        db.execute(f"UPDATE users SET {field} = ? WHERE id = ?", (choice or None, g.user["id"]))
        label = _("Evening check-in") if field == "reminder_time" else _("Morning plan")
        changes.append(f"{label}: {time_label(choice) if choice else _('off')}")
    db.commit()
    flash(_("🔔 Reminders saved. {changes}", changes=" · ".join(changes)), "success")
    return redirect(url_for("settings.index") + "#reminders")


@bp.route("/subscribe", methods=["POST"])
@login_required
def subscribe():
    data = request.get_json(silent=True) if request.is_json else None
    if not isinstance(data, dict):
        return jsonify(error=_("Expected JSON.")), 400
    endpoint = data.get("endpoint")
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    p256dh, auth_key = keys.get("p256dh"), keys.get("auth")
    if not (isinstance(endpoint, str) and len(endpoint) <= 1000 and is_push_endpoint(endpoint)
            and isinstance(p256dh, str) and 0 < len(p256dh) <= 200
            and isinstance(auth_key, str) and 0 < len(auth_key) <= 100):
        return jsonify(error=_("That doesn't look like a browser push subscription.")), 400

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
    return {"reminder_time": row["reminder_time"], "devices": row["devices"],
            "devices_text": _n("{n} device signed up for reminders.", "{n} devices signed up for reminders.",
                               row["devices"])}


@bp.route("/unsubscribe", methods=["POST"])
@login_required
def unsubscribe():
    data = request.get_json(silent=True) if request.is_json else None
    endpoint = data.get("endpoint") if isinstance(data, dict) else None
    if not isinstance(endpoint, str):
        return jsonify(error=_("Expected JSON with an endpoint.")), 400
    db = get_db()
    db.execute("DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?",
               (g.user["id"], endpoint))
    db.commit()
    return jsonify(ok=True, **device_status(db))


@bp.route("/test", methods=["POST"])
@login_required
def test():
    if not push_ready():
        return jsonify(error=_("Reminders aren't set up on this server yet.")), 503
    sent = send_to_user(get_db(), g.user["id"], {
        "title": _("🔔 EM OS reminders are on!"),
        "body": _("This is how your daily reminder will look."),
        "url": "/", "tag": "test",
    })
    return jsonify(sent=sent)
