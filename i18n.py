"""Languages: English, Spanish, French, Portuguese.

How it works
  • Text is written in English in the code:      _("Today's habits")
  • With values:                                  _("Habit added: {name}", name=habit)
  • Counting things (1 day / 2 days):             _n("{n} day", "{n} days", count)
  • Lists of options defined once at the top:     N_("Every day")  … then _(label) when shown
  • translations/es.json (fr, pt) maps each English text to its translation.
    Anything missing falls back to English, so a forgotten line never breaks a page.

Which language?
  1. The one you picked in Settings
  2. Otherwise your phone/browser language (Accept-Language)
  3. Otherwise English
Emails and notifications use the language saved on your account.
"""
import json
import os
import re
from contextlib import contextmanager
from datetime import date
from functools import lru_cache

from flask import g, has_request_context, request, session
from markupsafe import Markup

LANGUAGES = {"en": "English", "es": "Español", "fr": "Français", "pt": "Português"}
DEFAULT = "en"
BABEL_LOCALES = {"en": "en_US", "es": "es", "fr": "fr", "pt": "pt_BR"}
HERE = os.path.dirname(os.path.abspath(__file__))


@lru_cache(maxsize=None)
def catalog(language):
    if language == DEFAULT:
        return {}
    with open(os.path.join(HERE, "translations", f"{language}.json"), encoding="utf-8") as f:
        return json.load(f)


def browser_language():
    header = request.headers.get("Accept-Language", "") if has_request_context() else ""
    for part in header.split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGUAGES:
            return code
    return DEFAULT


def current_language():
    if not has_request_context():
        return DEFAULT
    if g.get("forced_language") in LANGUAGES:
        return g.forced_language
    user = g.get("user")
    if user is not None and "language" in user.keys() and user["language"] in LANGUAGES:
        return user["language"]
    if session.get("lang") in LANGUAGES:
        return session["lang"]
    return browser_language()


def fill(text, values):
    if not values:
        return text
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return text


def translate(language, text, **values):
    """For emails and notifications, where the language isn't the visitor's."""
    found = catalog(language).get(text) if language in LANGUAGES else None
    if isinstance(found, dict):
        found = found.get("other")
    return fill(found or text, values)


def gettext(text, **values):
    return translate(current_language(), text, **values)


def plural_form(language, n):
    if language in ("fr", "pt"):
        return "one" if n in (0, 1) else "other"
    return "one" if n == 1 else "other"


def translate_n(language, singular, plural, n, **values):
    values.setdefault("n", n)
    found = catalog(language).get(singular) if language in LANGUAGES else None
    if isinstance(found, dict):
        text = found.get(plural_form(language, n)) or found.get("other")
    else:
        text = singular if plural_form("en", n) == "one" else plural
    return fill(text, values)


def ngettext(singular, plural, n, **values):
    return translate_n(current_language(), singular, plural, n, **values)


@contextmanager
def force_language(language):
    """Use one language for a moment, e.g. to confirm a language change in the new language."""
    before = g.get("forced_language")
    g.forced_language = language
    try:
        yield
    finally:
        g.forced_language = before


def N_(text):
    """Marks text to translate later (e.g. option lists). Returns it unchanged."""
    return text


_ = gettext
_n = ngettext


# ── Dates in the right language ─────────────────────────────────
DATE_STYLES = {
    "long": "MMMMEEEEd",          # Sunday, November 22
    "medium": "MMMEd",            # Sun, Nov 22
    "day_month": "MMMd",          # Nov 22
    "full": "yMMMMEEEEd",         # Sunday, November 22, 2026
    "month_year": "yMMMM",        # November 2026
    "weekday": "EEEE",            # Sunday
    "short_weekday": "EEE",       # Sun
}


PATTERNS = {"weekday": "EEEE", "short_weekday": "EEE", "MMM": "LLL"}      # exact, not "closest match"
LONG = {"en": "EEEE, MMMM d", "es": "EEEE, d 'de' MMMM", "fr": "EEEE d MMMM", "pt": "EEEE, d 'de' MMMM"}


def format_day(d, style="medium", language=None):
    from babel.dates import format_date, format_skeleton
    language = language or current_language()
    if style in PATTERNS:
        text = format_date(d, PATTERNS[style], locale=BABEL_LOCALES[language])
    elif style == "long":
        text = format_date(d, LONG[language], locale=BABEL_LOCALES[language])
    elif style == "full":
        text = format_date(d, "full", locale=BABEL_LOCALES[language])
    else:
        text = format_skeleton(DATE_STYLES.get(style, style), d, locale=BABEL_LOCALES[language])
    if style in ("long", "full", "weekday", "short_weekday", "month_year", "MMM"):
        text = text[:1].upper() + text[1:]
    return text.rstrip(".") if style in ("short_weekday", "MMM") else text


def weekday_names(language=None, width="abbreviated"):
    from babel.dates import get_day_names
    names = get_day_names(width, locale=BABEL_LOCALES[language or current_language()])
    return [names[i].capitalize() for i in range(7)]


def js_strings(keys):
    """Text the page's JavaScript needs, already translated."""
    return {key: gettext(key) for key in keys}


# Words stored in the database (statuses, data sets), shown in the person's language
NAMES = {
    "todo": N_("To do"), "doing": N_("In progress"), "done": N_("Done"),
    "active": N_("Active"), "achieved": N_("Achieved"), "paused": N_("Paused"), "dropped": N_("Dropped"),
    "planned": N_("Planned"), "complete": N_("Complete"),
    "checkins": N_("Check-ins"), "habits": N_("Habits"), "tasks": N_("Tasks"), "goals": N_("Goals"),
    "weekly_reviews": N_("Weekly reviews"),
}


def name_of(value):
    return gettext(NAMES[value]) if value in NAMES else str(value).replace("_", " ").capitalize()


PLACEHOLDER = re.compile(r"\{(\w+)\}")


def placeholders(text):
    return sorted(set(PLACEHOLDER.findall(text)))


# Text used by static/js/app.js (sent to the page already translated)
JS_TEXTS = [
    N_('On iPhone: tap Share → Add to Home Screen, open EM OS from your Home Screen, then come back here.'),
    N_("This browser can't show notifications. Try Chrome, Edge, Firefox or Safari."),
    N_('✅ Reminders are on for this device.'),
    N_('Reminders are off for this device.'),
    N_('Something went wrong.'),
    N_('🔔 Reminders are on!'),
    N_('Reminders turned off for this device.'),
    N_('Test sent! Check your notifications.'),
    N_('No devices got it. Try turning reminders off and on.'),
    N_('Level up'),
    N_('LEVEL UP!'),
    N_('Keep going →'),
    N_('My week on EM OS'),
    N_('📸 Image saved'),
    N_('Close menu'),
    N_('Open menu'),
    N_('Notifications are blocked for EM OS. Allow them in your browser or phone settings, then reload this page.'),
    N_('Focus'),
    N_('You reached level {level} with {xp} XP. {to_next} XP to the next level.'),
    N_('🔓 New: {thing}'),
]


# ── In page templates: safe HTML ───────────────────────────────
def _page_text(text):
    """Escape < > & " in the (trusted) text but keep apostrophes readable."""
    return Markup(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&#34;"))


def _page_fill(text, values):
    try:
        return _page_text(text).format(**values)          # Markup.format escapes every value
    except (KeyError, IndexError, ValueError):
        return _page_text(text)


def page_gettext(text, **values):
    language = current_language()
    found = catalog(language).get(text) if language in LANGUAGES else None
    if isinstance(found, dict):
        found = found.get("other")
    return _page_fill(found or text, values)


def page_ngettext(singular, plural, n, **values):
    language = current_language()
    values.setdefault("n", n)
    found = catalog(language).get(singular) if language in LANGUAGES else None
    if isinstance(found, dict):
        text = found.get(plural_form(language, n)) or found.get("other")
    else:
        text = singular if n == 1 else plural
    return _page_fill(text, values)


def init_app(app):
    app.jinja_env.globals.update(_=page_gettext, _n=page_ngettext, LANGUAGES=LANGUAGES, current_language=current_language)
    app.jinja_env.filters["day"] = format_day
    app.jinja_env.filters["name"] = name_of
    app.jinja_env.filters["isodate"] = lambda text: date.fromisoformat(text)
    app.jinja_env.globals["js_texts"] = lambda: {k: gettext(k) for k in JS_TEXTS} if current_language() != DEFAULT else {}

    @app.before_request
    def pick_language_from_link():
        """?lang=fr on any page (used by the language links on the login page)."""
        lang = request.args.get("lang")
        if lang in LANGUAGES:
            session["lang"] = lang
