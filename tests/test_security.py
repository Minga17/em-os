"""Another user must never be able to see or change your data."""
import csv
import io
import sqlite3

import pytest


@pytest.fixture
def victim_data(app, client, auth):
    """User A creates one of everything, then logs out."""
    auth.register_and_login(name="Alice", email="alice@example.com")
    client.post("/habits/", data={"name": "Read", "icon": "📚"})
    client.post("/projects/", data={"title": "Secret project", "status": "active"})
    client.post("/tasks/", data={"title": "Secret task", "priority": "2", "project_id": "1"})
    client.post("/goals/", data={"title": "Secret goal"})
    client.post("/checkin/", data={"energy": "3", "mood": "3", "focus": "3",
                                   "accomplished": "private notes"})
    auth.logout()
    # User B signs in
    auth.register_and_login(name="Bob", email="bob@example.com")


@pytest.mark.parametrize("method, url", [
    ("post", "/habits/1/toggle"),
    ("post", "/habits/1/archive"),
    ("get", "/tasks/1/edit"),
    ("post", "/tasks/1/toggle"),
    ("post", "/tasks/1/delete"),
    ("get", "/projects/1/edit"),
    ("post", "/projects/1/delete"),
    ("get", "/goals/1/edit"),
    ("post", "/goals/1/delete"),
])
def test_other_users_items_return_404(client, victim_data, method, url):
    response = getattr(client, method)(url)
    assert response.status_code == 404


def test_cannot_attach_task_to_someone_elses_project(app, client, victim_data):
    client.post("/tasks/", data={"title": "Sneaky", "priority": "2", "project_id": "1"})
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT COUNT(*) FROM tasks WHERE title = 'Sneaky'").fetchone()[0] == 0


def test_pages_do_not_leak_other_users_data(client, victim_data):
    for url in ["/", "/tasks/?show=all", "/projects/", "/goals/", "/habits/", "/review/"]:
        page = client.get(url).get_data(as_text=True)
        assert "Secret" not in page, url
        assert "private notes" not in page, url


def test_export_only_contains_your_own_rows(client, victim_data):
    text = client.get("/export/checkins.csv").get_data(as_text=True).lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text)))
    assert len(rows) == 1                     # just the header row


def test_export_whitelist_blocks_other_tables(client, victim_data):
    assert client.get("/export/users.csv").status_code == 404


def test_export_neutralizes_spreadsheet_formulas(client, auth):
    auth.register_and_login()
    client.post("/checkin/", data={"energy": "3", "mood": "3", "focus": "3",
                                   "wins": "=HYPERLINK(\"http://evil\")"})
    text = client.get("/export/checkins.csv").get_data(as_text=True)
    assert "'=HYPERLINK" in text
