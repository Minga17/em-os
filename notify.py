"""Small pop-up messages ("toasts") for instant (htmx) actions.

A route calls toast("...") while it works. After the request, send_toasts()
puts them in the HX-Trigger header, and static/js/app.js shows them on screen.
"""
import json

from flask import g, request


def toast(message):
    g.setdefault("toasts", []).append(message)


def send_toasts(response):
    messages = g.pop("toasts", [])
    if messages and request.headers.get("HX-Request"):
        response.headers["HX-Trigger"] = json.dumps({"toast": {"messages": messages}})
    return response
