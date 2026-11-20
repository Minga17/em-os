"""Background jobs: one small loop that wakes up every minute.

- Daily reminders (if notification keys are set)
- Daily backup (if backups are set up)

It only starts online. On your laptop nothing runs in the background.
"""
import threading
import time
from datetime import datetime

from backup import backup_ready, run_backup
from db import get_db
from reminders import push_ready, run_reminders


def any_jobs(app):
    return push_ready(app) or backup_ready(app)


def run_due_jobs(app):
    with app.app_context():
        db = get_db()
        if push_ready(app):
            run_reminders(db)
        if backup_ready(app):
            run_backup(db)


def start_background_jobs(app):
    def loop():
        while True:
            time.sleep(60 - datetime.now().second)    # wake up at the start of each minute
            try:
                run_due_jobs(app)
            except Exception:
                app.logger.exception("Background job error")

    threading.Thread(target=loop, name="background-jobs", daemon=True).start()
