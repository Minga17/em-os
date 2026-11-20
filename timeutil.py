"""Dates and times in the logged-in user's own time zone.

The server's clock can be anywhere in the world, so never use date.today()
or datetime.now() directly. Use these helpers instead.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from flask import g

DEFAULT_TZ = "UTC"


def all_timezones():
    """Sorted list of real place-based zones for the dropdown (plus UTC)."""
    zones = {z for z in available_timezones()
             if "/" in z and not z.startswith(("Etc/", "SystemV/", "US/", "posix/", "right/"))}
    return ["UTC"] + sorted(zones)


def is_valid_timezone(name):
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False


def user_tz():
    """The logged-in user's ZoneInfo, or UTC if no one is logged in."""
    user = g.get("user")
    name = user["timezone"] if user and user["timezone"] else DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def user_now() -> datetime:
    """The current moment, on the user's clock."""
    return datetime.now(user_tz())


def user_today() -> date:
    """Today's date where the user lives."""
    return datetime.now(user_tz()).date()


def user_now_str() -> str:
    """The user's local date and time as text, e.g. '2026-11-17T23:19:05'."""
    return datetime.now(user_tz()).replace(tzinfo=None).isoformat(timespec="seconds")


def local_today_for(name) -> date:
    """Today's date in a given time zone (when no one is logged in yet)."""
    try:
        return datetime.now(ZoneInfo(name or DEFAULT_TZ)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(ZoneInfo(DEFAULT_TZ)).date()
