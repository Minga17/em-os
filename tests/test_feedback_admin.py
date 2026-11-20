"""Feedback button, private analytics, owner stats page and backups."""
import io
import sqlite3
from datetime import datetime, timezone

import pytest

from backup import claim_today, run_backup


def connect(app):
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    return db


def outbox(app):
    return app.extensions.get("outbox", [])


@pytest.fixture
def owner(app, client, auth):
    """Logged in as the app owner, with a confirmed email."""
    app.config["ADMIN_EMAILS"] = "Ema@Example.com, other@example.com"
    auth.register_and_login()
    with connect(app) as db:
        db.execute("UPDATE users SET email_verified = 1")
    return client


# ── Feedback ────────────────────────────────────────────────────
def test_feedback_saved_and_owner_emailed(app, client, auth):
    app.config["ADMIN_EMAILS"] = "owner@example.com"
    auth.register_and_login()
    response = client.post("/feedback/", data={"kind": "idea", "message": "Weekday-only habits please",
                                               "page": "https://em-os.onrender.com/habits/?x=1"},
                           follow_redirects=True)
    assert "Thank you" in response.get_data(as_text=True)
    with connect(app) as db:
        row = db.execute("SELECT kind, message, page FROM feedback").fetchone()
    assert tuple(row) == ("idea", "Weekday-only habits please", "/habits/")
    alert = outbox(app)[-1]
    assert alert["To"] == "owner@example.com"
    assert "Weekday-only habits please" in alert.get_body(("plain",)).get_content()


@pytest.mark.parametrize("data, error", [
    ({"kind": "spam", "message": "hi"}, "what kind"),
    ({"kind": "idea", "message": "   "}, "write a message"),
    ({"kind": "idea", "message": "x" * 2001}, "under 2000"),
])
def test_bad_feedback_rejected(app, client, auth, data, error):
    auth.register_and_login()
    assert error in client.post("/feedback/", data=data).get_data(as_text=True)
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0


def test_feedback_daily_limit(app, client, auth):
    auth.register_and_login()
    for i in range(12):
        client.post("/feedback/", data={"kind": "idea", "message": f"idea {i}"})
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 10


def test_feedback_needs_login(client):
    assert client.get("/feedback/").status_code == 302


def test_feedback_link_in_menu(client, auth):
    auth.register_and_login()
    assert "Send feedback" in client.get("/").get_data(as_text=True)


# ── Private analytics ───────────────────────────────────────────
def test_signup_and_visits_count_one_day_each(app, client, auth):
    auth.register_and_login()
    for _ in range(5):
        client.get("/")
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM activity_days").fetchone()[0] == 1


def test_old_history_is_backfilled(tmp_path):
    """Existing users get their past check-in days counted after the update."""
    from app import create_app
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO users (name, email, password_hash, created_at) VALUES ('Old', 'o@x.com', 'h', '2026-10-01 10:00:00');
    """)
    db.close()
    create_app({"TESTING": True, "DATABASE": path})
    db = sqlite3.connect(path)
    db.execute("INSERT INTO checkins (user_id, log_date, energy, mood, focus, score) VALUES (1, '2026-10-05', 3, 3, 3, 50)")
    db.commit()
    db.execute("DELETE FROM app_state")            # pretend the backfill hasn't run yet
    db.execute("DELETE FROM activity_days")
    db.commit()
    db.close()
    create_app({"TESTING": True, "DATABASE": path})
    db = sqlite3.connect(path)
    days = [r[0] for r in db.execute("SELECT day FROM activity_days ORDER BY day")]
    assert days == ["2026-10-01", "2026-10-05"]
    columns = {r[1] for r in db.execute("PRAGMA table_info(users)")}
    assert {"email_verified", "email_sent_at", "session_version"} <= columns


def test_deleting_account_removes_feedback_and_activity(app, client, auth):
    auth.register_and_login()
    client.post("/feedback/", data={"kind": "love", "message": "Great app"})
    client.post("/settings/delete", data={"confirm": "DELETE", "password": "password123"})
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM activity_days").fetchone()[0] == 0


# ── Owner stats page ────────────────────────────────────────────
def test_owner_sees_stats(app, owner):
    owner.post("/feedback/", data={"kind": "problem", "message": "Ring looks odd"})
    html = owner.get("/admin/").get_data(as_text=True)
    assert "Owner stats" in html and "Ring looks odd" in html
    assert "Owner stats" in owner.get("/").get_data(as_text=True)    # menu link


def test_stats_hidden_from_everyone_else(app, client, auth):
    app.config["ADMIN_EMAILS"] = "boss@example.com"
    assert client.get("/admin/").status_code == 404                  # logged out
    auth.register_and_login()
    assert client.get("/admin/").status_code == 404                  # normal user
    assert "Owner stats" not in client.get("/").get_data(as_text=True)


def test_owner_email_must_be_confirmed(app, client, auth):
    """Otherwise someone could sign up with the owner's address first."""
    app.config["ADMIN_EMAILS"] = "ema@example.com"
    auth.register_and_login()
    assert client.get("/admin/").status_code == 404


def test_return_rates(app, owner):
    """3 people started 10 days ago; 2 came back on day 1, 1 on day 7."""
    with connect(app) as db:
        for uid in (2, 3, 4):
            db.execute("INSERT INTO users (id, name, email, password_hash) VALUES (?, 'U', ?, 'h')",
                       (uid, f"u{uid}@x.com"))
            db.execute("INSERT INTO activity_days VALUES (?, date('now', '-10 day'))", (uid,))
        db.execute("INSERT INTO activity_days VALUES (2, date('now', '-9 day'))")
        db.execute("INSERT INTO activity_days VALUES (3, date('now', '-9 day'))")
        db.execute("INSERT INTO activity_days VALUES (2, date('now', '-3 day'))")
        db.execute("INSERT INTO checkins (user_id, log_date, energy, mood, focus, score) "
                   "VALUES (2, date('now', '-3 day'), 3, 3, 3, 50)")
    from admin import return_rate
    with app.app_context(), connect(app) as db:
        day1, day7, day30 = (return_rate(db, n) for n in (1, 7, 30))
    assert (day1["eligible"], day1["came_back"], day1["percent"]) == (3, 2, 67)   # owner started today
    assert (day7["came_back"], day7["checked_in"], day7["percent"]) == (1, 1, 33)
    assert day30["eligible"] == 0 and day30["percent"] is None
    assert "67%" in owner.get("/admin/").get_data(as_text=True)


# ── Backups ─────────────────────────────────────────────────────
def test_only_one_worker_claims_the_backup(app):
    with app.app_context(), connect(app) as db:
        assert claim_today(db, "last_backup_on", "2026-11-20") is True
        assert claim_today(db, "last_backup_on", "2026-11-20") is False
        assert claim_today(db, "last_backup_on", "2026-11-21") is True


def test_backup_is_encrypted_and_restorable(app, owner):
    try:
        import pyzipper
    except ImportError:                             # not installed, or blocked by Windows
        pytest.skip("pyzipper isn't available on this computer (it is on Render)")
    app.config["BACKUP_PASSWORD"] = "correct horse battery"
    with app.app_context(), connect(app) as db:
        assert run_backup(db, datetime(2026, 11, 20, 8, 59, tzinfo=timezone.utc)) is False  # too early
        assert run_backup(db, datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)) is True
        assert run_backup(db, datetime(2026, 11, 20, 9, 1, tzinfo=timezone.utc)) is False  # once a day

    emails = [m for m in outbox(app) if "backup" in m["Subject"]]
    assert sorted(m["To"] for m in emails) == ["ema@example.com", "other@example.com"]
    attachment = next(emails[0].iter_attachments())
    assert attachment.get_filename() == "em-os-backup-2026-11-20.zip"
    data = attachment.get_content()

    with pyzipper.AESZipFile(io.BytesIO(data)) as archive:
        with pytest.raises(RuntimeError):
            archive.read("em_os.db")                # no password: can't read it
        archive.setpassword(b"correct horse battery")
        restored = archive.read("em_os.db")
    path = app.config["DATABASE"] + ".restored"
    with open(path, "wb") as f:
        f.write(restored)
    assert sqlite3.connect(path).execute("SELECT email FROM users").fetchone()[0] == "ema@example.com"


def test_no_password_means_no_backup(app):
    from backup import backup_ready
    app.config.update(ADMIN_EMAILS="o@x.com", MAIL_USERNAME="u", MAIL_PASSWORD="p", BACKUP_PASSWORD="")
    assert backup_ready(app) is False
