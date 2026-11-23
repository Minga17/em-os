"""Phase E: confetti, level-ups, themes, year heatmap, weekly recap card."""
import json
import sqlite3
from datetime import date, timedelta

import pytest

from timeutil import local_today_for

HTMX = {"HX-Request": "true"}


def connect(app):
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def today():
    return local_today_for("UTC")


def events(response):
    return json.loads(response.headers.get("HX-Trigger", "{}"))


def page_celebrations(html):
    start = html.index('id="celebrations">') + len('id="celebrations">')
    return json.loads(html[start:html.index("</script>", start)])


def give_checkins(app, count):
    """Past check-ins: 10 XP each."""
    with connect(app) as db:
        for n in range(1, count + 1):
            db.execute("INSERT INTO checkins (user_id, log_date, energy, mood, focus, score) VALUES (1, ?, 3, 3, 3, 50)",
                       ((today() - timedelta(days=n)).isoformat(),))


# ── Confetti ────────────────────────────────────────────────────
def test_confetti_when_everything_is_done(app, client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read", "icon": "📚"})
    client.post("/checkin/quick", data={"energy": 3, "mood": 3, "focus": 3})
    response = client.post("/habits/1/toggle", headers=HTMX)
    assert "confetti" in events(response)


def test_no_confetti_for_ordinary_taps(app, client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read"})
    client.post("/habits/", data={"name": "Run"})
    assert "confetti" not in events(client.post("/habits/1/toggle", headers=HTMX))


def test_confetti_after_achieving_a_goal_shows_on_next_page(app, client, auth):
    auth.register_and_login()
    with connect(app) as db:
        db.execute("INSERT INTO goals (user_id, title) VALUES (1, 'Ship it')")
    response = client.post("/goals/1/achieve", follow_redirects=True)
    assert "confetti" in page_celebrations(response.get_data(as_text=True))
    assert page_celebrations(client.get("/goals/").get_data(as_text=True)) == {}      # only once


def test_confetti_when_closing_the_day(app, client, auth):
    auth.register_and_login()
    assert "confetti" in events(client.post("/plan/close", headers=HTMX))


# ── Level-up screen ─────────────────────────────────────────────
def test_level_up_shows_once(app, client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read"})                 # first action: remembers level 1 quietly
    with connect(app) as db:
        assert db.execute("SELECT level_seen FROM users").fetchone()[0] == 1
    give_checkins(app, 10)                                          # 100 XP: level 2
    response = client.post("/habits/1/toggle", headers=HTMX)
    level_up = events(response)["levelUp"]
    assert (level_up["level"], level_up["title"]) == (2, "Explorer")
    assert "🌊 Ocean theme" in level_up["unlocks"]
    assert "confetti" in events(response)
    assert "levelUp" not in events(client.post("/habits/1/toggle", headers=HTMX))


def test_level_up_after_normal_form_shows_on_next_page(app, client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read"})
    give_checkins(app, 10)
    html = client.post("/habits/", data={"name": "Run"}, follow_redirects=True).get_data(as_text=True)
    assert page_celebrations(html)["levelUp"]["level"] == 2


def test_existing_users_dont_get_old_level_ups(app, client, auth):
    auth.register_and_login()
    give_checkins(app, 30)                                          # already level 3 before the update
    with connect(app) as db:                                        # ...and this update is brand new to them
        db.execute("UPDATE users SET level_seen = NULL")
    response = client.post("/habits/", data={"name": "Read"}, headers=HTMX)
    assert "levelUp" not in events(response)


# ── Themes ──────────────────────────────────────────────────────
def test_default_theme_is_midnight(client, auth):
    auth.register_and_login()
    html = client.get("/").get_data(as_text=True)
    assert 'data-theme="midnight"' in html and 'content="#161b25"' in html


def test_switch_to_light_and_auto(app, client, auth):
    auth.register_and_login()
    client.post("/settings/theme", data={"theme": "light"})
    html = client.get("/").get_data(as_text=True)
    assert 'data-theme="light"' in html and '<meta name="theme-color" content="#ffffff">' in html
    client.post("/settings/theme", data={"theme": "auto"})
    html = client.get("/").get_data(as_text=True)
    assert 'data-theme="auto"' in html and 'media="(prefers-color-scheme: light)"' in html


def test_locked_themes_need_a_level(app, client, auth):
    auth.register_and_login()
    response = client.post("/settings/theme", data={"theme": "ocean"}, follow_redirects=True)
    assert "unlocks at level 2" in response.get_data(as_text=True)
    assert "🔒 Level 2" in client.get("/settings/").get_data(as_text=True)
    give_checkins(app, 10)
    client.post("/settings/theme", data={"theme": "ocean"})
    assert 'data-theme="ocean"' in client.get("/").get_data(as_text=True)


@pytest.mark.parametrize("bad", ["neon", "", "light\" onload=\"x"])
def test_unknown_theme_rejected(app, client, auth, bad):
    auth.register_and_login()
    client.post("/settings/theme", data={"theme": bad})
    assert 'data-theme="midnight"' in client.get("/").get_data(as_text=True)


def test_broken_saved_theme_falls_back(app, client, auth):
    auth.register_and_login()
    with connect(app) as db:
        db.execute("UPDATE users SET theme = 'neon'")       # e.g. a theme removed in a future update
    response = client.get("/")
    assert response.status_code == 200 and 'data-theme="midnight"' in response.get_data(as_text=True)


def test_logged_out_pages_use_default_theme(client):
    assert 'data-theme="midnight"' in client.get("/auth/login").get_data(as_text=True)


# ── Year heatmap ────────────────────────────────────────────────
def test_heatmap_counts_each_day(app, client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read"})
    client.post("/habits/1/toggle")
    client.post("/checkin/quick", data={"energy": 3, "mood": 3, "focus": 3})
    html = client.get("/records/").get_data(as_text=True)
    t = today()
    label = f"{t.strftime('%a %b')} {t.day}: 1 check-in, 1 habit"
    assert label in html and "heat-cell l1" in html
    assert "1 active day in the last 12 months" in html
    assert html.count('class="heat-week"') in (53, 54)


def test_heatmap_brightness_levels(app, client, auth):
    auth.register_and_login()
    with connect(app) as db:
        for n in range(7):
            db.execute("INSERT INTO tasks (user_id, title, status, completed_at) VALUES (1, ?, 'done', ?)",
                       (f"T{n}", f"{today().isoformat()} 10:00:00"))
    html = client.get("/records/").get_data(as_text=True)
    t = today()
    assert f'heat-cell l4" title="{t.strftime("%a %b")} {t.day}: 7 tasks"' in html      # today's square, brightest


# ── Weekly recap ────────────────────────────────────────────────
def test_recap_shows_numbers_not_private_text(app, client, auth):
    auth.register_and_login()
    with connect(app) as db:
        db.execute("INSERT INTO tasks (user_id, title, status, completed_at) VALUES (1, 'Secret project X', 'done', ?)",
                   (f"{today().isoformat()} 10:00:00",))
    client.post("/checkin/quick", data={"energy": 5, "mood": 4, "focus": 3})
    html = client.get("/recap/").get_data(as_text=True)
    assert "My week" in html and "tasks finished" in html and "1/7" in html
    assert "Secret project X" not in html
    assert "Ema" not in html.split('id="recap-card"')[1].split("</div>\n\n")[0]      # name hidden by default


def test_recap_name_is_opt_in(client, auth):
    auth.register_and_login()
    html = client.get("/recap/?name=1").get_data(as_text=True)
    assert "Ema&#39;s week" in html or "Ema's week" in html
    assert '"name": "Ema"' in html


def test_recap_past_weeks_and_no_future(client, auth):
    auth.register_and_login()
    html = client.get("/recap/").get_data(as_text=True)
    assert "Next →" not in html and "← Previous" in html
    last_week = (today() - timedelta(days=7)).isoformat()
    assert "Next →" in client.get(f"/recap/?week={last_week}").get_data(as_text=True)


def test_recap_only_counts_your_data(app, client, auth):
    auth.register_and_login()
    client.post("/checkin/quick", data={"energy": 5, "mood": 5, "focus": 5})
    with connect(app) as db:
        db.execute("INSERT INTO tasks (user_id, title, status, completed_at) VALUES (1, 'Mine', 'done', ?)",
                   (f"{today().isoformat()} 10:00:00",))
    auth.logout()
    auth.register_and_login(name="Other", email="other@example.com")
    html = client.get("/recap/").get_data(as_text=True)
    assert "0/7" in html
    assert '<span class="recap-value">0</span><span class="recap-label">tasks finished' in html


def test_recap_links(app, client, auth, monkeypatch):
    auth.register_and_login()
    assert "Make a recap card" in client.get("/review/").get_data(as_text=True)
    sunday = date(2026, 11, 22)
    monkeypatch.setattr("plan.user_today", lambda: sunday)
    html = client.post("/plan/close", headers=HTMX).get_data(as_text=True)
    assert "See your week&#39;s recap" in html or "See your week's recap" in html
    monkeypatch.setattr("plan.user_today", lambda: sunday - timedelta(days=1))
    assert "recap" not in client.post("/plan/close", headers=HTMX).get_data(as_text=True)


def test_recap_needs_login(client):
    assert client.get("/recap/").status_code == 302
