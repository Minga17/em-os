import sqlite3


def test_register_then_login(client, auth):
    auth.register()
    response = auth.login()
    assert response.status_code == 302                 # redirected to home
    home = client.get("/")
    assert "Hi, Ema" in home.get_data(as_text=True)


def test_password_is_stored_as_a_hash_not_plain_text(app, auth):
    auth.register(password="my-secret-pw")
    db = sqlite3.connect(app.config["DATABASE"])
    stored = db.execute("SELECT password_hash FROM users").fetchone()[0]
    assert "my-secret-pw" not in stored
    assert stored.startswith(("scrypt:", "pbkdf2:"))


def test_duplicate_email_is_rejected(client, auth):
    auth.register()
    response = auth.register(name="Copycat")
    page = client.get("/auth/register").get_data(as_text=True) + response.get_data(as_text=True)
    assert "already registered" in page


def test_email_is_case_insensitive(client, auth):
    auth.register(email="Ema@Example.com")
    response = auth.login(email="ema@example.com")
    assert response.status_code == 302


def test_short_password_is_rejected(app, auth):
    auth.register(password="short")
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_wrong_password_does_not_log_in(client, auth):
    auth.register()
    response = auth.login(password="wrong-password")
    assert response.status_code == 200                 # stays on the login page
    assert "Incorrect email or password" in response.get_data(as_text=True)


def test_pages_require_login(client):
    for url in ["/", "/dashboard/", "/checkin/", "/habits/", "/tasks/",
                "/projects/", "/goals/", "/review/", "/export/"]:
        response = client.get(url)
        assert response.status_code == 302, url
        assert "/auth/login" in response.headers["Location"], url


def test_logout(client, auth):
    auth.register_and_login()
    auth.logout()
    assert client.get("/").status_code == 302
