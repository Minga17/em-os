"""Streaks that forgive.

The rules (the same everywhere in EM OS):

1. Never miss twice. Missing ONE day doesn't break a streak, as long as you
   come back the next day. Missing TWO days in a row does.
2. A missed day doesn't add to the streak. The streak counts days you did it.
3. Today never breaks anything: until midnight, today is still "in progress".
4. Frozen days (streak freezes, see freezes.py) count as kept, but don't add.
5. Habits on certain days only skip the other days completely.
6. "N times a week" habits count weeks instead of days, with the same rules.
"""
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class Streak:
    current: int = 0      # the streak right now
    best: int = 0         # the longest ever
    at_risk: bool = False  # missed the last day: today keeps it alive, missing today breaks it


def _walk(units, done, frozen, current_unit):
    """Shared logic for days or weeks. `units` are in order, ending with current_unit."""
    streak = Streak()
    misses = 0
    for unit in units:
        if unit in done:
            streak.current += 1
            misses = 0
        elif unit in frozen:
            misses = 0                       # protected, but doesn't add
        elif unit == current_unit:
            pass                             # still in progress
        else:
            misses += 1
            if misses >= 2:                  # missed twice in a row: start over
                streak.current = 0
        streak.best = max(streak.best, streak.current)
    streak.at_risk = streak.current > 0 and misses == 1 and current_unit not in done
    return streak


def day_streak(done_dates, today, frozen=(), scheduled=None):
    """Streak in days. `scheduled(day)` says which days count (default: every day)."""
    done = {d for d in done_dates if d <= today}
    if not done:
        return Streak()
    frozen = set(frozen)
    scheduled = scheduled or (lambda day: True)
    days, day = [], min(done)
    while day <= today:
        if scheduled(day) or day in done:    # doing it on a day off still counts
            days.append(day)
        day += timedelta(days=1)
    return _walk(days, done, frozen, today)


def week_start(day):
    return day - timedelta(days=day.weekday())      # Monday


def week_streak(done_dates, today, per_week):
    """Streak in weeks for 'N times a week' habits: weeks where you reached N."""
    counts = {}
    for d in done_dates:
        if d <= today:
            counts[week_start(d)] = counts.get(week_start(d), 0) + 1
    met = {week for week, n in counts.items() if n >= per_week}
    if not counts:
        return Streak()
    weeks, week = [], min(counts)
    while week <= week_start(today):
        weeks.append(week)
        week += timedelta(days=7)
    return _walk(weeks, met, set(), week_start(today))


# Short helpers used all over the app
def current_streak(done_dates, today, frozen=()):
    return day_streak(done_dates, today, frozen).current


def best_streak(done_dates, today=None, frozen=()):
    done = set(done_dates)
    if not done:
        return 0
    return day_streak(done, today or max(done), frozen).best


def clean_streak(start, slips, today):
    """For 'quit' habits: days clean since the start or the last slip."""
    slips = sorted(d for d in slips if start <= d <= today)
    current = (today - (slips[-1] if slips else start)).days
    best, previous = current, start
    for slip in slips:
        best = max(best, (slip - previous).days)
        previous = slip
    return Streak(current=current, best=best)
