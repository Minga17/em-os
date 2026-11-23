"""Insights 💡: patterns in YOUR data that only EM OS can see.

EM OS holds mood, energy, focus, habits, tasks and focus sessions in one place,
so it can notice things like "your focus is higher on days you exercise".

Ground rules (on purpose):
- Only show a pattern when there's enough data AND the difference is clear.
  When we search many possibilities (every habit x 4 measures, 7 weekdays),
  some would look "real" by pure luck. So we use a permutation test: shuffle
  the days hundreds of times and check how often luck alone finds something
  this strong. Only patterns that luck rarely produces are shown.
- Words like "on days you..." and "tends to": a pattern, never a cause.
- No diagnoses, no medical or mental-health claims. If mood has been low for
  a while, we only offer a gentle link to free support.
"""
import math
import random
from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for

from i18n import N_, _, _n, current_language

from auth import login_required
from db import get_db
from habit_model import is_scheduled
from timeutil import user_today

bp = Blueprint("insights", __name__, url_prefix="/insights")

WINDOW_DAYS = 90            # look at the last 90 days
UNLOCK_CHECKINS = 14        # insights start after 14 check-ins
MIN_GROUP = 5               # each side of a comparison needs 5+ days
CLEAR_T = 2.0               # a difference must be at least this clear (Welch's t-test)...
SINGLE_T = 2.5              # ...and single checks (trend, task load, focus timer) a bit more
SHUFFLES = 999              # permutation test rounds
LUCK = 0.05                 # show a pattern only if luck finds one this strong < 5% of the time
MIN_CORRELATION = 0.4       # for "tends to go together" patterns
MAX_SHOWN = 6

METRICS = ("focus", "mood", "energy", "score")       # what we compare
# Whole sentences (not "Your {measure}…"): in other languages the words around them change
HABIT_SENTENCES = {
    ("focus", True): N_("Your focus is {amount} higher on days you do {habit}."),
    ("focus", False): N_("Your focus is {amount} lower on days you do {habit}."),
    ("mood", True): N_("Your mood is {amount} higher on days you do {habit}."),
    ("mood", False): N_("Your mood is {amount} lower on days you do {habit}."),
    ("energy", True): N_("Your energy is {amount} higher on days you do {habit}."),
    ("energy", False): N_("Your energy is {amount} lower on days you do {habit}."),
    ("score", True): N_("Your daily score is {amount} higher on days you do {habit}."),
    ("score", False): N_("Your daily score is {amount} lower on days you do {habit}."),
}
WEEKDAYS_PLURAL = [N_("Mondays"), N_("Tuesdays"), N_("Wednesdays"), N_("Thursdays"), N_("Fridays"),
                   N_("Saturdays"), N_("Sundays")]


def capitalized(text):
    return text[:1].upper() + text[1:]
SUPPORT_LINK = "https://findahelpline.com"


# ── Small, honest statistics (no extra packages) ────────────────
def mean(values):
    return sum(values) / len(values)


def variance(values):
    m = mean(values)
    return sum((v - m) ** 2 for v in values) / (len(values) - 1)


def welch_t(a, b):
    """How clearly two groups differ. |t| >= 2 is roughly 'not just luck'."""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    spread = math.sqrt(variance(a) / len(a) + variance(b) / len(b))
    if spread == 0:
        return math.inf if mean(a) != mean(b) else 0.0
    return (mean(a) - mean(b)) / spread


def correlation(xs, ys):
    mx, my = mean(xs), mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def chance_of_luck(observed, values, labels, statistic, seed):
    """Permutation test: shuffle which days are 'with' and 'without', see how often
    the statistic is at least as big as what we really saw. Same answer every time (seeded)."""
    if observed <= 0:
        return 1.0
    rng = random.Random(seed)
    labels = list(labels)
    as_big = 0
    for _shuffle in range(SHUFFLES):
        rng.shuffle(labels)
        if statistic(values, labels) >= observed:
            as_big += 1
    return (as_big + 1) / (SHUFFLES + 1)


def compare(metric, with_days, without_days, min_t=CLEAR_T):
    """Text for 'X is higher on days you...' or None if the difference isn't clear."""
    a = [d[metric] for d in with_days]
    b = [d[metric] for d in without_days]
    if len(a) < MIN_GROUP or len(b) < MIN_GROUP:
        return None
    t = welch_t(a, b)
    ma, mb = mean(a), mean(b)
    if abs(t) < min_t or mb == 0:
        return None
    if metric == "score":
        if abs(ma - mb) < 5:
            return None
        amount = _n("{n} point", "{n} points", round(abs(ma - mb)))
    else:
        if abs(ma - mb) < 0.3:
            return None
        amount = f"{abs(ma - mb) / mb * 100:.0f}%"
    return {"t": abs(t), "higher": ma > mb, "amount": amount,
            "with": round(ma, 1), "without": round(mb, 1), "n": len(a) + len(b)}


# ── Collect the days ────────────────────────────────────────────
def collect_days(db, user_id, today):
    start = today - timedelta(days=WINDOW_DAYS - 1)
    days = {}
    for r in db.execute("""SELECT log_date, energy, mood, focus, focused_hours, score FROM checkins
                           WHERE user_id = ? AND log_date BETWEEN ? AND ?""",
                        (user_id, start.isoformat(), today.isoformat())):
        days[r["log_date"]] = {"date": date.fromisoformat(r["log_date"]), "energy": r["energy"],
                               "mood": r["mood"], "focus": r["focus"], "hours": r["focused_hours"] or 0,
                               "score": r["score"]}
    focus_days = {r[0] for r in db.execute(
        "SELECT DISTINCT log_date FROM focus_sessions WHERE user_id = ? AND minutes >= 10", (user_id,))}
    tasks = db.execute("SELECT date(created_at) AS made, date(completed_at) AS done FROM tasks WHERE user_id = ?",
                       (user_id,)).fetchall()
    for key, d in days.items():
        d["focus_session"] = key in focus_days
        d["open_tasks"] = sum(1 for t in tasks if t["made"] <= key and (t["done"] is None or t["done"] > key))
    return days


# ── Find the patterns ───────────────────────────────────────────
def best_metric(rows, labels):
    """The clearest of the 4 measures for one split of days: (|t|, metric, result) or None."""
    with_it = [r for r, on in zip(rows, labels) if on]
    without = [r for r, on in zip(rows, labels) if not on]
    best = None
    for metric in METRICS:
        result = compare(metric, with_it, without)
        if result and (best is None or result["t"] > best[0]):
            best = (result["t"], metric, result)
    return best


def habit_insights(db, user_id, days):
    found = []
    habits = db.execute("SELECT * FROM habits WHERE user_id = ? AND is_active = 1 AND kind = 'build' "
                        "AND schedule != 'weekly'", (user_id,)).fetchall()
    tested = []
    for h in habits:
        done_dates = {r[0] for r in db.execute(
            "SELECT log_date FROM habit_logs WHERE habit_id = ? AND amount >= ?", (h["id"], h["goal_count"]))}
        rows = [d for d in days.values() if is_scheduled(h, d["date"]) or d["date"].isoformat() in done_dates]
        labels = [d["date"].isoformat() in done_dates for d in rows]
        if sum(labels) >= MIN_GROUP and len(labels) - sum(labels) >= MIN_GROUP:
            tested.append((h, rows, labels))

    for h, rows, labels in tested:
        best = best_metric(rows, labels)
        if not best:
            continue
        statistic = lambda rs, ls: (best_metric(rs, ls) or (0,))[0]   # noqa: E731
        luck = chance_of_luck(best[0], rows, labels, statistic, seed=h["id"])
        if luck >= LUCK / len(tested):          # stricter when there are many habits to check
            continue
        metric, r = best[1], best[2]
        detail = (_n("{a} vs {b} points · based on {n} day", "{a} vs {b} points · based on {n} days", r["n"],
                     a=r["with"], b=r["without"]) if metric == "score" else
                  _n("{a} vs {b} out of 5 · based on {n} day", "{a} vs {b} out of 5 · based on {n} days", r["n"],
                     a=r["with"], b=r["without"]))
        found.append({
            "key": f"habit:{h['id']}:{metric}", "icon": h["icon"], "strength": r["t"],
            "text": _(HABIT_SENTENCES[(metric, r["higher"])], amount=r["amount"], habit=f"{h['icon']} {h['name']}"),
            "detail": detail,
        })
    return found


def weekday_insight(days):
    by_day = defaultdict(list)
    for d in days.values():
        by_day[d["date"].weekday()].append(d["score"])
    counted = {wd: s for wd, s in by_day.items() if len(s) >= 3}
    if len(counted) < 4:
        return []
    best = max(counted, key=lambda wd: mean(counted[wd]))
    worst = min(counted, key=lambda wd: mean(counted[wd]))
    others = [s for wd, scores in counted.items() if wd != best for s in scores]
    spread = mean(counted[best]) - mean(counted[worst])
    if spread < 8:
        return []

    def weekday_spread(scores, weekdays):
        groups = defaultdict(list)
        for score, wd in zip(scores, weekdays):
            groups[wd].append(score)
        means = [mean(v) for v in groups.values() if len(v) >= 3]
        return max(means) - min(means) if len(means) >= 4 else 0

    scores = [s for wd in counted for s in counted[wd]]
    weekdays = [wd for wd in counted for _ in counted[wd]]
    if chance_of_luck(spread, scores, weekdays, weekday_spread, seed=7) >= LUCK:
        return []
    names = [_(n) for n in WEEKDAYS_PLURAL]
    return [{
        "key": "weekday", "icon": "📅", "strength": abs(welch_t(counted[best], others)),
        "text": capitalized(_("{best} tend to be your best days. {worst} are usually the hardest.",
                              best=names[best], worst=names[worst])),
        "detail": capitalized(_("Average score {a} on {best} vs {b} on {worst}", a=round(mean(counted[best])),
                                best=names[best], b=round(mean(counted[worst])), worst=names[worst])),
    }]


def task_load_insight(days):
    busy = [d for d in days.values() if d["open_tasks"] >= 5]
    calm = [d for d in days.values() if d["open_tasks"] < 5]
    r = compare("mood", busy, calm, min_t=SINGLE_T)
    if not r or r["higher"]:
        return []
    return [{
        "key": "task_load", "icon": "📋", "strength": r["t"],
        "text": _("Your mood is {amount} lower on days with 5 or more open tasks.", amount=r["amount"]),
        "detail": _("{a} vs {b} out of 5 · picking a top 3 can make a long list feel lighter",
                    a=r["with"], b=r["without"]),
    }]


def focus_session_insight(days):
    r = compare("score", [d for d in days.values() if d["focus_session"]],
                [d for d in days.values() if not d["focus_session"]], min_t=SINGLE_T)
    if not r or not r["higher"]:
        return []
    return [{
        "key": "focus_timer", "icon": "⏱", "strength": r["t"],
        "text": _("Days with a focus session score {amount} higher.", amount=r["amount"]),
        "detail": _("Average {a} vs {b}", a=r["with"], b=r["without"]),
    }]


def hours_mood_insight(days):
    rows = list(days.values())
    if len(rows) < UNLOCK_CHECKINS:
        return []
    r = correlation([d["hours"] for d in rows], [d["mood"] for d in rows])
    if abs(r) < MIN_CORRELATION:
        return []
    return [{
        "key": "hours_mood", "icon": "🧠", "strength": abs(r) * 4,
        "text": (_("Days with more focused hours tend to come with a better mood.")
                 if r > 0 else _("Long focus days tend to come with a lower mood. Breaks might help.")),
        "detail": _n("Based on {n} check-in", "Based on {n} check-ins", len(rows)),
    }]


def energy_focus_insight(days):
    rows = list(days.values())
    if len(rows) < UNLOCK_CHECKINS:
        return []
    r = correlation([d["energy"] for d in rows], [d["focus"] for d in rows])
    if r < MIN_CORRELATION + 0.1:
        return []
    return [{"key": "energy_focus", "icon": "⚡", "strength": r * 3,
             "text": _("Your focus follows your energy: high-energy days are your sharpest."),
             "detail": _("Worth protecting the things that give you energy")}]


def trend_insight(days, today):
    recent = [d["score"] for d in days.values() if (today - d["date"]).days < 14]
    before = [d["score"] for d in days.values() if 14 <= (today - d["date"]).days < 28]
    if len(recent) < MIN_GROUP or len(before) < MIN_GROUP:
        return []
    change = mean(recent) - mean(before)
    if abs(change) < 5 or abs(welch_t(recent, before)) < SINGLE_T:
        return []
    up = change > 0
    return [{
        "key": "trend", "icon": "📈" if up else "📉", "strength": abs(welch_t(recent, before)),
        "text": (_("Your average score is up {n} points over the last 2 weeks. Keep it going!", n=round(change))
                 if up else
                 _("Your average score is down {n} points over the last 2 weeks. Small steps count.", n=round(-change))),
        "detail": _("{a} vs {b} the 2 weeks before", a=round(mean(recent)), b=round(mean(before))),
    }]


def support_note(days, today):
    """Gentle and optional. Never a diagnosis."""
    recent = [d["mood"] for d in days.values() if (today - d["date"]).days < 14]
    if len(recent) >= 7 and mean(recent) <= 2.0:
        return {"text": _("Your mood has been low lately. If things feel heavy, talking to someone you trust "
                          "can help. Free, confidential support is listed for your country at findahelpline.com."),
                "link": SUPPORT_LINK}
    return None


_cache = {}       # (database, user, day, data fingerprint) -> found insights


def find_patterns(db, user_id, today):
    """The slow part (permutation tests). Remembered until the person's data changes."""
    fingerprint = db.execute(
        """SELECT (SELECT COUNT(*) || ':' || COALESCE(MAX(updated_at), '') FROM checkins WHERE user_id = :u),
                  (SELECT COUNT(*) FROM habit_logs l JOIN habits h ON h.id = l.habit_id WHERE h.user_id = :u),
                  (SELECT COUNT(*) || ':' || COALESCE(SUM(is_active), 0) FROM habits WHERE user_id = :u),
                  (SELECT COUNT(*) FROM tasks WHERE user_id = :u AND status = 'done'),
                  (SELECT COUNT(*) FROM focus_sessions WHERE user_id = :u)""", {"u": user_id}).fetchone()
    key = (current_app.config["DATABASE"], user_id, today, tuple(fingerprint), current_language())
    if key not in _cache:
        if len(_cache) > 2000:
            _cache.clear()
        days = collect_days(db, user_id, today)
        _cache[key] = (habit_insights(db, user_id, days) + weekday_insight(days) + task_load_insight(days)
                       + focus_session_insight(days) + hours_mood_insight(days) + energy_focus_insight(days)
                       + trend_insight(days, today), support_note(days, today))
    return _cache[key]


def build_insights(db, user_id, today):
    """Everything for the page: locked state, insights (best first) and an optional support note."""
    total = db.execute("SELECT COUNT(*) FROM checkins WHERE user_id = ?", (user_id,)).fetchone()[0]
    if total < UNLOCK_CHECKINS:
        return {"locked": True, "checkins": total, "needed": UNLOCK_CHECKINS, "items": [], "support": None}
    items, support = find_patterns(db, user_id, today)
    hidden = {r[0] for r in db.execute(
        "SELECT insight_key FROM insight_votes WHERE user_id = ? AND helpful = 0", (user_id,))}
    items = sorted((i for i in items if i["key"] not in hidden), key=lambda i: -i["strength"])[:MAX_SHOWN]
    return {"locked": False, "checkins": total, "needed": UNLOCK_CHECKINS, "items": items,
            "support": support}


def insight_of_the_day(db, user_id, today):
    """One insight for the Today screen, a different one each day."""
    result = build_insights(db, user_id, today)
    if result["locked"] or not result["items"]:
        return None
    return result["items"][today.toordinal() % len(result["items"])]


# ── Pages ───────────────────────────────────────────────────────
@bp.route("/")
@login_required
def index():
    db = get_db()
    result = build_insights(db, g.user["id"], user_today())
    votes = dict(db.execute("SELECT insight_key, helpful FROM insight_votes WHERE user_id = ?",
                            (g.user["id"],)).fetchall())
    return render_template("insights/index.html", result=result, votes=votes)


@bp.route("/vote", methods=["POST"])
@login_required
def vote():
    key = request.form.get("key", "")[:80]
    helpful = request.form.get("helpful")
    if not key or helpful not in ("0", "1"):
        abort(400)
    db = get_db()
    db.execute("""INSERT INTO insight_votes (user_id, insight_key, helpful) VALUES (?, ?, ?)
                  ON CONFLICT(user_id, insight_key) DO UPDATE SET helpful = excluded.helpful""",
               (g.user["id"], key, int(helpful)))
    db.commit()
    return redirect(url_for("insights.index"))


@bp.route("/reset", methods=["POST"])
@login_required
def reset():
    db = get_db()
    db.execute("DELETE FROM insight_votes WHERE user_id = ? AND helpful = 0", (g.user["id"],))
    db.commit()
    return redirect(url_for("insights.index"))
