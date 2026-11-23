"""Quick add: type a sentence, get a habit or a task.

    "Gym every Mon/Wed/Fri"          -> habit on Mon, Wed, Fri
    "Run 3 times a week"             -> habit, 3 times a week
    "Drink 8 glasses of water daily" -> habit, 8 glasses a day
    "No smoking"                     -> quit habit
    "Essay due Friday high"          -> task, due Friday, high priority
    "Pay rent monthly"               -> task that repeats every month

Understands English, Spanish, French and Portuguese, even mixed together.
Nothing is saved until the person sees the preview and taps Add.
"""
import re
import unicodedata
from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from i18n import _, _n, format_day, weekday_names

from auth import login_required
from db import get_db
from task_logic import store_repeat
from timeutil import user_today

bp = Blueprint("quick", __name__, url_prefix="/quick")

MAX_LENGTH = 120
KINDS = ("habit", "quit", "task")


def fold(text):
    """Lowercase without accents, so 'Miércoles' and 'miercoles' match the same."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def words(*options):
    return "(?:" + "|".join(sorted(options, key=len, reverse=True)) + ")"


# ── Vocabulary (written without accents; input is folded the same way) ──
WEEKDAY_NAMES = [
    ["monday", "lunes", "lundi", "segunda-feira", "segunda"],
    ["tuesday", "martes", "mardi", "terca-feira", "terca"],
    ["wednesday", "miercoles", "mercredi", "quarta-feira", "quarta"],
    ["thursday", "jueves", "jeudi", "quinta-feira", "quinta"],
    ["friday", "viernes", "vendredi", "sexta-feira", "sexta"],
    ["saturday", "sabado", "samedi"],
    ["sunday", "domingo", "dimanche"],
]
WEEKDAY_SHORT = [["mon"], ["tue", "tues"], ["wed"], ["thu", "thur", "thurs"], ["fri"], ["sat"], ["sun"]]
WEEKDAY_LOOKUP = {name: i for i, names in enumerate(WEEKDAY_NAMES) for name in names}
WEEKDAY_LOOKUP.update({name: i for i, names in enumerate(WEEKDAY_SHORT) for name in names})
WEEKDAY = words(*WEEKDAY_LOOKUP)
FULL_WEEKDAY = words(*(n for names in WEEKDAY_NAMES for n in names))

MONTHS = {
    1: ["january", "jan", "enero", "janvier", "janeiro"],
    2: ["february", "feb", "febrero", "fevrier", "fevereiro", "fev"],
    3: ["march", "marzo", "mars", "marco"],
    4: ["april", "apr", "abril", "avril", "avr"],
    5: ["may", "mayo", "mai", "maio"],
    6: ["june", "jun", "junio", "juin", "junho"],
    7: ["july", "jul", "julio", "juillet", "julho"],
    8: ["august", "aug", "agosto", "aout"],
    9: ["september", "sept", "sep", "septiembre", "setiembre", "septembre", "setembro", "set"],
    10: ["october", "oct", "octubre", "octobre", "outubro", "out"],
    11: ["november", "nov", "noviembre", "novembre", "novembro"],
    12: ["december", "dec", "diciembre", "dic", "decembre", "dezembro", "dez"],
}
MONTH_LOOKUP = {name: n for n, names in MONTHS.items() for name in names}
MONTH = words(*MONTH_LOOKUP)

NUMBER_WORDS = {
    "once": 1, "one": 1, "una": 1, "un": 1, "une": 1, "uma": 1, "um": 1,
    "twice": 2, "two": 2, "dos": 2, "deux": 2, "duas": 2, "dois": 2,
    "three": 3, "thrice": 3, "tres": 3, "trois": 3,
    "four": 4, "cuatro": 4, "quatre": 4, "quatro": 4,
    "five": 5, "cinco": 5, "cinq": 5,
    "six": 6, "seis": 6,
}
NUMBER = r"(\d+|" + words(*NUMBER_WORDS) + ")"

DAILY = words(
    "every day", "everyday", "daily", "each day", "a day", "per day",
    "every morning", "every night", "every evening", "each morning", "each night",
    "a diario", "diariamente", "diario", "cada dia", "todos los dias", "al dia", "por dia",
    "cada manana", "cada noche", "cada tarde", "todas las mananas", "todas las noches",
    "quotidiennement", "quotidien", "chaque jour", "tous les jours", "par jour",
    "chaque matin", "chaque soir", "tous les matins", "tous les soirs",
    "todo dia", "todos os dias", "toda manha", "todas as manhas", "toda noite", "todas as noites", "ao dia",
)
WEEKDAYS_ONLY = words(
    "every weekday", "on weekdays", "weekdays", "entre semana", "dias laborables", "dias de semana",
    "en semaine", "les jours de semaine", "jours ouvrables", "dias uteis", "durante a semana",
)
WEEKENDS = words(
    "every weekend", "on weekends", "weekends", "los fines de semana", "cada fin de semana",
    "le week-end", "les week-ends", "chaque week-end", "nos fins de semana", "todo fim de semana",
)
TIMES = words("times", "time", "x", "veces", "vez", "fois", "vezes")
WEEK = words("a week", "per week", "each week", "every week", "/week", "/wk", "a la semana", "por semana",
             "a semana", "par semaine", "na semana", "por semana")
WEEKLY = words("weekly", "every week", "each week", "semanalmente", "semanal", "cada semana",
               "todas las semanas", "hebdomadaire", "chaque semaine", "toutes les semaines",
               "toda semana", "todas as semanas")
MONTHLY = words("monthly", "every month", "each month", "mensualmente", "mensual", "cada mes",
                "todos los meses", "mensuellement", "mensuel", "chaque mois", "tous les mois",
                "mensalmente", "mensal", "todo mes", "todos os meses")
EVERY = words("every", "each", "cada", "chaque", "tous les", "toutes les", "todos os", "todas as",
              "todos los", "todas las", "toda", "todo", "los", "les", "as", "aos", "on")
QUIT_START = words("no more", "no", "quit", "stop", "avoid", "don't", "dont", "never",
                   "dejar de", "deja de", "no mas", "nada de", "sin",
                   "arreter de", "arreter", "pas de", "plus de", "sans", "jamais",
                   "parar de", "largar", "sem", "nada de")
HIGH = words("high priority", "priority high", "urgent", "important", "asap",
             "alta prioridad", "prioridad alta", "urgente", "importante",
             "haute priorite", "priorite haute", "prioridade alta", "alta prioridade", "p1")
LOW = words("low priority", "priority low", "baja prioridad", "prioridad baja",
            "basse priorite", "priorite basse", "baixa prioridade", "prioridade baixa", "p3")
TRAILING_LEVEL = {"high": 1, "alta": 1, "haute": 1, "low": 3, "baja": 3, "basse": 3, "baixa": 3,
                  "medium": 2, "p2": 2}
DUE_WORDS = words("due on", "due by", "due", "by", "on", "before", "until",
                  "para el", "para", "el", "hasta el", "antes del", "antes de", "pour le", "pour", "avant le",
                  "avant", "d'ici", "le", "ate", "na", "no", "em", "a")
STOP_AFTER_NUMBER = {"of", "de", "a", "per", "times", "time", "x", "veces", "fois", "vezes"}

ICONS = [
    ("🏋️", ["gym", "workout", "lift", "gimnasio", "pesas", "muscu", "salle de sport", "academia", "treino"]),
    ("🏃", ["run", "jog", "correr", "courir", "course", "corrida"]),
    ("🏊", ["swim", "nadar", "nager", "natacao", "natation", "pool", "piscina"]),
    ("🚶", ["walk", "steps", "caminar", "pasos", "marcher", "marche", "caminhar", "passos"]),
    ("💧", ["water", "agua", "eau"]),
    ("📚", ["read", "book", "leer", "libro", "lire", "livre", "ler ", "livro"]),
    ("🧘", ["meditat", "yoga", "meditar", "mediter", "respir", "breath"]),
    ("😴", ["sleep", "bed", "dormir", "coucher", "cama"]),
    ("🚭", ["smok", "cigar", "fumar", "fumer", "vape"]),
    ("🍺", ["alcohol", "beer", "alcool", "cerveza", "biere", "cerveja", "wine", "vino", "vin ", "vinho"]),
    ("🍬", ["sugar", "sweets", "candy", "azucar", "sucre", "acucar", "dulces", "doces"]),
    ("📵", ["phone", "social media", "instagram", "tiktok", "scroll", "telefono", "telephone", "celular", "redes"]),
    ("🎓", ["study", "class", "homework", "estudiar", "etudier", "devoirs", "estudar", "tarea"]),
    ("💻", ["code", "coding", "program", "leetcode", "programar", "coder"]),
    ("✍️", ["journal", "write", "escribir", "ecrire", "escrever"]),
    ("💊", ["vitamin", "pill", "medic", "vitamina", "medicament", "remedio"]),
    ("🤸", ["stretch", "estirar", "etirer", "alongar"]),
    ("🧹", ["clean", "tidy", "limpiar", "nettoyer", "ranger", "limpar"]),
    ("🍳", ["cook", "cocinar", "cuisiner", "cozinhar"]),
    ("🎸", ["guitar", "piano", "music", "guitarra", "musica", "musique", "practice"]),
    ("🗣️", ["spanish", "english", "french", "portuguese", "language", "duolingo", "idioma", "langue", "ingles", "anglais"]),
    ("🙏", ["pray", "rezar", "prier", "orar", "bible", "biblia"]),
    ("💰", ["save money", "budget", "ahorrar", "economiser", "poupar", "rent", "pay", "pagar", "payer", "loyer", "aluguel", "alquiler"]),
]


EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿][️‍\U0001F300-\U0001FAFF♀♂]*")


def guess_icon(folded):
    """A keyword at the start of a word: 'run' finds 'running', but 'ler' doesn't find 'alquiler'."""
    for icon, keys in ICONS:
        if any(re.search(r"(?<!\w)" + re.escape(key.strip()), folded) for key in keys):
            return icon
    return "✅"


def number(token):
    return int(token) if token.isdigit() else NUMBER_WORDS[token]


def next_weekday(today, weekday):
    return today + timedelta(days=(weekday - today.weekday()) % 7)


# ── The parser ──────────────────────────────────────────────────
class Reader:
    """Finds phrases in the folded text and remembers where they were, to cut them from the name."""

    def __init__(self, text):
        self.original = text
        chars, self.source = [], []          # source[i] = position in the original text
        for i, c in enumerate(text):
            for f in fold(c):
                chars.append(f)
                self.source.append(i)
        self.folded = "".join(chars)
        self.cut = []

    def find(self, pattern):
        for match in re.finditer(pattern, self.folded):
            if not any(a < match.end() and match.start() < b for a, b in self.cut):
                return match
        return None

    def take(self, pattern):
        match = self.find(pattern)
        if match:
            self.cut.append(match.span())
        return match

    def rest(self):
        gone = {self.source[i] for a, b in self.cut for i in range(a, b)}
        name = "".join(c for i, c in enumerate(self.original) if i not in gone)
        name = re.sub(r"\s+", " ", name)
        name = re.sub(r"\s+([,.!?;:])", r"\1", name)
        name = name.strip(" ,.;:-/!&+").strip()
        name = re.sub(r"\s+(?:and|y|et|e|of|de)$", "", name).strip(" ,")
        return name[:1].upper() + name[1:]


def b(pattern):
    """Whole words only (letters/digits on either side don't count)."""
    return r"(?<![\w'])" + pattern + r"(?![\w])"


def parse(text, today):
    """Read a sentence. Returns everything the preview and the add button need."""
    text = " ".join(text.split())[:MAX_LENGTH]
    r = Reader(text)
    result = {"text": text, "habit_signal": False, "quit": False, "schedule": "daily", "days": [],
              "per_week": 3, "goal_count": 1, "unit": "", "repeat": "", "due_date": None, "priority": 2}

    # 1. Quit habits: "No smoking", "Stop biting nails", "Dejar de fumar"
    if r.find(r"^" + b(QUIT_START)):
        result["quit"] = result["habit_signal"] = True

    # 2. Priority: "urgent", "high priority", "!!!"
    if r.take(b(HIGH)):
        result["priority"] = 1
    elif r.take(b(LOW)):
        result["priority"] = 3
    if r.find(r"!{1,3}\s*$"):
        r.take(r"!{1,3}\s*$")
        result["priority"] = 1

    # 3. How often (habits): "3 times a week", "weekdays", "every Mon/Wed/Fri", "daily"
    per_week = r.take(b(NUMBER + r"\s*" + TIMES + r"?\s*" + WEEK))
    if per_week:
        n = number(per_week.group(1))
        result["habit_signal"] = True
        if n >= 7:
            result["schedule"] = "daily"
        else:
            result["schedule"], result["per_week"] = "weekly", max(1, n)
    elif r.take(b(WEEKDAYS_ONLY)):
        result.update(habit_signal=True, schedule="days", days=[0, 1, 2, 3, 4], repeat="weekdays")
    elif r.take(b(WEEKENDS)):
        result.update(habit_signal=True, schedule="days", days=[5, 6])
    else:
        days = weekday_list(r)
        if days:
            result.update(habit_signal=True, schedule="days", days=days)

    times_a_day = r.take(b(NUMBER + r"\s*" + TIMES + r"\s*" + words("a day", "per day", "al dia", "por dia",
                                                                       "par jour", "ao dia", "daily")))
    if times_a_day:
        result.update(habit_signal=True, goal_count=min(100, number(times_a_day.group(1))), unit="times")
    elif r.take(b(DAILY)):
        result["habit_signal"] = True

    # 4. Repeating tasks: "Pay rent monthly", "Clean room weekly"
    if r.take(b(MONTHLY)):
        result["repeat"] = "monthly"
    elif r.take(b(WEEKLY)):
        result["repeat"] = "weekly"

    # 5. Amount per day: "8 glasses of water", "20 pages" (only for habits)
    if result["habit_signal"] and not result["quit"] and result["goal_count"] == 1:
        amount = r.find(b(r"(\d{1,3})\s+([a-z\-]+)"))
        if amount and 2 <= int(amount.group(1)) <= 100 and amount.group(2) not in STOP_AFTER_NUMBER:
            unit = r.original[r.source[amount.start(2)]:r.source[amount.end(2) - 1] + 1]
            result["goal_count"], result["unit"] = int(amount.group(1)), unit[:20]

    # 6. A last word like "high" or "low" sets the priority of a task
    if not result["habit_signal"] and result["priority"] == 2:
        last = r.find(r"(?<=\s)(" + words(*TRAILING_LEVEL) + r")\s*$")
        if last:
            r.cut.append(last.span(1))
            result["priority"] = TRAILING_LEVEL[last.group(1)]

    # 7. Due date (tasks): "tomorrow", "Friday", "Nov 30", "in 3 days"
    if not result["habit_signal"]:
        result["due_date"] = due_date(r, today)

    typed_icon = EMOJI.search(text)
    if typed_icon:                                   # an emoji they typed becomes the icon
        start = typed_icon.start()
        r.cut.append((r.source.index(start), r.source.index(start) + len(fold(typed_icon.group(0)))))
    result["name"] = r.rest() or text
    result["icon"] = typed_icon.group(0)[:4] if typed_icon else guess_icon(fold(result["name"]))
    result["kind"] = "quit" if result["quit"] else ("habit" if result["habit_signal"] else "task")
    return result


def weekday_list(r):
    """'every Mon/Wed/Fri', 'on Mondays and Thursdays', 'Tue, Thu'. A single plain 'Friday' is a due date."""
    sep = r"(?:\s*(?:,|/|&|\+|\band\b|\by\b|\bet\b|\be\b)\s*|\s+)"
    match = r.find(b("(" + EVERY + r"\s+)?" + WEEKDAY + "s?" + "(?:" + sep + WEEKDAY + "s?)*"))
    if not match:
        return []
    found = re.findall(r"(?<![\w'])(" + WEEKDAY + r")(s?)(?!\w)", match.group(0))
    days = sorted({WEEKDAY_LOOKUP[name] for name, _s in found})
    prefix = (match.group(1) or "").strip()
    plural = any(s for _name, s in found)
    if len(days) >= 2 or (prefix and prefix != "on") or plural:
        r.cut.append(match.span())
        return days
    return []


def due_date(r, today):
    lead = r"(?:" + DUE_WORDS + r"\s+)?"

    def take(pattern):
        return r.take(b(lead + pattern))

    if take(words("day after tomorrow", "pasado manana", "apres-demain", "apres demain", "depois de amanha")):
        return (today + timedelta(days=2)).isoformat()
    if take(words("tomorrow", "tmrw", "demain", "amanha") + "|(?<!la )manana"):
        return (today + timedelta(days=1)).isoformat()
    if take(words("today", "tonight", "hoy", "esta noche", "aujourd'hui", "ce soir", "hoje")):
        return today.isoformat()
    if take(words("next week", "la semana que viene", "la proxima semana", "semana que viene",
                  "la semaine prochaine", "semaine prochaine", "semana que vem", "proxima semana")):
        return (today + timedelta(days=7 - today.weekday())).isoformat()
    if take(words("this weekend", "este fin de semana", "ce week-end", "este fim de semana")):
        return next_weekday(today, 5).isoformat()
    match = take(words("in", "en", "dans", "em") + r"\s+(\d{1,3})\s+" + words("days", "day", "dias", "dia", "jours", "jour"))
    if match:
        return (today + timedelta(days=int(match.group(1)))).isoformat()
    match = take(r"(\d{4})-(\d{2})-(\d{2})")
    if match:
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            pass
    match = take(r"(\d{1,2})(?:st|nd|rd|th|er)?\s+(?:de\s+)?" + MONTH + r"\.?") \
        or take(MONTH + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?")
    if match:
        found = match.group(0)
        day_num = int(re.search(r"\d{1,2}", found).group(0))
        month = MONTH_LOOKUP[re.search(b(MONTH), found).group(0)]
        for year in (today.year, today.year + 1):
            try:
                d = date(year, month, day_num)
            except ValueError:
                return None
            if d >= today:
                return d.isoformat()
    match = take(FULL_WEEKDAY + "s?") or r.take(b(DUE_WORDS + r"\s+" + words(*(n for names in WEEKDAY_SHORT for n in names))))
    if match:
        name = re.search(b(WEEKDAY), match.group(0)).group(0)
        return next_weekday(today, WEEKDAY_LOOKUP[name]).isoformat()
    return None


# ── Turning a reading into rows ─────────────────────────────────
def habit_fields(p, kind):
    fields = {"name": p["name"][:60], "icon": p["icon"], "kind": "quit" if kind == "quit" else "build",
              "schedule": p["schedule"], "days": ",".join(map(str, p["days"])), "per_week": p["per_week"],
              "goal_count": p["goal_count"], "unit": p["unit"]}
    if kind == "quit":
        fields.update(schedule="daily", days="", goal_count=1, unit="", icon="🚫" if p["icon"] == "✅" else p["icon"])
    elif not p["habit_signal"] and p["repeat"] == "weekly":
        fields.update(schedule="weekly", per_week=1)
    return fields


def task_fields(p):
    repeat = p["repeat"]
    if not repeat and p["habit_signal"] and not p["quit"]:
        if p["schedule"] == "daily":
            repeat = "daily"
        elif p["days"] == [0, 1, 2, 3, 4]:
            repeat = "weekdays"
        elif p["schedule"] == "weekly" and p["per_week"] == 1:
            repeat = "weekly"
    return {"title": p["name"][:200], "priority": p["priority"], "due_date": p["due_date"], "repeat": repeat}


def describe(p, kind, today):
    """The chips under the preview, e.g. ['Mon, Wed, Fri'] or ['Due Fri, Nov 27', 'High priority']."""
    if kind in ("habit", "quit"):
        f = habit_fields(p, kind)
        if kind == "quit":
            return [_("Quit habit"), _("Track clean days")]
        if f["schedule"] == "days":
            names = weekday_names()
            when = _("Weekdays") if f["days"] == "0,1,2,3,4" else ", ".join(names[int(d)] for d in f["days"].split(","))
        elif f["schedule"] == "weekly":
            when = _n("Once a week", "{n} times a week", f["per_week"])
        else:
            when = _("Every day")
        chips = [when]
        if f["goal_count"] > 1:
            chips.append(_("{n} {unit} a day", n=f["goal_count"], unit=f["unit"] or _("times")))
        return chips
    t = task_fields(p)
    chips = []
    if t["due_date"]:
        d = date.fromisoformat(t["due_date"])
        chips.append(_("Due today") if d == today else _("Due tomorrow") if d == today + timedelta(days=1)
                     else _("Due {date}", date=format_day(d, "medium")))
    if t["priority"] != 2:
        chips.append(_("High priority") if t["priority"] == 1 else _("Low priority"))
    if t["repeat"]:
        chips.append({"daily": _("Repeats daily"), "weekdays": _("Repeats on weekdays"), "weekly": _("Repeats weekly"),
                      "monthly": _("Repeats monthly")}[t["repeat"]])
    return chips or [_("No due date")]


SAFE_NEXT = {"home": "home", "tasks": "tasks.index", "habits": "habits.index"}


def read_request(source):
    text = (source.get("text") or "").strip()
    kind = source.get("kind", "auto")
    if kind not in KINDS + ("auto",):
        abort(400)
    return text, kind


@bp.route("/preview")
@login_required
def preview():
    text, kind = read_request(request.args)
    if not text:
        return ""
    today = user_today()
    p = parse(text, today)
    kind = p["kind"] if kind == "auto" else kind
    name = p["name"]
    existing = None
    if kind in ("habit", "quit"):
        existing = get_db().execute("SELECT 1 FROM habits WHERE user_id = ? AND lower(name) = lower(?) AND is_active = 1",
                                    (g.user["id"], name[:60])).fetchone()
    return render_template("quick/_preview.html", p=p, kind=kind, chips=describe(p, kind, today),
                           guessed=p["kind"], exists=bool(existing))


@bp.route("/add", methods=["POST"])
@login_required
def add():
    text, kind = read_request(request.form)
    back = url_for(SAFE_NEXT.get(request.form.get("next"), "home"))
    if not text:
        flash(_('Type something to add first.'), "error")
        return redirect(back)
    p = parse(text, user_today())
    kind = p["kind"] if kind == "auto" else kind
    db = get_db()
    if kind in ("habit", "quit"):
        fields = habit_fields(p, kind)
        db.execute("""INSERT INTO habits (user_id, name, icon, kind, schedule, days, per_week, goal_count, unit)
                      VALUES (:user_id, :name, :icon, :kind, :schedule, :days, :per_week, :goal_count, :unit)""",
                   {"user_id": g.user["id"], **fields})
        db.commit()
        flash(_('Habit added: {icon} {name}', icon=fields['icon'], name=fields['name']), "success")
    else:
        t = task_fields(p)
        db.execute("""INSERT INTO tasks (user_id, title, priority, due_date, status, repeat)
                      VALUES (?, ?, ?, ?, 'todo', ?)""",
                   (g.user["id"], t["title"], t["priority"], t["due_date"], store_repeat(t["repeat"], t["due_date"])))
        db.commit()
        flash(_('Task added: {title}', title=t['title']), "success")
    return redirect(back)
