"""Shared setup for all tests. pytest loads this file automatically."""
import pytest

from app import create_app


@pytest.fixture
def app(tmp_path):
    """A fresh app with its own empty database for every single test."""
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "DATABASE": str(tmp_path / "test.db"),
    })
    yield app


@pytest.fixture
def client(app):
    """A fake browser that can visit pages and submit forms."""
    return app.test_client()


class Auth:
    """Helpers to sign up, log in and log out in one line."""

    def __init__(self, client):
        self.client = client

    def register(self, name="Ema", email="ema@example.com", password="password123"):
        return self.client.post("/auth/register",
                                data={"name": name, "email": email, "password": password})

    def login(self, email="ema@example.com", password="password123"):
        return self.client.post("/auth/login", data={"email": email, "password": password})

    def logout(self):
        return self.client.post("/auth/logout")

    def register_and_login(self, **kwargs):
        self.register(**kwargs)
        email = kwargs.get("email", "ema@example.com")
        return self.login(email=email)


@pytest.fixture
def auth(client):
    return Auth(client)
