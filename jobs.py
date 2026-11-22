"""Background jobs: one small loop that wakes up every minute.

- Finish focus sessions on time (and notify, if notification keys are set)
- Daily reminders (if notification keys are set)
- Daily backup (if backups are set up)

It runs online. On your laptop (`flask run --debug`) it runs too, which is fine.
"""
import threading
import time
from datetime import datetime

from backup import backup_ready, run_backup
from db import get_db
from focus import finish_overdue
from reminders import push_ready, run_reminders, send_push


def any_jobs(app):
    return True             # focus sessions always need finishing on time


def run_due_jobs(app):
    with app.app_context():
        db = get_db()
        finish_overdue(db, sender=send_push if push_ready(app) else None)
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
