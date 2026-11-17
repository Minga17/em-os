"""Smoke tests: every main page loads for a logged-in user (no crashes)."""
import pytest


@pytest.mark.parametrize("url", [
    "/", "/dashboard/", "/dashboard/?days=7", "/dashboard/?days=90", "/dashboard/?days=abc",
    "/checkin/", "/habits/", "/tasks/", "/projects/", "/goals/",
    "/review/", "/review/?week=2020-01-01", "/review/?week=nonsense", "/export/",
])
def test_page_loads(client, auth, url):
    auth.register_and_login()
    assert client.get(url).status_code == 200


def test_habit_toggle_builds_a_streak(client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read", "icon": "📚"})
    client.post("/habits/1/toggle")
    page = client.get("/habits/").get_data(as_text=True)
    assert "1-day streak" in page
