import sqlite3

ALL_TABLES = ["users", "checkins", "habits", "habit_logs", "projects", "tasks",
              "life_areas", "goals", "weekly_reviews"]


def count_rows(app):
    db = sqlite3.connect(app.config["DATABASE"])
    return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ALL_TABLES}


def fill_account(client):
    """Create at least one row in every table."""
    client.post("/checkin/", data={"energy": "3", "mood": "3", "focus": "3"})
    client.post("/habits/", data={"name": "Read"})
    client.post("/habits/1/toggle")
    client.post("/projects/", data={"title": "P", "status": "active"})
    client.post("/tasks/", data={"title": "T", "priority": "2", "project_id": "1"})
    client.get("/goals/")                                   # creates default life areas
    client.post("/goals/", data={"title": "G"})
    client.post("/review/", data={"rating": "4", "went_well": "ok"})


def test_delete_account_removes_every_row(app, client, auth):
    auth.register_and_login()
    fill_account(client)
    assert all(n > 0 for n in count_rows(app).values())     # sanity check

    response = client.post("/settings/delete",
                           data={"confirm": "DELETE", "password": "password123"})
    assert response.status_code == 302
    assert all(n == 0 for n in count_rows(app).values()), count_rows(app)
    assert client.get("/").status_code == 302               # logged out


def test_delete_needs_the_word_delete(app, client, auth):
    auth.register_and_login()
    client.post("/settings/delete", data={"confirm": "delete", "password": "password123"})
    assert count_rows(app)["users"] == 1


def test_delete_needs_the_right_password(app, client, auth):
    auth.register_and_login()
    client.post("/settings/delete", data={"confirm": "DELETE", "password": "wrong-one"})
    assert count_rows(app)["users"] == 1


def test_deleting_one_account_keeps_other_users(app, client, auth):
    auth.register_and_login(email="stay@example.com")
    fill_account(client)
    auth.logout()
    auth.register_and_login(email="leave@example.com")
    client.post("/settings/delete", data={"confirm": "DELETE", "password": "password123"})
    rows = count_rows(app)
    assert rows["users"] == 1 and rows["checkins"] == 1 and rows["tasks"] == 1


def test_change_password(client, auth):
    auth.register_and_login()
    client.post("/settings/password", data={"current_password": "password123",
                                            "new_password": "brand-new-pw",
                                            "confirm_password": "brand-new-pw"})
    auth.logout()
    assert auth.login(password="password123").status_code == 200   # old one fails
    assert auth.login(password="brand-new-pw").status_code == 302  # new one works


def test_change_password_needs_current_password(client, auth):
    auth.register_and_login()
    client.post("/settings/password", data={"current_password": "WRONG",
                                            "new_password": "brand-new-pw",
                                            "confirm_password": "brand-new-pw"})
    auth.logout()
    assert auth.login(password="password123").status_code == 302   # unchanged


def test_settings_page_loads(client, auth):
    auth.register_and_login()
    page = client.get("/settings/").get_data(as_text=True)
    assert "Time zone" in page and "Delete account" in page
