"""The installable-app (PWA) files and the instant Today screen."""
import json
import sqlite3


def htmx(client, url):
    """Send a request the way htmx does (it adds the HX-Request header)."""
    return client.post(url, headers={"HX-Request": "true"})


# ── Install files ───────────────────────────────────────────────
def test_manifest_is_valid(client):
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/manifest+json")
    data = json.loads(response.get_data(as_text=True))
    assert data["display"] == "standalone"
    assert data["start_url"] == "/"
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert {"192x192", "512x512"} <= sizes


def test_manifest_icons_exist(client):
    data = json.loads(client.get("/manifest.webmanifest").get_data(as_text=True))
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200, icon["src"]


def test_service_worker_is_served_from_the_root(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["Content-Type"]
    assert response.headers["Cache-Control"] == "no-cache"


def test_install_files_need_no_login(client):
    for url in ["/sw.js", "/manifest.webmanifest", "/static/offline.html"]:
        assert client.get(url).status_code == 200, url


def test_pages_link_the_manifest_and_theme_color(client, auth):
    auth.register_and_login()
    page = client.get("/").get_data(as_text=True)
    assert 'rel="manifest"' in page and 'name="theme-color"' in page
    assert "js/htmx.min.js" in page and 'class="tabbar"' in page


# ── Today screen ────────────────────────────────────────────────
def test_today_screen_shows_ring_and_habits(client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read", "icon": "📚"})
    page = client.get("/").get_data(as_text=True)
    assert 'id="today-ring"' in page
    assert "0/2" in page                     # 1 habit + the check-in
    assert 'id="habit-1"' in page


def test_instant_tick_returns_only_the_updated_pieces(app, client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read", "icon": "📚"})
    response = htmx(client, "/habits/1/toggle")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "<html" not in html                       # a small fragment, not a page
    assert 'id="habit-1"' in html and "done" in html
    assert 'hx-swap-oob="true"' in html and "1/2" in html
    trigger = json.loads(response.headers["HX-Trigger"])
    assert "+5 XP" in trigger["toast"]["messages"][0]
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT COUNT(*) FROM habit_logs").fetchone()[0] == 1


def test_instant_untick(app, client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read"})
    htmx(client, "/habits/1/toggle")
    response = htmx(client, "/habits/1/toggle")
    assert "HX-Trigger" not in response.headers      # no "+5 XP" for undoing
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT COUNT(*) FROM habit_logs").fetchone()[0] == 0


def test_normal_form_still_works_without_javascript(client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read"})
    response = client.post("/habits/1/toggle")      # no HX-Request header
    assert response.status_code == 302


def test_all_done_celebration(client, auth):
    auth.register_and_login()
    client.post("/checkin/", data={"energy": "3", "mood": "3", "focus": "3"})
    client.post("/habits/", data={"name": "Read"})
    response = htmx(client, "/habits/1/toggle")
    messages = json.loads(response.headers["HX-Trigger"])["toast"]["messages"]
    assert any("Everything done today" in m for m in messages)
    assert "🎉" in response.get_data(as_text=True)


def test_achievement_pops_up_on_instant_action(app, client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read"})
    db = sqlite3.connect(app.config["DATABASE"])
    db.executemany("INSERT INTO habit_logs VALUES (1, ?)",
                   [(f"2025-01-{d:02d}",) for d in range(1, 32)] +
                   [(f"2025-03-{d:02d}",) for d in range(1, 31)] +
                   [(f"2025-05-{d:02d}",) for d in range(1, 31)] +
                   [(f"2025-07-{d:02d}",) for d in range(1, 9)])    # 99 old ticks
    db.commit()
    response = htmx(client, "/habits/1/toggle")      # the 100th tick
    messages = json.loads(response.headers["HX-Trigger"])["toast"]["messages"]
    assert any("Hundred Habits" in m for m in messages)


def test_instant_tick_is_still_private(client, auth):
    auth.register_and_login(email="alice@example.com")
    client.post("/habits/", data={"name": "Read"})
    auth.logout()
    auth.register_and_login(email="bob@example.com")
    assert htmx(client, "/habits/1/toggle").status_code == 404
