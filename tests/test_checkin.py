import sqlite3

GOOD_CHECKIN = {
    "energy": "4", "mood": "4", "focus": "5", "focused_hours": "3",
    "accomplished": "Wrote tests", "wins": "All green", "blockers": "", "priorities": "Deploy",
}


def checkin_rows(app):
    db = sqlite3.connect(app.config["DATABASE"])
    return db.execute("SELECT score, mood FROM checkins").fetchall()


def test_checkin_saves_the_right_score(app, client, auth):
    auth.register_and_login()
    response = client.post("/checkin/", data=GOOD_CHECKIN, follow_redirects=True)
    assert "82/100" in response.get_data(as_text=True)    # 13*4 + 15 + 10 + 5
    assert checkin_rows(app) == [(82, 4)]


def test_second_checkin_same_day_updates_instead_of_duplicating(app, client, auth):
    auth.register_and_login()
    client.post("/checkin/", data=GOOD_CHECKIN)
    client.post("/checkin/", data={**GOOD_CHECKIN, "mood": "2"})
    assert checkin_rows(app) == [(74, 2)]                 # still ONE row


def test_rating_out_of_range_is_rejected(app, client, auth):
    auth.register_and_login()
    response = client.post("/checkin/", data={**GOOD_CHECKIN, "energy": "9"})
    assert "between 1 and 5" in response.get_data(as_text=True)
    assert checkin_rows(app) == []


def test_hours_must_be_a_number(app, client, auth):
    auth.register_and_login()
    response = client.post("/checkin/", data={**GOOD_CHECKIN, "focused_hours": "lots"})
    assert "must be a number" in response.get_data(as_text=True)
    assert checkin_rows(app) == []
