"""Pop-up messages ("toasts") and celebrations (confetti, level-up screen).

A route calls toast("...") or celebrate(...) while it works. After the request:
- instant (htmx) actions get them in the HX-Trigger header, right away;
- normal page loads keep them in the session and show them on the next page.
static/js/app.js shows them either way.
"""
import json

from flask import g, request, session


def toast(message):
    g.setdefault("toasts", []).append(message)


def celebrate(kind, **details):
    """kind: 'confetti' or 'levelUp' (with level, title, to_next...)."""
    g.setdefault("celebrations", {})[kind] = details


def send_toasts(response):
    messages = g.pop("toasts", [])
    celebrations = g.pop("celebrations", {})
    if request.headers.get("HX-Request"):
        events = dict(celebrations)
        if messages:
            events["toast"] = {"messages": messages}
        if events:
            response.headers["HX-Trigger"] = json.dumps(events)
    elif celebrations:
        session["celebrate"] = {**session.get("celebrate", {}), **celebrations}
    return response


def pending_celebrations():
    """For base.html: celebrations waiting for this page (shown once)."""
    return session.pop("celebrate", None) or {}
