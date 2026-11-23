"""Quick add: sentences in 4 languages become the right habit or task."""
import sqlite3
from datetime import date

import pytest

from quickadd import describe, habit_fields, parse, task_fields

SUNDAY = date(2026, 11, 22)


def connect(app):
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    return db


@pytest.mark.parametrize("text, name, schedule, days, per_week, goal, unit", [
    ("Gym every Mon/Wed/Fri", "Gym", "days", "0,2,4", None, 1, ""),
    ("Run 3 times a week", "Run", "weekly", "", 3, 1, ""),
    ("Push-ups twice a week", "Push-ups", "weekly", "", 2, 1, ""),
    ("Drink 8 glasses of water daily", "Drink 8 glasses of water", "daily", "", None, 8, "glasses"),
    ("Yoga on Tuesdays and Thursdays", "Yoga", "days", "1,3", None, 1, ""),
    ("Study Spanish weekdays", "Study Spanish", "days", "0,1,2,3,4", None, 1, ""),
    ("Meditate every morning", "Meditate", "daily", "", None, 1, ""),
    ("Stretch 7 times a week", "Stretch", "daily", "", None, 1, ""),
    ("Ir al gimnasio lunes, miércoles y viernes", "Ir al gimnasio", "days", "0,2,4", None, 1, ""),
    ("Correr 3 veces por semana", "Correr", "weekly", "", 3, 1, ""),
    ("Beber 8 vasos de agua al día", "Beber 8 vasos de agua", "daily", "", None, 8, "vasos"),
    ("Courir 3 fois par semaine", "Courir", "weekly", "", 3, 1, ""),
    ("Lire 20 pages par jour", "Lire 20 pages", "daily", "", None, 20, "pages"),
    ("Academia toda segunda e quarta", "Academia", "days", "0,2", None, 1, ""),
    ("Ler 10 páginas por dia", "Ler 10 páginas", "daily", "", None, 10, "páginas"),
])
def test_habits(text, name, schedule, days, per_week, goal, unit):
    p = parse(text, SUNDAY)
    assert p["kind"] == "habit"
    f = habit_fields(p, "habit")
    assert (f["name"], f["schedule"], f["days"], f["goal_count"], f["unit"]) == (name, schedule, days, goal, unit)
    if per_week:
        assert f["per_week"] == per_week


@pytest.mark.parametrize("text", ["No smoking", "Quit vaping", "Dejar de fumar", "Arrêter de fumer", "Parar de fumar"])
def test_quit_habits(text):
    p = parse(text, SUNDAY)
    assert p["kind"] == "quit" and p["name"] == text
    assert habit_fields(p, "quit")["kind"] == "quit"


@pytest.mark.parametrize("text, title, due, priority, repeat", [
    ("Essay due Friday high", "Essay", "2026-11-27", 1, ""),
    ("Call mom tomorrow", "Call mom", "2026-11-23", 2, ""),
    ("Buy milk", "Buy milk", None, 2, ""),
    ("Pay rent monthly", "Pay rent", None, 2, "monthly"),
    ("Clean room weekly", "Clean room", None, 2, "weekly"),
    ("Finish report by Nov 30 urgent", "Finish report", "2026-11-30", 1, ""),
    ("Renew passport jan 5", "Renew passport", "2027-01-05", 2, ""),      # past this year: next year
    ("Dentist 2026-12-03 low", "Dentist", "2026-12-03", 3, ""),
    ("Prep meals in 3 days", "Prep meals", "2026-11-25", 2, ""),
    ("Plan trip next week", "Plan trip", "2026-11-23", 2, ""),
    ("Fix bike today!", "Fix bike", "2026-11-22", 1, ""),
    ("SAT exam on Friday", "SAT exam", "2026-11-27", 2, ""),
    ("Watch social media", "Watch social media", None, 2, ""),
    ("Entregar ensayo el viernes alta", "Entregar ensayo", "2026-11-27", 1, ""),
    ("Comprar pan mañana", "Comprar pan", "2026-11-23", 2, ""),
    ("Llamar al médico por la mañana", "Llamar al médico por la mañana", None, 2, ""),
    ("Pagar alquiler cada mes", "Pagar alquiler", None, 2, "monthly"),
    ("Rendre le devoir vendredi", "Rendre le devoir", "2026-11-27", 2, ""),
    ("Payer le loyer tous les mois", "Payer le loyer", None, 2, "monthly"),
    ("Réviser pour l'examen demain", "Réviser pour l'examen", "2026-11-23", 2, ""),
    ("Entregar trabalho sexta-feira", "Entregar trabalho", "2026-11-27", 2, ""),
    ("Pagar aluguel todo mês", "Pagar aluguel", None, 2, "monthly"),
    ("Estudar para prova 3 de dezembro", "Estudar para prova", "2026-12-03", 2, ""),
])
def test_tasks(text, title, due, priority, repeat):
    p = parse(text, SUNDAY)
    assert p["kind"] == "task"
    assert task_fields(p) == {"title": title, "due_date": due, "priority": priority, "repeat": repeat}


def test_icons():
    assert parse("Gym every Mon/Wed/Fri", SUNDAY)["icon"] == "🏋️"
    assert parse("Pagar alquiler cada mes", SUNDAY)["icon"] == "💰"          # not 📚 from "ler"
    p = parse("🎹 Piano 30 minutes daily", SUNDAY)
    assert (p["icon"], p["name"]) == ("🎹", "Piano 30 minutes")
    assert parse("Floss every day", SUNDAY)["icon"] == "✅"


def test_switching_kind_keeps_meaning():
    p = parse("Meditate every morning", SUNDAY)
    assert task_fields(p)["repeat"] == "daily"
    assert task_fields(parse("Study weekdays", SUNDAY))["repeat"] == "weekdays"
    assert habit_fields(parse("Clean room weekly", SUNDAY), "habit")["per_week"] == 1
    assert describe(parse("Buy milk", SUNDAY), "habit", SUNDAY) == ["Every day"]


@pytest.mark.parametrize("text", ["", "   ", "!!!", "a" * 500, "every every every", "🙂", "Mon Tue Wed Thu Fri Sat Sun",
                                  "in 999 days", "Feb 30", "0 times a week", "9999 glasses daily", "no", "<script>x</script>"])
def test_odd_input_never_crashes(text):
    p = parse(text, SUNDAY)
    assert (p["name"] or not text.strip()) and len(p["name"]) <= 120
    habit_fields(p, "habit"), task_fields(p), describe(p, p["kind"], SUNDAY)


# ── The page ────────────────────────────────────────────────────
def test_preview_then_add_habit(app, client, auth):
    auth.register_and_login()
    assert "Quick add" in client.get("/").get_data(as_text=True)
    html = client.get("/quick/preview", query_string={"text": "Gym every Mon/Wed/Fri"}).get_data(as_text=True)
    assert "Mon, Wed, Fri" in html and 'value="habit" checked' in html
    with connect(app) as db:
        assert db.execute("SELECT COUNT(*) FROM habits").fetchone()[0] == 0        # preview saves nothing
    response = client.post("/quick/add", data={"text": "Gym every Mon/Wed/Fri", "next": "home"})
    assert response.headers["Location"] == "/"
    with connect(app) as db:
        h = db.execute("SELECT name, icon, schedule, days, user_id FROM habits").fetchone()
    assert tuple(h) == ("Gym", "🏋️", "days", "0,2,4", 1)


def test_add_task_and_override_kind(app, client, auth):
    auth.register_and_login()
    client.post("/quick/add", data={"text": "Essay due Friday high", "next": "tasks"})
    client.post("/quick/add", data={"text": "Meditate every morning", "kind": "task"})
    with connect(app) as db:
        rows = [tuple(r) for r in db.execute("SELECT title, priority, repeat, status FROM tasks ORDER BY id")]
        assert db.execute("SELECT COUNT(*) FROM habits").fetchone()[0] == 0
    assert rows[0][:2] == ("Essay", 1) and rows[1] == ("Meditate", 2, "daily", "todo")


def test_preview_warns_about_duplicate(client, auth):
    auth.register_and_login()
    client.post("/habits/", data={"name": "Gym"})
    html = client.get("/quick/preview", query_string={"text": "gym daily"}).get_data(as_text=True)
    assert "You already have a habit" in html


def test_bad_input_rejected(client, auth):
    auth.register_and_login()
    assert client.get("/quick/preview", query_string={"text": "x", "kind": "admin"}).status_code == 400
    assert client.get("/quick/preview").get_data(as_text=True) == ""
    response = client.post("/quick/add", data={"text": "Buy milk", "next": "https://evil.example"})
    assert response.headers["Location"] == "/"                       # never sends you off-site
    html = client.get("/quick/preview", query_string={"text": "<b>x</b> daily"}).get_data(as_text=True)
    assert "<b>x</b>" not in html


def test_quick_add_needs_login(client):
    assert client.get("/quick/preview?text=x").status_code == 302
    assert client.post("/quick/add", data={"text": "x"}).status_code == 302
