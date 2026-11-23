"""Import: Loop, Todoist, EM OS export, any spreadsheet. Preview first, own account only, no duplicates."""
import io
import re
import sqlite3
import zipfile
from datetime import timedelta

import pytest

from timeutil import local_today_for


def connect(app):
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    return db


def today():
    return local_today_for("UTC")


def ago(n):
    return (today() - timedelta(days=n)).isoformat()


def upload(client, *files):
    data = {"files": [(io.BytesIO(content if isinstance(content, bytes) else content.encode()), name)
                      for name, content in files]}
    return client.post("/import/", data=data, content_type="multipart/form-data", follow_redirects=True)


def confirm(client, html):
    token = re.search(r'name="token" value="([^"]+)"', html).group(1)
    return client.post("/import/confirm", data={"token": token}, follow_redirects=True)


def import_file(client, *files):
    return confirm(client, upload(client, *files).get_data(as_text=True))


def loop_zip():
    """Built exactly like Loop's HabitsCSVExporter."""
    habits = ("Position,Name,Type,Question,Description,FrequencyNumerator,FrequencyDenominator,Color,Unit,"
              "Target Type,Target Value,Archived?,Category\n"
              "001,Floss,YES_NO,Did you floss?,,1,1,#FF8F00,,,,false,\n"
              "002,Gym,YES_NO,Did you go?,,3,7,#E53935,,,,false,\n"
              "003,Water,NUMERICAL,How many glasses?,,1,1,#039BE5,glasses,AT_LEAST,8.0,false,\n"
              "004,Old habit,YES_NO,,,1,1,#000000,,,,true,\n")
    floss = "Date,Value,Notes\n" + "".join(
        f"{ago(n)},{'YES_MANUAL' if n % 2 == 0 else 'NO'},\n" for n in range(10))
    gym = "Date,Value,Notes\n" + f"{ago(1)},YES_MANUAL,\n{ago(2)},YES_AUTO,\n{ago(3)},SKIP,\n{ago(4)},UNKNOWN,\n"
    water = "Date,Value,Notes\n" + f"{ago(1)},8000,\n{ago(2)},3000,\n{ago(3)},0,\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("Habits.csv", habits)
        z.writestr("001 Floss/Checkmarks.csv", floss)
        z.writestr("001 Floss/Scores.csv", "Date,Score\n")
        z.writestr("002 Gym/Checkmarks.csv", gym)
        z.writestr("003 Water/Checkmarks.csv", water)
        z.writestr("Checkmarks.csv", f"Date,Floss,Gym,Water,Old habit,\n{ago(1)},NO,YES_MANUAL,8000,YES_MANUAL,\n")
        z.writestr("Scores.csv", "Date,Floss,\n")
    return buffer.getvalue()


# ── Loop ────────────────────────────────────────────────────────
def test_loop_preview_then_import(app, client, auth):
    auth.register_and_login()
    html = upload(client, ("Loop Habits CSV 2026-11-22.zip", loop_zip())).get_data(as_text=True)
    assert "Loop Habit Tracker" in html and "Floss" in html and "archived" in html
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM habits").fetchone()[0] == 0          # preview saves nothing
    html = confirm(client, html).get_data(as_text=True)
    assert "Imported 4 habits, 9 habit days" in html
    with connect(app) as db:
        rows = {r["name"]: r for r in db.execute("SELECT * FROM habits")}
        logs = lambda name: dict(db.execute(                                       # noqa: E731
            "SELECT log_date, amount FROM habit_logs WHERE habit_id = ?", (rows[name]["id"],)).fetchall())
        assert len(logs("Floss")) == 5 and logs("Gym") == {ago(1): 1}            # YES_AUTO / SKIP aren't ticks
        assert logs("Water") == {ago(1): 8, ago(2): 3}                            # Loop stores 8 as 8000
        assert (rows["Gym"]["schedule"], rows["Gym"]["per_week"]) == ("weekly", 3)
        assert (rows["Water"]["goal_count"], rows["Water"]["unit"]) == (8, "glasses")
        assert rows["Old habit"]["is_active"] == 0 and rows["Floss"]["is_active"] == 1
        assert {r["user_id"] for r in rows.values()} == {1}


def test_importing_twice_adds_nothing(app, client, auth):
    auth.register_and_login()
    import_file(client, ("loop.zip", loop_zip()))
    html = import_file(client, ("loop.zip", loop_zip())).get_data(as_text=True)
    assert "Nothing new to import" in html
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM habits").fetchone()[0] == 4


def test_loop_merges_into_existing_habit(app, client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "floss"})
    html = upload(client, ("loop.zip", loop_zip())).get_data(as_text=True)
    assert "1 you already have" in html
    confirm(client, html)
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM habits WHERE lower(name) = 'floss'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM habit_logs WHERE habit_id = 1").fetchone()[0] == 5


# ── Todoist ─────────────────────────────────────────────────────
TODOIST = """TYPE,CONTENT,DESCRIPTION,PRIORITY,INDENT,AUTHOR,RESPONSIBLE,DATE,DATE_LANG,TIMEZONE,DURATION,DURATION_UNIT,meta,DEADLINE,DEADLINE_LANG,IS_COLLAPSED
section,School,,,,,,,,,,,,,,
task,Write essay @school,Chapter 3,1,1,Ema (123),,2026-12-03,en,UTC,,None,,,,
task,Find sources,,4,2,Ema (123),,,en,UTC,,None,,,,
task,Pay rent,,2,1,Ema (123),,every month,en,UTC,,None,,,,
task,Stand-up,,3,1,Ema (123),,every weekday,en,UTC,,None,,,,
note,Remember the rubric,,,,,,,,,,,,,,
"""


def test_todoist(app, client, auth):
    auth.register_and_login()
    html = upload(client, ("School.csv", TODOIST)).get_data(as_text=True)
    assert "Todoist" in html and "4</span> tasks" in html
    confirm(client, html)
    with connect(app) as db:
        tasks = {r["title"]: r for r in db.execute("SELECT * FROM tasks")}
    essay = tasks["Write essay @school"]
    assert (essay["priority"], essay["due_date"]) == (1, "2026-12-03")
    assert "Chapter 3" in essay["notes"] and "Section: School" in essay["notes"]
    assert "Part of: Write essay @school" in tasks["Find sources"]["notes"]
    assert tasks["Find sources"]["priority"] == 2                       # p4 = Todoist's "no priority"
    assert tasks["Pay rent"]["repeat"].startswith("monthly")
    assert tasks["Stand-up"]["repeat"] == "weekdays" and tasks["Stand-up"]["priority"] == 3
    assert "Remember the rubric" not in tasks


# ── EM OS export → import (moving to a new account) ─────────────
def test_emos_round_trip(app, client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Read", "icon": "📚"})
    client.post("/habits/1/toggle")
    client.post("/checkin/quick", data={"energy": 4, "mood": 5, "focus": 3})
    client.post("/tasks/", data={"title": "=SUM(A1) looks like a formula", "priority": 1, "due_date": "2026-12-01"})
    client.post("/goals/", data={"title": "Learn Spanish"})
    exported = [(f"em-os-{name}.csv", client.get(f"/export/{name}.csv").get_data())
                for name in ("checkins", "habits", "tasks", "goals", "weekly_reviews")]
    auth.logout()
    auth.register_and_login(name="New", email="new@example.com")
    html = upload(client, *exported).get_data(as_text=True)
    assert "an EM OS export" in html
    confirm(client, html)
    with connect(app) as db:
        assert db.execute("SELECT name, icon FROM habits WHERE user_id = 2").fetchone()[:] == ("Read", "📚")
        assert db.execute("SELECT COUNT(*) FROM habit_logs l JOIN habits h ON h.id = l.habit_id "
                          "WHERE h.user_id = 2").fetchone()[0] == 1
        c = db.execute("SELECT energy, mood, focus, score FROM checkins WHERE user_id = 2").fetchone()
        assert c[:3] == (4, 5, 3) and c[3] > 0
        t = db.execute("SELECT title, priority, due_date FROM tasks WHERE user_id = 2").fetchone()
        assert t[:] == ("=SUM(A1) looks like a formula", 1, "2026-12-01")
        assert db.execute("SELECT title FROM goals WHERE user_id = 2").fetchone()[0] == "Learn Spanish"
        assert db.execute("SELECT COUNT(*) FROM habits WHERE user_id = 1").fetchone()[0] == 1     # untouched


# ── Any spreadsheet ─────────────────────────────────────────────
def test_spreadsheet_task_list_with_semicolons_and_day_first(app, client, auth):
    auth.register_and_login()
    sheet = "Tarea;Fecha;Prioridad;Hecho\nComprar libros;25/12/2026;alta;\nLlamar a mamá;03/12/2026;;sí\n"
    confirm(client, upload(client, ("tareas.csv", sheet.encode("cp1252"))).get_data(as_text=True))
    with connect(app) as db:
        rows = [tuple(r) for r in db.execute("SELECT title, due_date, priority, status FROM tasks ORDER BY id")]
    assert rows == [("Comprar libros", "2026-12-25", 1, "todo"), ("Llamar a mamá", "2026-12-03", 2, "done")]


def test_spreadsheet_habit_grid(app, client, auth):
    auth.register_and_login()
    sheet = (f"Date,Read,Run,Pushups\n{ago(1)},x,,20\n{ago(2)},✓,1,0\n{ago(3)},,yes,15\n"
             f"{(today() + timedelta(days=3)).isoformat()},x,x,x\n")
    html = upload(client, ("tracker.csv", sheet)).get_data(as_text=True)
    assert "habit grid" in html
    confirm(client, html)
    with connect(app) as db:
        counts = dict(db.execute("SELECT h.name, COUNT(*) FROM habit_logs l JOIN habits h ON h.id = l.habit_id "
                                 "GROUP BY h.name").fetchall())
        assert counts == {"Read": 2, "Run": 2, "Pushups": 2}                  # future days ignored
        assert db.execute("SELECT amount FROM habit_logs WHERE habit_id = 3 ORDER BY log_date DESC").fetchone()[0] == 20


def test_spreadsheet_without_header(app, client, auth):
    auth.register_and_login()
    confirm(client, upload(client, ("list.csv", "Buy milk\nFix bike\n")).get_data(as_text=True))
    with connect(app) as db:
        assert [r[0] for r in db.execute("SELECT title FROM tasks ORDER BY id")] == ["Buy milk", "Fix bike"]


# ── Safety ──────────────────────────────────────────────────────
def test_rejects_non_csv_and_empty(client, auth):
    auth.register_and_login()
    assert "isn&#39;t a CSV" in upload(client, ("photo.png", b"\x89PNG\r\n\x1a\n" + bytes(50))).get_data(as_text=True)
    assert "didn&#39;t find anything" in upload(client, ("empty.csv", "Title\n")).get_data(as_text=True)


def test_rejects_zip_bomb(client, auth):
    auth.register_and_login()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.csv", "0" * (30 * 1024 * 1024))                        # 30 MB of zeros, tiny zipped
    assert len(buffer.getvalue()) < 200_000
    assert "too big" in upload(client, ("bomb.zip", buffer.getvalue())).get_data(as_text=True)


def test_rejects_large_upload(client, auth):
    auth.register_and_login()
    assert "too big" in upload(client, ("huge.csv", "a,b\n" * 1_400_000)).get_data(as_text=True)


def test_drafts_are_private_and_single_use(app, client, auth):
    auth.register_and_login()
    html = upload(client, ("list.csv", "Title\nSecret plan\n")).get_data(as_text=True)
    token = re.search(r'name="token" value="([^"]+)"', html).group(1)
    auth.logout()
    auth.register_and_login(name="Other", email="other@example.com")
    assert "expired" in client.post("/import/confirm", data={"token": token}, follow_redirects=True).get_data(as_text=True)
    auth.logout()
    auth.login()
    client.post("/import/confirm", data={"token": token})
    assert "expired" in client.post("/import/confirm", data={"token": token}, follow_redirects=True).get_data(as_text=True)
    with connect(app) as db:
        assert [tuple(r) for r in db.execute("SELECT user_id, title FROM tasks")] == [(1, "Secret plan")]


def test_cancel_discards_draft(app, client, auth):
    auth.register_and_login()
    upload(client, ("list.csv", "Title\nX\n"))
    client.post("/import/cancel")
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM import_drafts").fetchone()[0] == 0


def test_import_skips_welcome_tour(app, client, auth):
    auth.register_and_login(welcome=False)
    with connect(app) as db:
        db.execute("UPDATE users SET onboarded = 0")
    import_file(client, ("list.csv", "Title\nX\n"))
    assert client.get("/").status_code == 200


def test_import_needs_login(client):
    assert client.get("/import/").status_code == 302
    assert client.post("/import/confirm", data={"token": "x"}).status_code == 302


@pytest.mark.parametrize("junk", [b"\x00\x01\x02", b"PK\x03\x04garbage", "a,b\n\"unclosed\n".encode(),
                                  b"\xff\xfe\x00b\x00a\x00d", ",,,,\n,,,\n"])
def test_junk_never_crashes(client, auth, junk):
    auth.register_and_login()
    assert upload(client, ("junk.csv", junk)).status_code == 200


def test_duplicate_rows_in_one_file_import_once(app, client, auth):
    auth.register_and_login()
    confirm(client, upload(client, ("list.csv", "Title\nBuy milk\nbuy milk\nFix bike\n")).get_data(as_text=True))
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2


def test_zip_that_lies_about_its_size(monkeypatch):
    """Even if a zip claims tiny files, we stop reading once the real data is too big."""
    import importer
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("a.csv", "x" * 5000)
    real_infolist = zipfile.ZipFile.infolist

    def lying_infolist(self):
        infos = real_infolist(self)
        for info in infos:
            info.file_size = 10                      # the zip says 10 bytes
        return infos
    monkeypatch.setattr(zipfile.ZipFile, "infolist", lying_infolist)
    monkeypatch.setattr(importer, "MAX_UNZIPPED", 4000)
    with pytest.raises(importer.ImportProblem):
        importer.read_zip(buffer.getvalue())
