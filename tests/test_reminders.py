"""Daily reminder notifications. No real phones needed: sending is faked."""
import sqlite3
from datetime import datetime, timezone

import pytest

import reminders
from reminders import is_push_endpoint, run_reminders, time_label

FCM = "https://fcm.googleapis.com/fcm/send/abc123"
SUBSCRIPTION = {"endpoint": FCM, "keys": {"p256dh": "BPublicKeyFromTheBrowser", "auth": "authsecret"}}


def connect(app):
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class FakeSender:
    """Pretends to be the push service and remembers what it 'sent'."""

    def __init__(self, result="sent"):
        self.result = result
        self.sent = []

    def __call__(self, subscription, payload):
        self.sent.append((subscription["endpoint"], payload))
        return self.result


@pytest.fixture
def user(app, auth):
    """A logged-in user (UTC) with one device and a reminder at 8:00 PM."""
    auth.register_and_login()
    with connect(app) as db:
        db.execute("UPDATE users SET reminder_time = '20:00'")
        db.execute("INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) "
                   "VALUES (1, ?, 'key', 'secret')", (FCM,))
    return 1


@pytest.fixture
def push_on(app, monkeypatch):
    """Pretend the server has keys and pywebpush installed."""
    app.config.update(VAPID_PUBLIC_KEY="BPublic", VAPID_PRIVATE_KEY="private")
    monkeypatch.setattr(reminders, "load_webpush", lambda: (object(), Exception))


# ── Small helpers ───────────────────────────────────────────────
def test_time_labels():
    assert time_label("06:00") == "6:00 AM"
    assert time_label("12:30") == "12:30 PM"
    assert time_label("20:00") == "8:00 PM"
    assert time_label("00:00") == "12:00 AM"


def test_only_real_push_services_are_allowed():
    assert is_push_endpoint(FCM)
    assert is_push_endpoint("https://updates.push.services.mozilla.com/wpush/v2/x")
    assert is_push_endpoint("https://web.push.apple.com/abc")
    assert not is_push_endpoint("http://fcm.googleapis.com/x")              # not HTTPS
    assert not is_push_endpoint("https://evil.example.com/x")
    assert not is_push_endpoint("https://fcm.googleapis.com.evil.com/x")
    assert not is_push_endpoint("https://localhost/x")


def test_new_columns_and_table_exist(app):
    with connect(app) as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        assert {"reminder_time", "last_reminded_on"} <= columns
        assert db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 0


# ── Choosing a time ─────────────────────────────────────────────
def test_set_and_turn_off_reminder_time(app, client, auth):
    auth.register_and_login()
    response = client.post("/reminders/time", data={"reminder_time": "21:30"}, follow_redirects=True)
    assert "9:30 PM" in response.get_data(as_text=True)
    with connect(app) as db:
        assert db.execute("SELECT reminder_time FROM users").fetchone()[0] == "21:30"

    client.post("/reminders/time", data={"reminder_time": ""})
    with connect(app) as db:
        assert db.execute("SELECT reminder_time FROM users").fetchone()[0] is None


def test_made_up_times_are_rejected(app, client, auth):
    auth.register_and_login()
    for bad in ["25:00", "20:15", "'; DROP TABLE users; --"]:
        client.post("/reminders/time", data={"reminder_time": bad})
    with connect(app) as db:
        assert db.execute("SELECT reminder_time FROM users").fetchone()[0] is None


def test_reminder_routes_need_login(client):
    assert client.post("/reminders/time", data={"reminder_time": "20:00"}).status_code == 302
    assert client.post("/reminders/subscribe", json=SUBSCRIPTION).status_code == 302
    assert client.post("/reminders/test", json={}).status_code == 302


# ── Signing a device up ─────────────────────────────────────────
def test_subscribe_saves_device_and_sets_default_time(app, client, auth):
    auth.register_and_login()
    response = client.post("/reminders/subscribe", json=SUBSCRIPTION)
    assert response.get_json() == {"ok": True, "reminder_time": "20:00", "devices": 1,
                                   "devices_text": "1 device signed up for reminders."}
    client.post("/reminders/subscribe", json=SUBSCRIPTION)            # again: no duplicate
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 1
        assert db.execute("SELECT reminder_time FROM users").fetchone()[0] == "20:00"


def test_subscribe_keeps_a_chosen_time(app, client, auth):
    auth.register_and_login()
    client.post("/reminders/time", data={"reminder_time": "07:00"})
    client.post("/reminders/subscribe", json=SUBSCRIPTION)
    with connect(app) as db:
        assert db.execute("SELECT reminder_time FROM users").fetchone()[0] == "07:00"


@pytest.mark.parametrize("body", [
    {"endpoint": "https://evil.example.com/x", "keys": {"p256dh": "a", "auth": "b"}},
    {"endpoint": FCM, "keys": {"p256dh": "", "auth": "b"}},
    {"endpoint": FCM},
    {"endpoint": FCM + "x" * 2000, "keys": {"p256dh": "a", "auth": "b"}},
    ["not", "an", "object"],
])
def test_bad_subscriptions_are_rejected(app, client, auth, body):
    auth.register_and_login()
    assert client.post("/reminders/subscribe", json=body).status_code == 400
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 0


def test_subscribe_needs_json_not_a_form(client, auth):
    """A form posted from another website can't sign someone's account up."""
    auth.register_and_login()
    assert client.post("/reminders/subscribe", data={"endpoint": FCM}).status_code == 400


def test_unsubscribe_only_removes_your_own_device(app, client, auth):
    auth.register_and_login()
    client.post("/reminders/subscribe", json=SUBSCRIPTION)
    auth.logout()
    auth.register_and_login(name="Other", email="other@example.com")
    client.post("/reminders/unsubscribe", json={"endpoint": FCM})     # not theirs
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 1


def test_deleting_account_removes_devices(app, client, user):
    client.post("/settings/delete", data={"confirm": "DELETE", "password": "password123"})
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 0


# ── Sending at the right moment ─────────────────────────────────
def run(app, when, sender):
    with app.app_context(), connect(app) as db:
        return run_reminders(db, utc(when), sender)


def test_no_reminder_before_the_time(app, user):
    sender = FakeSender()
    assert run(app, "2026-11-19T19:59:00", sender) == 0
    assert sender.sent == []


def test_reminder_sent_at_the_time(app, user):
    sender = FakeSender()
    assert run(app, "2026-11-19T20:00:00", sender) == 1
    endpoint, payload = sender.sent[0]
    assert endpoint == FCM
    assert payload["url"] == "/"                   # Today, where the 3-tap check-in is
    assert "check in" in (payload["title"] + payload["body"]).lower()


def test_only_one_reminder_per_day(app, user):
    sender = FakeSender()
    run(app, "2026-11-19T20:00:00", sender)
    run(app, "2026-11-19T20:01:00", sender)      # the loop runs every minute
    run(app, "2026-11-19T21:30:00", sender)
    assert len(sender.sent) == 1
    run(app, "2026-11-20T20:00:00", sender)      # next day: reminded again
    assert len(sender.sent) == 2


def test_two_workers_at_once_send_only_once(app, user):
    """Render runs 2 copies of the app; both loops may wake at the same second."""
    sender = FakeSender()
    with app.app_context(), connect(app) as db1, connect(app) as db2:
        run_reminders(db1, utc("2026-11-19T20:00:00"), sender)
        run_reminders(db2, utc("2026-11-19T20:00:00"), sender)
    assert len(sender.sent) == 1


def test_two_workers_racing_send_only_once(app, user):
    """Worst case: both workers read 'not reminded yet' before either one saves."""
    sender = FakeSender()

    class SlowWorker:
        """Lets the other worker run completely just before this one saves."""
        def __init__(self, db, other):
            self.db, self.other, self.raced = db, other, False

        def execute(self, sql, params=()):
            if sql.lstrip().startswith("UPDATE users SET last_reminded_on") and not self.raced:
                self.raced = True
                run_reminders(self.other, utc("2026-11-19T20:00:00"), sender)
            return self.db.execute(sql, params)

        def commit(self):
            self.db.commit()

    with app.app_context(), connect(app) as db1, connect(app) as db2:
        run_reminders(SlowWorker(db1, db2), utc("2026-11-19T20:00:00"), sender)
    assert len(sender.sent) == 1


def test_too_late_is_skipped(app, user):
    """If the server was down all evening, don't wake people up at midnight."""
    sender = FakeSender()
    assert run(app, "2026-11-19T23:00:00", sender) == 0


def test_no_reminder_if_already_checked_in(app, client, user):
    client.post("/checkin/", data={"energy": 4, "mood": 4, "focus": 4, "focused_hours": 3})
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM checkins").fetchone()[0] == 1
        db.execute("UPDATE checkins SET log_date = '2026-11-19'")
    sender = FakeSender()
    run(app, "2026-11-19T20:00:00", sender)
    assert sender.sent == []


def test_reminder_follows_the_users_time_zone(app, user):
    """8:00 PM in Chicago is 02:00 the next morning in UTC."""
    with connect(app) as db:
        db.execute("UPDATE users SET timezone = 'America/Chicago'")
    sender = FakeSender()
    assert run(app, "2026-11-19T20:00:00", sender) == 0       # 2:00 PM in Chicago
    assert run(app, "2026-11-20T02:00:00", sender) == 1       # 8:00 PM in Chicago


def test_reminder_turned_off_means_no_reminder(app, user):
    with connect(app) as db:
        db.execute("UPDATE users SET reminder_time = NULL")
    sender = FakeSender()
    assert run(app, "2026-11-19T20:00:00", sender) == 0


def test_streak_message(app, user):
    with connect(app) as db:
        for day in ("2026-11-16", "2026-11-17", "2026-11-18"):
            db.execute("INSERT INTO checkins (user_id, log_date, energy, mood, focus, score) "
                       "VALUES (1, ?, 3, 3, 3, 50)", (day,))
    sender = FakeSender()
    run(app, "2026-11-19T20:00:00", sender)
    assert "3-day streak" in sender.sent[0][1]["title"]


def test_unsubscribed_phones_are_cleaned_up(app, user):
    sender = FakeSender(result="gone")     # the push service says: this phone is gone
    run(app, "2026-11-19T20:00:00", sender)
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 0


# ── Pages ───────────────────────────────────────────────────────
def test_settings_says_not_set_up_without_keys(client, auth):
    auth.register_and_login()
    html = client.get("/settings/").get_data(as_text=True)
    assert "Daily reminder" in html
    assert "aren't set up on this server" in html
    assert 'id="push-panel"' not in html


def test_settings_shows_device_buttons_with_keys(client, auth, push_on):
    auth.register_and_login()
    html = client.get("/settings/").get_data(as_text=True)
    assert 'id="push-panel"' in html
    assert 'data-key="BPublic"' in html
    assert "VAPID_PRIVATE_KEY" not in html and '"private"' not in html


def test_today_shows_reminder_tip_until_set(client, auth, push_on):
    auth.register_and_login()
    assert "Turn on a daily reminder" in client.get("/").get_data(as_text=True)
    client.post("/reminders/time", data={"reminder_time": "20:00"})
    assert "Turn on a daily reminder" not in client.get("/").get_data(as_text=True)


def test_no_tip_when_server_has_no_keys(client, auth):
    auth.register_and_login()
    assert "Turn on a daily reminder" not in client.get("/").get_data(as_text=True)


def test_test_button_sends_to_your_devices(client, user, push_on, monkeypatch):
    sender = FakeSender()
    monkeypatch.setattr(reminders, "send_push", sender)
    assert client.post("/reminders/test", json={}).get_json() == {"sent": 1}
    assert len(sender.sent) == 1


def test_test_button_without_keys(client, user):
    assert client.post("/reminders/test", json={}).status_code == 503


def test_reminder_actions_dont_unlock_badges(client, user, monkeypatch):
    calls = []
    monkeypatch.setattr("achievements.check_achievements", lambda uid: calls.append(uid) or [])
    client.post("/reminders/time", data={"reminder_time": "20:00"})
    assert calls == []


def test_service_worker_shows_notifications(client):
    js = client.get("/sw.js").get_data(as_text=True)
    assert 'addEventListener("push"' in js
    assert 'addEventListener("notificationclick"' in js
