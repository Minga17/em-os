"""Bring your history into EM OS.

Understands four kinds of file (it works out which one by itself):
  • Loop Habit Tracker: the .zip from "Export as CSV" (or the CSV files inside it)
  • Todoist: a project exported as CSV
  • EM OS's own CSV export (check-ins, habits, tasks, goals, weekly reviews)
  • Any spreadsheet saved as CSV: a list of tasks, or a habit grid (dates down, habits across)

Safety:
  • Nothing is saved until you see the preview and press Import.
  • Everything is added to YOUR account only; ids or user columns in a file are ignored.
  • Things you already have are skipped, so importing the same file twice adds nothing.
  • Zip files are read in memory with size limits; nothing is ever unpacked to disk.
"""
import csv
import io
import json
import re
import secrets
import zipfile
import zlib
from datetime import date, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from i18n import N_, _, _n

from auth import login_required
from db import get_db
from quickadd import fold, parse as parse_sentence
from scoring import compute_score
from task_logic import store_repeat
from timeutil import user_today

bp = Blueprint("importer", __name__, url_prefix="/import")

MAX_UPLOAD = 5 * 1024 * 1024          # per import, all files together
MAX_UNZIPPED = 25 * 1024 * 1024       # what a zip may expand to
MAX_FILES = 400
MAX_ROWS = 20000                      # habit days + tasks + everything else
DRAFT_HOURS = 2


class ImportProblem(ValueError):
    """A message we can show the person as-is."""


# ── Reading files ───────────────────────────────────────────────
def decode(raw):
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportProblem(_("We couldn't read that file. Please save it as CSV (UTF-8)."))


def read_uploads(uploads):
    """[(filename, text)] from uploaded CSV and zip files, with size limits."""
    files, total = [], 0
    for upload in uploads:
        if not upload or not upload.filename:
            continue
        raw = upload.read(MAX_UPLOAD + 1)
        total += len(raw)
        if total > MAX_UPLOAD:
            raise ImportProblem(_("That's too big. Imports can be up to 5 MB."))
        if raw[:2] == b"PK":
            files += read_zip(raw)
        elif upload.filename.lower().endswith((".csv", ".txt", ".tsv")) or b"," in raw[:2000] or b";" in raw[:2000]:
            files.append((upload.filename, decode(raw)))
        else:
            raise ImportProblem(_("“{name}” isn't a CSV or zip file.", name=upload.filename[:60]))
    if not files:
        raise ImportProblem(_("Please choose a CSV or zip file."))
    return files


def read_zip(raw):
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ImportProblem(_("That zip file seems to be damaged."))
    entries = [i for i in archive.infolist() if i.filename.lower().endswith(".csv") and not i.is_dir()]
    if len(entries) > MAX_FILES or sum(i.file_size for i in entries) > MAX_UNZIPPED:
        raise ImportProblem(_("That zip file is too big to import."))
    files = []
    for info in entries:
        try:
            with archive.open(info) as f:
                data = f.read(MAX_UNZIPPED + 1)          # never trust the size the zip claims
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError):
            raise ImportProblem(_("That zip file seems to be damaged."))
        if len(data) > MAX_UNZIPPED:
            raise ImportProblem(_("That zip file is too big to import."))
        files.append((info.filename, decode(data)))
    return files


def rows_of(text):
    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text), dialect)]
    return [row for row in rows if any(row)]


def unsafe_prefix(value):
    """EM OS exports put ' before text starting with = + - @ (to stop spreadsheet formulas). Undo that."""
    if value[:2] in ("'=", "'+", "'-", "'@"):
        return value[1:]
    return value


def short(text, limit):
    return unsafe_prefix(text or "").strip()[:limit]


# ── Dates and simple values ─────────────────────────────────────
def iso(value):
    try:
        return date.fromisoformat(value.strip()[:10]).isoformat()
    except (ValueError, AttributeError):
        return None


def number_parts(value):
    m = re.fullmatch(r"\s*(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{2,4})(?:[ T].*)?", value or "")
    return tuple(map(int, m.groups())) if m else None


def day_first(values, language="en"):
    """Is a column of dates like 31/12/2026 (day first) or 12/31/2026 (month first)?"""
    for v in values:
        parts = number_parts(v)
        if parts and parts[0] <= 31 and len(str(parts[0])) <= 2:
            if parts[0] > 12:
                return True
            if parts[1] > 12:
                return False
    return language != "en"


def to_date(value, first_is_day, today):
    if not value:
        return None
    found = iso(value)
    if found:
        return found
    parts = number_parts(value)
    if parts:
        a, b, year = parts
        if len(str(a)) == 4:                              # 2026/12/31
            year, a, b = a, b, year
            first_is_day = False
        year += 2000 if year < 100 else 0
        d, m = (a, b) if first_is_day else (b, a)
        try:
            return date(year, m, d).isoformat()
        except ValueError:
            return None
    p = parse_sentence("x " + value, today)               # "Nov 30", "tomorrow", "vendredi"…
    return p["due_date"]


YES = {"1", "x", "✓", "✔", "✅", "y", "yes", "true", "done", "si", "sí", "oui", "sim", "fait", "hecho", "feito",
       "completed", "complete", "2", "yes_manual"}
NO = {"", "0", "-", "n", "no", "false", "non", "nao", "não", "todo", "-1", "unknown", "skip", "3", "yes_auto"}


def truthy(value):
    return value.strip().lower() in YES


def looks_like_tick(value):
    v = value.strip().lower()
    return v in YES or v in NO or re.fullmatch(r"\d+(\.\d+)?", v) is not None


PRIORITY_WORDS = {"1": 1, "p1": 1, "high": 1, "alta": 1, "haute": 1, "urgent": 1, "urgente": 1, "!!!": 1,
                  "2": 2, "p2": 2, "medium": 2, "media": 2, "moyenne": 2, "média": 2, "normal": 2,
                  "3": 3, "p3": 3, "low": 3, "baja": 3, "basse": 3, "baixa": 3,
                  "4": 2, "p4": 2}


def header_index(header, names):
    folded = [fold(h).strip() for h in header]
    for name in names:
        if name in folded:
            return folded.index(name)
    return None


TITLE_HEADERS = ["title", "task", "name", "content", "todo", "to do", "item", "tarea", "titulo", "nombre",
                 "tache", "titre", "nom", "tarefa", "nome", "descricao curta"]
DUE_HEADERS = ["due", "due date", "due_date", "date", "deadline", "fecha", "vencimiento", "fecha limite",
               "echeance", "date limite", "data", "prazo", "data limite"]
PRIORITY_HEADERS = ["priority", "prioridad", "priorite", "prioridade"]
NOTES_HEADERS = ["notes", "note", "description", "details", "notas", "descripcion", "description", "notes",
                 "descricao", "observacoes"]
STATUS_HEADERS = ["status", "done", "completed", "complete", "estado", "hecho", "statut", "fait", "feito",
                  "concluido", "situacao"]
HABIT_HEADERS = ["habit", "habito", "habitude", "habits"]
AMOUNT_HEADERS = ["amount", "count", "value", "cantidad", "valor", "quantite", "valeur", "quantidade"]
DATE_HEADERS = ["date", "day", "log_date", "fecha", "dia", "jour", "data"]


# ── A plan: what would be imported ──────────────────────────────
def empty_plan(source):
    return {"source": source, "habits": [], "tasks": [], "checkins": [], "goals": [], "reviews": [],
            "notes": []}


def new_habit(name, icon="✅", **fields):
    habit = {"name": name[:60], "icon": icon[:4] or "✅", "kind": "build", "schedule": "daily", "days": "",
             "per_week": 3, "goal_count": 1, "unit": "", "active": 1, "logs": {}}
    habit.update(fields)
    return habit


# Loop Habit Tracker ------------------------------------------------
def is_loop(files):
    return any(name.split("/")[-1] == "Habits.csv" and text.lstrip("﻿").startswith("Position,Name")
               for name, text in files)


def loop_frequency(numerator, denominator):
    try:
        num, den = int(numerator), int(denominator)
    except ValueError:
        return {}
    if den <= 0 or num <= 0 or num >= den:
        return {"schedule": "daily"}
    per_week = round(num * 7 / den)
    if per_week >= 7:
        return {"schedule": "daily"}
    return {"schedule": "weekly", "per_week": max(1, per_week)}


def from_loop(files, today):
    plan = empty_plan("loop")
    by_name = dict(files)
    habits_file = next(text for name, text in files if name.split("/")[-1] == "Habits.csv")
    rows = rows_of(habits_file)
    header, rows = rows[0], rows[1:]
    col = {h: i for i, h in enumerate(header)}
    per_habit = {name.split("/")[-2][:3]: text for name, text in files
                 if name.endswith("/Checkmarks.csv") and name.count("/") >= 1}
    root = by_name.get("Checkmarks.csv")
    root_rows = rows_of(root) if root else []

    for row in rows:
        get = lambda key: row[col[key]] if key in col and col[key] < len(row) else ""   # noqa: E731
        name = get("Name")
        if not name:
            continue
        numerical = get("Type") == "NUMERICAL" or (get("Type") == "" and get("Unit") != "")
        habit = new_habit(name, guess_icon_for(name), **loop_frequency(get("FrequencyNumerator"),
                                                                       get("FrequencyDenominator")))
        habit["active"] = 0 if get("Archived?").lower() == "true" else 1
        if numerical:
            try:
                target = round(float(get("Target Value") or 1))
            except ValueError:
                target = 1
            habit.update(goal_count=min(100, max(1, target)) if habit["schedule"] == "daily" else 1,
                         unit=get("Unit")[:20])

        # Checkmarks: prefer the habit's own file, else its column in the big file
        ticks = []
        own = per_habit.get(get("Position"))
        if own:
            ticks = [(r[0], r[1]) for r in rows_of(own)[1:] if len(r) >= 2]
        elif root_rows and name in root_rows[0]:
            i = root_rows[0].index(name)
            ticks = [(r[0], r[i]) for r in root_rows[1:] if len(r) > i]
        for day, value in ticks:
            day = iso(day)
            if not day or day > today.isoformat():
                continue
            amount = loop_amount(value, numerical)
            if amount:
                habit["logs"][day] = amount
        plan["habits"].append(habit)
    return plan


def loop_amount(value, numerical):
    v = value.strip().upper()
    if numerical:
        try:
            raw = float(v)
        except ValueError:
            return 0
        return min(1000, round(raw / 1000)) if raw > 0 else 0     # Loop stores 8 as 8000
    return 1 if v in ("YES_MANUAL", "2") else 0                   # YES_AUTO is a day Loop filled in, not a tick


def guess_icon_for(name):
    from quickadd import guess_icon
    return guess_icon(fold(name))


# Todoist -------------------------------------------------------------
def is_todoist(files):
    return any(rows_of(text)[:1] and [fold(h) for h in rows_of(text)[0][:2]] == ["type", "content"]
               for _, text in files)


def todoist_date(text, today):
    """Todoist keeps dates as typed: 'every monday', 'Dec 3', '2026-12-03 10:00'…"""
    if not text:
        return None, ""
    found = iso(text)
    if found:
        return found, ""
    p = parse_sentence("x " + text, today)
    if p["habit_signal"] and not p["quit"]:
        if p["schedule"] == "days" and p["days"] == [0, 1, 2, 3, 4]:
            return today.isoformat(), "weekdays"
        if p["schedule"] == "days":
            first = min((today + timedelta(days=(d - today.weekday()) % 7) for d in p["days"]))
            return first.isoformat(), "weekly"
        return today.isoformat(), "daily" if p["schedule"] == "daily" else "weekly"
    if p["repeat"]:
        return p["due_date"] or today.isoformat(), p["repeat"]
    return p["due_date"], ""


def from_todoist(files, today):
    plan = empty_plan("todoist")
    for filename, text in files:
        rows = rows_of(text)
        if not rows or [fold(h) for h in rows[0][:2]] != ["type", "content"]:
            continue
        col = {fold(h): i for i, h in enumerate(rows[0])}
        get = lambda row, key: row[col[key]] if key in col and col[key] < len(row) else ""   # noqa: E731
        section, parents = "", {}
        for row in rows[1:]:
            kind = get(row, "type").lower()
            if kind == "section":
                section = get(row, "content")
                continue
            if kind != "task" or not get(row, "content"):
                continue
            title = get(row, "content")
            indent = int(get(row, "indent") or 1) if get(row, "indent").isdigit() else 1
            parents[indent] = title
            notes = [get(row, "description")]
            if section:
                notes.append(_("Section: {name}", name=section))
            if indent > 1 and parents.get(indent - 1):
                notes.append(_("Part of: {name}", name=parents[indent - 1]))
            due, repeat = todoist_date(get(row, "date"), today)
            plan["tasks"].append({"title": title[:200], "notes": "\n".join(n for n in notes if n)[:2000],
                                  "priority": PRIORITY_WORDS.get(get(row, "priority"), 2),
                                  "due_date": due, "repeat": repeat, "status": "todo"})
    return plan


# EM OS's own export ---------------------------------------------------
EMOS_HEADERS = {
    "checkins": ["log_date", "energy", "mood", "focus"],
    "habits": ["log_date", "habit", "icon", "amount"],
    "tasks": ["title", "project", "status", "priority"],
    "goals": ["title", "life_area", "status", "progress"],
    "weekly_reviews": ["week_start", "rating", "went_well"],
}


def emos_kind(text):
    rows = rows_of(text)
    header = rows[0] if rows else []
    for kind, start in EMOS_HEADERS.items():
        if header[:len(start)] == start:
            return kind
    return None


def is_emos(files):
    return any(emos_kind(text) for _, text in files)


def as_int(value, low, high, default=None):
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return n if low <= n <= high else default


def from_emos(files, today):
    plan = empty_plan("emos")
    habits = {}
    for _name, text in files:
        kind = emos_kind(text)
        if not kind:
            continue
        rows = rows_of(text)
        header, rows = rows[0], rows[1:]
        for row in rows:
            r = dict(zip(header, row))
            if kind == "checkins":
                day = iso(r.get("log_date", ""))
                values = [as_int(r.get(k), 1, 5) for k in ("energy", "mood", "focus")]
                if not day or day > today.isoformat() or None in values:
                    continue
                try:
                    hours = min(24.0, max(0.0, float(r.get("focused_hours") or 0)))
                except ValueError:
                    hours = 0.0
                plan["checkins"].append({"log_date": day, "energy": values[0], "mood": values[1],
                                         "focus": values[2], "focused_hours": hours,
                                         **{k: short(r.get(k), 2000) for k in
                                            ("accomplished", "wins", "blockers", "priorities")}})
            elif kind == "habits":
                name, day = short(r.get("habit"), 60), iso(r.get("log_date", ""))
                if not name or not day or day > today.isoformat():
                    continue
                habit = habits.setdefault(name, new_habit(name, r.get("icon") or "✅",
                                                          active=0 if r.get("habit_status") == "archived" else 1))
                habit["logs"][day] = as_int(r.get("amount"), 1, 1000, 1)
            elif kind == "tasks":
                title = short(r.get("title"), 200)
                if not title:
                    continue
                status = r.get("status") if r.get("status") in ("todo", "doing", "done") else "todo"
                plan["tasks"].append({"title": title, "notes": "", "priority": as_int(r.get("priority"), 1, 3, 2),
                                      "due_date": iso(r.get("due_date", "")), "repeat": "", "status": status,
                                      "completed": iso(r.get("completed", "")) if status == "done" else None})
            elif kind == "goals":
                title = short(r.get("title"), 200)
                if not title:
                    continue
                status = r.get("status") if r.get("status") in ("active", "achieved", "paused", "dropped") else "active"
                plan["goals"].append({"title": title, "area": short(r.get("life_area"), 40), "status": status,
                                      "progress": as_int(r.get("progress"), 0, 100, 0),
                                      "target_date": iso(r.get("target_date", "")), "why": short(r.get("why"), 2000)})
            elif kind == "weekly_reviews":
                week = iso(r.get("week_start", ""))
                if not week:
                    continue
                plan["reviews"].append({"week_start": week, "rating": as_int(r.get("rating"), 1, 5),
                                        **{k: short(r.get(k), 2000) for k in
                                           ("went_well", "challenges", "lessons", "next_focus")}})
    plan["habits"] = list(habits.values())
    return plan


# Any spreadsheet -------------------------------------------------------
def from_spreadsheet(files, today, language="en"):
    plan = empty_plan("spreadsheet")
    for filename, text in files:
        rows = rows_of(text)
        if len(rows) < 2:
            continue
        header, body = rows[0], rows[1:]
        date_col = header_index(header, DATE_HEADERS)
        habit_col = header_index(header, HABIT_HEADERS)
        first_is_day = day_first([r[0] for r in body if r] + [r[date_col] for r in body if date_col is not None
                                                                 and len(r) > date_col], language)

        # A habit grid: Date | Read | Run | …  with ticks in the cells
        first_column_dates = sum(1 for r in body if to_date(r[0], first_is_day, today)) >= len(body) * 0.8
        tick_columns = [i for i in range(1, len(header)) if header[i] and
                        all(looks_like_tick(r[i]) for r in body if len(r) > i)]
        if first_column_dates and tick_columns and habit_col is None:
            for i in tick_columns:
                habit = new_habit(header[i], guess_icon_for(header[i]))
                for r in body:
                    day = to_date(r[0], first_is_day, today)
                    if day and day <= today.isoformat() and len(r) > i:
                        amount = tick_amount(r[i])
                        if amount:
                            habit["logs"][day] = amount
                plan["habits"].append(habit)
            plan["notes"].append(_("{file}: read as a habit grid (dates down, habits across).", file=filename))
            continue

        # A habit log: Date | Habit | (Amount)
        if habit_col is not None and date_col is not None:
            amount_col = header_index(header, AMOUNT_HEADERS)
            habits = {}
            for r in body:
                if len(r) <= max(habit_col, date_col) or not r[habit_col]:
                    continue
                day = to_date(r[date_col], first_is_day, today)
                if not day or day > today.isoformat():
                    continue
                amount = tick_amount(r[amount_col]) if amount_col is not None and len(r) > amount_col else 1
                if amount:
                    name = short(r[habit_col], 60)
                    habits.setdefault(name, new_habit(name, guess_icon_for(name)))["logs"][day] = amount
            plan["habits"] += list(habits.values())
            plan["notes"].append(_("{file}: read as a habit log (one row per day).", file=filename))
            continue

        # Otherwise: a list of tasks
        title_col = header_index(header, TITLE_HEADERS)
        has_header = title_col is not None or any(
            header_index(header, names) is not None for names in (DUE_HEADERS, PRIORITY_HEADERS, STATUS_HEADERS))
        if title_col is None:
            title_col = 0
        if not has_header:
            body = rows                                  # no header row: every row is a task
        due_col = header_index(header, DUE_HEADERS) if has_header else None
        priority_col = header_index(header, PRIORITY_HEADERS) if has_header else None
        notes_col = header_index(header, NOTES_HEADERS) if has_header else None
        status_col = header_index(header, STATUS_HEADERS) if has_header else None
        cell = lambda r, i: r[i] if i is not None and i < len(r) else ""     # noqa: E731
        for r in body:
            title = short(cell(r, title_col), 200)
            if not title:
                continue
            done = status_col is not None and (truthy(cell(r, status_col)) or
                                               fold(cell(r, status_col)) in ("done", "terminado", "termine", "concluido"))
            plan["tasks"].append({"title": title, "notes": short(cell(r, notes_col), 2000),
                                  "priority": PRIORITY_WORDS.get(fold(cell(r, priority_col)).strip(), 2),
                                  "due_date": to_date(cell(r, due_col), first_is_day, today),
                                  "repeat": "", "status": "done" if done else "todo"})
        plan["notes"].append(_("{file}: read as a list of tasks.", file=filename))
    return plan


def tick_amount(value):
    v = value.strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", v):
        n = round(float(v))
        return min(1000, n) if n > 0 else 0
    return 1 if v in YES else 0


# ── Detect, then check against what you already have ────────────
def build_plan(files, today, language="en"):
    if is_loop(files):
        plan = from_loop(files, today)
    elif is_todoist(files):
        plan = from_todoist(files, today)
    elif is_emos(files):
        plan = from_emos(files, today)
    else:
        plan = from_spreadsheet(files, today, language)
    size = (sum(len(h["logs"]) + 1 for h in plan["habits"]) + len(plan["tasks"]) + len(plan["checkins"])
            + len(plan["goals"]) + len(plan["reviews"]))
    if size == 0:
        raise ImportProblem(_("We didn't find anything to import in that file."))
    if size > MAX_ROWS:
        raise ImportProblem(_("That's a lot! Imports can have up to {n} rows. Try splitting the file.", n=MAX_ROWS))
    return plan


def existing(db, user_id):
    return {
        "habits": {fold(r[0]): r[1] for r in db.execute(
            "SELECT name, id FROM habits WHERE user_id = ? ORDER BY is_active", (user_id,))},
        "tasks": {(fold(r[0]), r[1]) for r in db.execute(
            "SELECT title, due_date FROM tasks WHERE user_id = ?", (user_id,))},
        "checkins": {r[0] for r in db.execute("SELECT log_date FROM checkins WHERE user_id = ?", (user_id,))},
        "goals": {fold(r[0]) for r in db.execute("SELECT title FROM goals WHERE user_id = ?", (user_id,))},
        "reviews": {r[0] for r in db.execute("SELECT week_start FROM weekly_reviews WHERE user_id = ?", (user_id,))},
    }


def summarize(db, user_id, plan):
    """Counts for the preview: what's new, what you already have."""
    have = existing(db, user_id)
    habit_logs = {}
    for habit_id in have["habits"].values():
        habit_logs[habit_id] = {r[0] for r in db.execute("SELECT log_date FROM habit_logs WHERE habit_id = ?",
                                                         (habit_id,))}
    new_habits = [h for h in plan["habits"] if fold(h["name"]) not in have["habits"]]
    new_days = sum(len(set(h["logs"]) - habit_logs.get(have["habits"].get(fold(h["name"])), set()))
                   for h in plan["habits"])
    seen, new_tasks = set(have["tasks"]), 0
    for t in plan["tasks"]:
        key = (fold(t["title"]), t["due_date"])
        if key not in seen:
            seen.add(key)
            new_tasks += 1
    return {
        "habits_new": len(new_habits), "habits_merged": len(plan["habits"]) - len(new_habits),
        "habit_days": new_days,
        "tasks_new": new_tasks, "tasks_skipped": len(plan["tasks"]) - new_tasks,
        "checkins_new": len({c["log_date"] for c in plan["checkins"]} - have["checkins"]),
        "goals_new": len({fold(gl["title"]) for gl in plan["goals"]} - have["goals"]),
        "reviews_new": len({r["week_start"] for r in plan["reviews"]} - have["reviews"]),
    }


def apply_plan(db, user_id, plan):
    """Save it all in one go (all or nothing). Returns the same counts as summarize()."""
    counts = summarize(db, user_id, plan)
    have = existing(db, user_id)
    for h in plan["habits"]:
        habit_id = have["habits"].get(fold(h["name"]))
        if habit_id is None:
            habit_id = db.execute(
                """INSERT INTO habits (user_id, name, icon, kind, schedule, days, per_week, goal_count, unit, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, h["name"], h["icon"], h["kind"], h["schedule"], h["days"], h["per_week"],
                 h["goal_count"], h["unit"], h["active"])).lastrowid
            have["habits"][fold(h["name"])] = habit_id
        db.executemany("INSERT OR IGNORE INTO habit_logs (habit_id, log_date, amount) VALUES (?, ?, ?)",
                       [(habit_id, day, amount) for day, amount in h["logs"].items()])
    for t in plan["tasks"]:
        key = (fold(t["title"]), t["due_date"])
        if key in have["tasks"]:
            continue
        have["tasks"].add(key)
        completed = None
        if t["status"] == "done":
            completed = f"{t.get('completed') or user_today().isoformat()} 12:00:00"
        db.execute("""INSERT INTO tasks (user_id, title, notes, priority, due_date, status, repeat, completed_at)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                   (user_id, t["title"], t["notes"] or None, t["priority"], t["due_date"], t["status"],
                    store_repeat(t["repeat"], t["due_date"]) if t["repeat"] else "", completed))
    for c in plan["checkins"]:
        score = compute_score(c["energy"], c["mood"], c["focus"], c["focused_hours"],
                              c["accomplished"], c["wins"])
        db.execute("""INSERT OR IGNORE INTO checkins (user_id, log_date, energy, mood, focus, focused_hours,
                          accomplished, wins, blockers, priorities, score)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (user_id, c["log_date"], c["energy"], c["mood"], c["focus"], c["focused_hours"],
                    c["accomplished"] or None, c["wins"] or None, c["blockers"] or None,
                    c["priorities"] or None, score))
    for gl in plan["goals"]:
        if fold(gl["title"]) in have["goals"]:
            continue
        have["goals"].add(fold(gl["title"]))
        area_id = None
        if gl["area"]:
            db.execute("INSERT OR IGNORE INTO life_areas (user_id, name) VALUES (?, ?)", (user_id, gl["area"]))
            area_id = db.execute("SELECT id FROM life_areas WHERE user_id = ? AND name = ?",
                                 (user_id, gl["area"])).fetchone()[0]
        db.execute("""INSERT INTO goals (user_id, area_id, title, why, target_date, status, progress)
                      VALUES (?, ?, ?, ?, ?, ?, ?)""",
                   (user_id, area_id, gl["title"], gl["why"] or None, gl["target_date"], gl["status"], gl["progress"]))
    for r in plan["reviews"]:
        db.execute("""INSERT OR IGNORE INTO weekly_reviews (user_id, week_start, rating, went_well, challenges,
                          lessons, next_focus) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                   (user_id, r["week_start"], r["rating"], r["went_well"] or None, r["challenges"] or None,
                    r["lessons"] or None, r["next_focus"] or None))
    db.commit()
    return counts


# ── Pages ───────────────────────────────────────────────────────
SOURCES = {"loop": "Loop Habit Tracker", "todoist": "Todoist", "emos": N_("an EM OS export"),
           "spreadsheet": N_("a spreadsheet")}


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    db = get_db()
    db.execute("DELETE FROM import_drafts WHERE created_at < datetime('now', ?)", (f"-{DRAFT_HOURS} hours",))
    db.commit()
    if request.method == "GET":
        return render_template("import/index.html")
    try:
        files = read_uploads(request.files.getlist("files"))
        plan = build_plan(files, user_today(), language=g.user["language"] if "language" in g.user.keys() else "en")
    except ImportProblem as problem:
        flash(str(problem), "error")
        return redirect(url_for("importer.index"))
    token = secrets.token_urlsafe(16)
    db.execute("DELETE FROM import_drafts WHERE user_id = ?", (g.user["id"],))     # one draft at a time
    db.execute("INSERT INTO import_drafts (user_id, token, plan) VALUES (?, ?, ?)",
               (g.user["id"], token, json.dumps(plan)))
    db.commit()
    return render_template("import/preview.html", plan=plan, token=token, source=_(SOURCES[plan["source"]]),
                           counts=summarize(db, g.user["id"], plan),
                           sample_tasks=plan["tasks"][:5], sample_habits=plan["habits"][:8])


@bp.route("/confirm", methods=["POST"])
@login_required
def confirm():
    db = get_db()
    row = db.execute("SELECT plan FROM import_drafts WHERE user_id = ? AND token = ?",
                     (g.user["id"], request.form.get("token", ""))).fetchone()
    if row is None:
        flash(_('That import has expired. Please choose the file again.'), "error")
        return redirect(url_for("importer.index"))
    db.execute("DELETE FROM import_drafts WHERE user_id = ?", (g.user["id"],))     # can't be used twice
    counts = apply_plan(db, g.user["id"], json.loads(row["plan"]))
    db.execute("UPDATE users SET onboarded = 1 WHERE id = ?", (g.user["id"],))     # no need for the welcome tour
    db.commit()
    parts = []
    if counts["habits_new"] or counts["habit_days"]:
        parts.append(_n("{n} habit", "{n} habits", counts["habits_new"]))
        parts.append(_n("{n} habit day", "{n} habit days", counts["habit_days"]))
    if counts["tasks_new"]:
        parts.append(_n("{n} task", "{n} tasks", counts["tasks_new"]))
    if counts["checkins_new"]:
        parts.append(_n("{n} check-in", "{n} check-ins", counts["checkins_new"]))
    if counts["goals_new"]:
        parts.append(_n("{n} goal", "{n} goals", counts["goals_new"]))
    if counts["reviews_new"]:
        parts.append(_n("{n} weekly review", "{n} weekly reviews", counts["reviews_new"]))
    if parts:
        flash(_("Imported {things}. 🎉", things=", ".join(parts)), "success")
    else:
        flash(_("Nothing new to import: you already had all of it."), "success")
    return redirect(url_for("home"))


@bp.route("/cancel", methods=["POST"])
@login_required
def cancel():
    db = get_db()
    db.execute("DELETE FROM import_drafts WHERE user_id = ?", (g.user["id"],))
    db.commit()
    return redirect(url_for("importer.index"))

