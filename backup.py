"""Daily database backup, emailed to the owner as an encrypted zip.

Online, once a day (around 3 AM Chicago time) EM OS:
1. takes a safe copy of the database (even while people are using the app),
2. puts it in a zip locked with BACKUP_PASSWORD (AES-256),
3. emails it to everyone in ADMIN_EMAILS.

Without BACKUP_PASSWORD no backup is made: user data is never sent unencrypted.

To open a backup: install 7-Zip (free), right-click the zip > 7-Zip > Extract,
and type your BACKUP_PASSWORD. Inside is em_os.db, the whole database.
"""
import io
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

from flask import current_app

from mailer import admin_emails, mail_configured, send_email

BACKUP_HOUR_UTC = 9                     # 9:00 UTC = 3:00 AM in Chicago, when nobody's using it
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # Gmail refuses attachments over 25 MB


def load_pyzipper():
    try:
        import pyzipper
        return pyzipper
    except Exception:                      # not installed, or blocked by the computer
        return None


def backup_ready(app=None):
    app = app or current_app
    return bool(app.config.get("BACKUP_PASSWORD") and admin_emails(app)
                and mail_configured(app) and load_pyzipper())


def make_backup_zip(database_path, password):
    """Safe copy of the live database, inside an AES-encrypted zip. Returns bytes."""
    pyzipper = load_pyzipper()
    with tempfile.TemporaryDirectory() as folder:
        copy_path = os.path.join(folder, "em_os.db")
        source = sqlite3.connect(database_path)
        target = sqlite3.connect(copy_path)
        with target:
            source.backup(target)          # SQLite's own safe copy, works while the app is busy
        source.close()
        target.close()

        buffer = io.BytesIO()
        with pyzipper.AESZipFile(buffer, "w", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as archive:
            archive.setpassword(password.encode())
            archive.write(copy_path, "em_os.db")
        return buffer.getvalue()


def claim_today(db, key, today):
    """True for exactly one worker per day, even if two run at the same moment."""
    db.execute("INSERT OR IGNORE INTO app_state (key, value) VALUES (?, '')", (key,))
    claimed = db.execute("UPDATE app_state SET value = ? WHERE key = ? AND value != ?",
                         (today, key, today)).rowcount
    db.commit()
    return bool(claimed)


def send_backup(today_label):
    data = make_backup_zip(current_app.config["DATABASE"], current_app.config["BACKUP_PASSWORD"])
    size_mb = len(data) / 1024 / 1024
    for owner in admin_emails():
        if len(data) > MAX_ATTACHMENT_BYTES:
            send_email(owner, f"⚠️ EM OS backup too big for email ({size_mb:.1f} MB)",
                       "The database is now too big to email. Time to switch backups to "
                       "cloud storage (Backblaze B2 or Cloudflare R2). Render's disk "
                       "snapshots are still running.")
            current_app.logger.error("Backup too big to email: %.1f MB", size_mb)
            continue
        sent = send_email(owner, f"EM OS backup {today_label}",
                          f"Your daily EM OS backup is attached ({size_mb:.2f} MB).\n\n"
                          "Open it with 7-Zip and your BACKUP_PASSWORD. Keep these emails "
                          "private: they contain all your users' data.\n",
                          attachments=[(f"em-os-backup-{today_label}.zip", data, "application/zip")])
        if not sent:
            current_app.logger.error("Backup email to %s failed", owner)
    return len(data)


def run_backup(db, now_utc=None):
    """Called every minute by the background loop. Backs up once a day."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.hour < BACKUP_HOUR_UTC:
        return False
    today = now_utc.date().isoformat()
    if not claim_today(db, "last_backup_on", today):
        return False
    send_backup(today)
    return True
