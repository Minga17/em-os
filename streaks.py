from datetime import date, timedelta


def current_streak(done_dates, today=None):
    """Days in a row, ending today (or yesterday, if today isn't done yet)."""
    today = today or date.today()
    done = set(done_dates)
    day = today if today in done else today - timedelta(days=1)
    streak = 0
    while day in done:
        streak += 1
        day -= timedelta(days=1)
    return streak


def best_streak(done_dates):
    """Longest run of consecutive days ever."""
    best = run = 0
    previous = None
    for day in sorted(set(done_dates)):
        if previous and (day - previous).days == 1:
            run += 1
        else:
            run = 1
        best = max(best, run)
        previous = day
    return best