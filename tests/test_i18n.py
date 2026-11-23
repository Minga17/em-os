"""Languages: every text is translated, the right language is picked, and nothing breaks."""
import json
import os
import re
import sqlite3
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "translations"))
from extract import keys  # noqa: E402

from i18n import LANGUAGES, format_day, translate, translate_n  # noqa: E402
from timeutil import local_today_for  # noqa: E402

LANGS = ["es", "fr", "pt"]
PLACEHOLDER = re.compile(r"\{(\w+)\}")
ALL_KEYS = keys()


def catalog(lang):
    with open(os.path.join(ROOT, "translations", f"{lang}.json"), encoding="utf-8") as f:
        return json.load(f)


def connect(app):
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    return db


def set_language(app, lang):
    with connect(app) as db:
        db.execute("UPDATE users SET language = ?", (lang,))


# ── The dictionaries ────────────────────────────────────────────
@pytest.mark.parametrize("lang", LANGS)
def test_every_text_is_translated(lang):
    found = catalog(lang)
    missing = [k for k in ALL_KEYS if k not in found or not found[k]]
    assert not missing, f"{len(missing)} missing in {lang}.json, e.g. {missing[:5]}"


@pytest.mark.parametrize("lang", LANGS)
def test_placeholders_and_plurals_match(lang):
    found = catalog(lang)
    for key, plural in ALL_KEYS.items():
        value = found[key]
        if plural:
            assert isinstance(value, dict) and set(value) == {"one", "other"}, key
            texts = list(value.values())
        else:
            assert isinstance(value, str), key
            texts = [value]
        allowed = set(PLACEHOLDER.findall(key)) | set(PLACEHOLDER.findall(plural or "")) | {"n"}
        needed = allowed - {"n"}
        for text in texts:
            assert set(PLACEHOLDER.findall(text)) <= allowed, (key, text)
        assert needed <= set().union(*(set(PLACEHOLDER.findall(t)) for t in texts)), (key, texts)


@pytest.mark.parametrize("lang", LANGS)
def test_no_html_in_translations(lang):
    """Translations are shown as trusted text, so they must never contain HTML."""
    for value in catalog(lang).values():
        for text in (value.values() if isinstance(value, dict) else [value]):
            assert "<" not in text and ">" not in text.replace("→", ""), text


def test_no_leftover_keys():
    """Keys in the dictionaries that the app no longer uses (keeps the files tidy)."""
    for lang in LANGS:
        assert set(catalog(lang)) <= set(ALL_KEYS), lang


# ── Picking the language ────────────────────────────────────────
def test_browser_language_on_logged_out_pages(client):
    html = client.get("/auth/login", headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}).get_data(as_text=True)
    assert '<html lang="fr"' in html and "Mot de passe" in html
    html = client.get("/auth/login", headers={"Accept-Language": "de-DE,de;q=0.9"}).get_data(as_text=True)
    assert '<html lang="en"' in html and "Password" in html                 # not supported: English


def test_language_links_on_login_page(client):
    html = client.get("/auth/login?lang=pt").get_data(as_text=True)
    assert '<html lang="pt"' in html and "Senha" in html
    assert 'href="?lang=es"' in html
    assert "Senha" in client.get("/auth/register").get_data(as_text=True)      # remembered


def test_unknown_lang_link_ignored(client):
    assert '<html lang="en"' in client.get("/auth/login?lang=xx").get_data(as_text=True)


def test_new_account_keeps_browser_language(app, client, auth):
    client.get("/auth/login?lang=es")
    auth.register_and_login()
    with connect(app) as db:
        assert db.execute("SELECT language FROM users").fetchone()[0] == "es"
    html = client.get("/").get_data(as_text=True)
    assert "Hábitos de hoy" in html and '<html lang="es"' in html


def test_settings_language_picker(app, client, auth):
    auth.register_and_login()
    html = client.post("/settings/language", data={"language": "fr"}, follow_redirects=True).get_data(as_text=True)
    assert "Langue enregistrée." in html and '<option value="fr" lang="fr" selected>' in html
    assert "Habitudes du jour" in client.get("/").get_data(as_text=True)
    client.post("/settings/language", data={"language": ""})                    # "Same as my phone"
    html = client.get("/", headers={"Accept-Language": "pt-BR"}).get_data(as_text=True)
    assert "Hábitos de hoje" in html


def test_bad_language_rejected(app, client, auth):
    auth.register_and_login()
    client.post("/settings/language", data={"language": "es\" onload=\"x"})
    with connect(app) as db:
        assert db.execute("SELECT language FROM users").fetchone()[0] in (None, "en")


# ── Every page works in every language ──────────────────────────
PAGES = ["/", "/habits/", "/tasks/", "/checkin/", "/goals/", "/projects/", "/review/", "/dashboard/",
         "/achievements/", "/records/", "/insights/", "/recap/", "/focus/", "/settings/", "/feedback/",
         "/export/", "/import/", "/welcome/"]


@pytest.mark.parametrize("lang", LANGS)
def test_all_pages_render(app, client, auth, lang):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read", "icon": "📚"})
    client.post("/tasks/", data={"title": "Essay", "due_date": local_today_for("UTC").isoformat()})
    client.post("/checkin/quick", data={"energy": 3, "mood": 4, "focus": 5})
    set_language(app, lang)
    for page in PAGES:
        response = client.get(page)
        assert response.status_code == 200, page
        html = response.get_data(as_text=True)
        assert f'<html lang="{lang}"' in html, page
        assert "{" not in re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S), page


# ── Messages, emails, notifications ─────────────────────────────
def test_flash_messages_translated(app, client, auth):
    auth.register_and_login()
    set_language(app, "es")
    html = client.post("/habits/", data={"name": "Leer"}, follow_redirects=True).get_data(as_text=True)
    assert "Hábito añadido: ✅ Leer" in html


def test_user_text_is_still_escaped(app, client, auth):
    """Names go into translated sentences; they must never become HTML."""
    auth.register_and_login(name="<b>Ema</b>")
    set_language(app, "fr")
    auth.logout()
    html = auth.login().get_data(as_text=True) + client.get("/").get_data(as_text=True)
    assert "<b>Ema</b>" not in html and "&lt;b&gt;Ema&lt;/b&gt;" in html


def test_verification_email_in_browser_language(app, client):
    client.get("/auth/login?lang=pt")
    client.post("/auth/register", data={"name": "Ana", "email": "ana@example.com",
                                         "password": "password123", "timezone": "UTC"})
    message = app.extensions["outbox"][-1]
    body = message.get_content()
    assert message["Subject"] == "Confirme seu e-mail do EM OS"
    assert "Olá, Ana!" in body and "/auth/verify/" in body


def test_reminders_in_saved_language(app, auth):
    from reminders import morning_message, reminder_message
    auth.register_and_login()
    set_language(app, "fr")
    with app.app_context(), connect(app) as db:
        today = local_today_for("UTC")
        evening = reminder_message(db, 1, today)
        morning = morning_message(db, 1, today)
    assert evening["title"] == "🌙 Comment s'est passée ta journée ?"
    assert morning["title"] == "🌅 Bonjour ! C'est quoi ton top 3 ?"


def test_plurals():
    assert translate_n("es", "{n} task", "{n} tasks", 1) == "1 tarea"
    assert translate_n("es", "{n} task", "{n} tasks", 0) == "0 tareas"
    assert translate_n("fr", "{n} task", "{n} tasks", 0) == "0 tâche"          # French: 0 is singular
    assert translate_n("pt", "{n} task", "{n} tasks", 2) == "2 tarefas"
    assert translate_n("en", "{n} task", "{n} tasks", 1) == "1 task"
    assert translate("xx", "Today") == "Today"                                   # unknown language: English


def test_dates_in_each_language():
    d = date(2026, 11, 23)
    assert format_day(d, "long", "en") == "Monday, November 23"
    assert format_day(d, "long", "es") == "Lunes, 23 de noviembre"
    assert format_day(d, "long", "fr") == "Lundi 23 novembre"
    assert format_day(d, "long", "pt") == "Segunda-feira, 23 de novembro"
    assert format_day(d, "weekday", "es") == "Lunes"


def test_times_24_hours_outside_english(app, client, auth):
    auth.register_and_login()
    assert "8:00 PM" in client.get("/settings/").get_data(as_text=True)
    set_language(app, "es")
    assert "20:00" in client.get("/settings/").get_data(as_text=True)


def test_javascript_texts_sent_to_page(app, client, auth):
    auth.register_and_login()
    assert '<script type="application/json" id="js-texts">{}</script>' in client.get("/").get_data(as_text=True)
    set_language(app, "pt")
    html = client.get("/").get_data(as_text=True)
    texts = json.loads(html.split('id="js-texts">')[1].split("</script>")[0])
    assert texts["Open menu"] == "Abrir menu" and texts["Level up"] == "Subiu de nível"


def test_insights_cache_follows_language(app, client, auth):
    auth.register_and_login()
    with connect(app) as db:
        run = db.execute("INSERT INTO habits (user_id, name, icon) VALUES (1, 'Run', '🏃')").lastrowid
        for n in range(1, 41):
            day = (local_today_for("UTC") - timedelta(days=n)).isoformat()
            db.execute("INSERT INTO checkins (user_id, log_date, energy, mood, focus, score) VALUES (1, ?, 3, 3, ?, 60)",
                       (day, 5 if n % 2 == 0 else 2))
            if n % 2 == 0:
                db.execute("INSERT INTO habit_logs (habit_id, log_date) VALUES (?, ?)", (run, day))
    assert "higher on days you do 🏃 Run" in client.get("/insights/").get_data(as_text=True)
    set_language(app, "es")
    assert "mayor los días que haces 🏃 Run" in client.get("/insights/").get_data(as_text=True)


def test_quick_add_preview_in_spanish(app, client, auth):
    auth.register_and_login()
    set_language(app, "es")
    html = client.get("/quick/preview", query_string={"text": "Correr 3 veces por semana"}).get_data(as_text=True)
    assert "3 veces por semana" in html and "Hábito a dejar" in html


def test_every_language_listed():
    assert list(LANGUAGES) == ["en", "es", "fr", "pt"]


def test_page_text_escapes_quotes_for_attributes(app):
    from i18n import page_gettext
    with app.test_request_context("/"):
        assert str(page_gettext('Say "hi" & <go>')) == "Say &#34;hi&#34; &amp; &lt;go&gt;"
        assert str(page_gettext("It's {x}", x="<b>")) == "It's &lt;b&gt;"


def test_reminder_time_labels(app, client, auth):
    auth.register_and_login()
    assert ">8:00 PM<" in client.get("/settings/").get_data(as_text=True)
    set_language(app, "fr")
    html = client.get("/settings/").get_data(as_text=True)
    assert ">20:00<" in html and "PM<" not in html
