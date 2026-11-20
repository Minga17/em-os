"""Password reset, email confirmation and 'log out of all devices'."""
import re
import sqlite3

import pytest

from mailer import external_url


def connect(app):
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    return db


def outbox(app):
    return app.extensions.get("outbox", [])


def link_in(message):
    body = message.get_body(("plain",)).get_content()
    return re.search(r"https?://\S+", body).group(0)


def path_of(link):
    return "/" + link.split("/", 3)[3]


def allow_next_email(app):
    """Skip the one-email-per-minute wait in tests."""
    with connect(app) as db:
        db.execute("UPDATE users SET email_sent_at = NULL")


# ── Email confirmation ──────────────────────────────────────────
def test_signup_sends_confirmation_email(app, auth):
    auth.register()
    assert len(outbox(app)) == 1
    message = outbox(app)[0]
    assert message["To"] == "ema@example.com"
    assert "Confirm" in message["Subject"]
    assert "/auth/verify/" in link_in(message)


def test_confirming_email(app, client, auth):
    auth.register_and_login()
    assert "Please confirm your email" in client.get("/").get_data(as_text=True)
    response = client.get(path_of(link_in(outbox(app)[0])), follow_redirects=True)
    assert "Email confirmed" in response.get_data(as_text=True)
    assert "Please confirm your email" not in client.get("/").get_data(as_text=True)
    with connect(app) as db:
        assert db.execute("SELECT email_verified FROM users").fetchone()[0] == 1


def test_fake_or_changed_confirmation_link_fails(app, client, auth):
    auth.register()
    path = path_of(link_in(outbox(app)[0]))
    response = client.get(path[:-3] + "abc", follow_redirects=True)
    assert "expired" in response.get_data(as_text=True)
    with connect(app) as db:
        assert db.execute("SELECT email_verified FROM users").fetchone()[0] == 0


def test_confirmation_link_from_another_app_secret_fails(app, auth):
    """Links are signed with SECRET_KEY: nobody can make their own."""
    from itsdangerous import URLSafeTimedSerializer
    auth.register()
    forged = URLSafeTimedSerializer("wrong-key", salt="verify-email").dumps(
        {"uid": 1, "email": "ema@example.com"})
    app.test_client().get(f"/auth/verify/{forged}")
    with connect(app) as db:
        assert db.execute("SELECT email_verified FROM users").fetchone()[0] == 0


def test_confirmation_link_for_another_address_fails(app, client, auth):
    """A link made for an old address can't confirm a different one."""
    from auth import make_token
    auth.register()
    with app.test_request_context():
        token = make_token("verify-email", {"uid": 1, "email": "someone-else@example.com"})
    client.get(f"/auth/verify/{token}")
    with connect(app) as db:
        assert db.execute("SELECT email_verified FROM users").fetchone()[0] == 0


def test_resend_waits_a_minute(app, client, auth):
    auth.register_and_login()
    client.post("/auth/verify/resend")                      # too soon after sign-up
    assert len(outbox(app)) == 1
    allow_next_email(app)
    client.post("/auth/verify/resend")
    assert len(outbox(app)) == 2


def test_signup_rejects_bad_email(app, auth):
    response = auth.register(email="not-an-email")
    assert "valid email" in response.get_data(as_text=True)
    assert outbox(app) == []


# ── Forgot password ─────────────────────────────────────────────
def reset_link(app, client, email="ema@example.com"):
    allow_next_email(app)
    client.post("/auth/forgot", data={"email": email})
    return path_of(link_in(outbox(app)[-1]))


def test_password_reset_full_flow(app, client, auth):
    auth.register()
    path = reset_link(app, client)
    assert "Choose a new password" in client.get(path).get_data(as_text=True)
    response = client.post(path, data={"new_password": "brandnew99", "confirm_password": "brandnew99"},
                           follow_redirects=True)
    assert "Password changed" in response.get_data(as_text=True)
    assert "Incorrect" in auth.login(password="password123").get_data(as_text=True)
    auth.login(password="brandnew99")
    assert client.get("/").status_code == 200
    with connect(app) as db:     # clicking the emailed link also proves the email is theirs
        assert db.execute("SELECT email_verified FROM users").fetchone()[0] == 1


def test_reset_link_works_only_once(app, client, auth):
    auth.register()
    path = reset_link(app, client)
    client.post(path, data={"new_password": "brandnew99", "confirm_password": "brandnew99"})
    response = client.get(path, follow_redirects=True)
    assert "expired or was already used" in response.get_data(as_text=True)


def test_reset_link_expires(app, client, auth, monkeypatch):
    auth.register()
    path = reset_link(app, client)
    monkeypatch.setattr("auth.RESET_LINK_HOURS", -1)
    response = client.get(path, follow_redirects=True)
    assert "expired" in response.get_data(as_text=True)


def test_reset_checks_new_password(app, client, auth):
    auth.register()
    path = reset_link(app, client)
    assert "at least 8" in client.post(path, data={"new_password": "short", "confirm_password": "short"}
                                       ).get_data(as_text=True)
    assert "passwords don&#39;t match" in client.post(path, data={"new_password": "longenough1",
                                                    "confirm_password": "longenough2"}).get_data(as_text=True)


def test_forgot_gives_same_answer_for_unknown_email(app, client, auth):
    """Nobody can use this page to find out who has an account."""
    auth.register()
    client.get("/auth/login")                   # clear the "Account created" message
    count = len(outbox(app))
    known = client.post("/auth/forgot", data={"email": "ema@example.com"}, follow_redirects=True)
    unknown = client.post("/auth/forgot", data={"email": "nobody@example.com"}, follow_redirects=True)
    assert "If that email has an EM OS account" in known.get_data(as_text=True)
    assert known.get_data() == unknown.get_data()      # exactly the same page
    assert len(outbox(app)) == count            # the known one was within the 1-minute wait
    allow_next_email(app)
    client.post("/auth/forgot", data={"email": "nobody@example.com"})
    assert len(outbox(app)) == count


def test_forgot_cant_spam_an_inbox(app, client, auth):
    auth.register()
    allow_next_email(app)
    for _ in range(5):
        client.post("/auth/forgot", data={"email": "ema@example.com"})
    assert len(outbox(app)) == 2                # 1 confirmation + only 1 reset


def test_reset_logs_out_other_devices(app, auth):
    auth.register()
    phone = app.test_client()
    phone.post("/auth/login", data={"email": "ema@example.com", "password": "password123"})
    assert phone.get("/").status_code == 200
    laptop = app.test_client()
    path = reset_link(app, laptop)
    laptop.post(path, data={"new_password": "brandnew99", "confirm_password": "brandnew99"})
    assert phone.get("/").status_code == 302    # phone was logged out


def test_links_use_app_url_when_set(app):
    app.config["APP_URL"] = "https://em-os.onrender.com/"
    with app.test_request_context(headers={"Host": "evil.example.com"}):
        assert external_url("/auth/reset/x") == "https://em-os.onrender.com/auth/reset/x"


# ── Log out of all devices ──────────────────────────────────────
def test_logout_other_devices_keeps_this_one(app, auth):
    auth.register()
    phone, laptop = app.test_client(), app.test_client()
    for device in (phone, laptop):
        device.post("/auth/login", data={"email": "ema@example.com", "password": "password123"})
    laptop.post("/settings/logout-others")
    assert laptop.get("/").status_code == 200
    assert phone.get("/").status_code == 302


def test_password_change_logs_out_other_devices(app, auth):
    auth.register()
    phone, laptop = app.test_client(), app.test_client()
    for device in (phone, laptop):
        device.post("/auth/login", data={"email": "ema@example.com", "password": "password123"})
    laptop.post("/settings/password", data={"current_password": "password123",
                                            "new_password": "brandnew99", "confirm_password": "brandnew99"})
    assert laptop.get("/").status_code == 200
    assert phone.get("/").status_code == 302


def test_logout_others_needs_login(client):
    assert client.post("/settings/logout-others").status_code == 302


def test_old_logins_from_before_this_update_still_work(app, client, auth):
    """People already logged in shouldn't be kicked out by the update."""
    auth.register()
    with client.session_transaction() as session:
        session["user_id"] = 1                  # an old cookie: no "sv" in it
    assert client.get("/").status_code == 200


@pytest.mark.parametrize("bad", ["a@b.com\nBcc: x@y.com", "x@y.com\r\nSubject: hi"])
def test_email_header_injection_blocked(app, bad):
    from mailer import send_email
    with app.app_context():
        assert send_email(bad, "Hi", "text") is False
