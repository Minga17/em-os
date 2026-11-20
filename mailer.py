"""Sending email: password resets, email confirmation, feedback alerts, backups.

Uses plain SMTP from Python's standard library, so nothing extra to install.
Online it sends through Gmail with an "app password" (see the README steps).

If email isn't set up (for example on your laptop), nothing breaks: the
email is printed in the terminal instead, so you can still click the links.
"""
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app, request


def mail_configured(app=None):
    app = app or current_app
    return bool(app.config.get("MAIL_USERNAME") and app.config.get("MAIL_PASSWORD"))


def admin_emails(app=None):
    """Owner email addresses from the ADMIN_EMAILS setting (comma separated)."""
    app = app or current_app
    return [e.strip().lower() for e in app.config.get("ADMIN_EMAILS", "").split(",") if e.strip()]


def external_url(path):
    """Full link for emails, e.g. https://em-os.onrender.com/auth/reset/abc.

    APP_URL is set online so links can never point anywhere else, even if
    someone sends the server a fake Host header.
    """
    base = current_app.config.get("APP_URL") or request.host_url
    return base.rstrip("/") + path


def build_message(app, to, subject, text, attachments=()):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr(("EM OS", app.config.get("MAIL_USERNAME") or "em-os@localhost"))
    message["To"] = to
    message.set_content(text)
    for filename, data, mimetype in attachments:
        maintype, subtype = mimetype.split("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return message


def deliver(app, message):
    """Actually talk to the mail server. Returns True if it was sent."""
    if app.config.get("TESTING"):
        app.extensions.setdefault("outbox", []).append(message)
        return True
    if not mail_configured(app):
        app.logger.warning("Email isn't set up, so here it is instead:\nTo: %s\nSubject: %s\n\n%s",
                           message["To"], message["Subject"],
                           message.get_body(("plain",)).get_content())
        return False
    try:
        with smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
            smtp.send_message(message)
        return True
    except (smtplib.SMTPException, OSError) as error:
        app.logger.error("Email to %s failed: %s", message["To"], error)
        return False


def send_email(to, subject, text, attachments=(), background=False):
    """Send one email. With background=True the page doesn't wait for Gmail."""
    app = current_app._get_current_object()
    if not to or "\n" in to or "\r" in to:
        return False
    try:
        message = build_message(app, to, subject, text, attachments)
    except ValueError:               # e.g. a weird character in the address
        return False
    if background and not app.config.get("TESTING"):
        threading.Thread(target=deliver, args=(app, message), daemon=True).start()
        return True
    return deliver(app, message)
