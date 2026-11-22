"""The streak rules: never miss twice, freezes, certain days, times a week, quitting."""
from datetime import date, timedelta

from streaks import best_streak, clean_streak, current_streak, day_streak, week_streak

TODAY = date(2026, 11, 16)          # a Monday


def days_ago(*numbers):
    return [TODAY - timedelta(days=n) for n in numbers]


def test_no_history_means_no_streak():
    assert current_streak([], TODAY) == 0
    assert best_streak([]) == 0


def test_three_days_in_a_row_including_today():
    assert current_streak(days_ago(0, 1, 2), TODAY) == 3


def test_today_is_still_in_progress():
    assert current_streak(days_ago(1, 2, 3), TODAY) == 3


def test_one_missed_day_is_forgiven():
    """Never miss twice: a single gap doesn't break it (the gap doesn't count)."""
    assert current_streak(days_ago(0, 1, 3, 4, 5), TODAY) == 5


def test_two_missed_days_in_a_row_break_it():
    assert current_streak(days_ago(0, 3, 4, 5), TODAY) == 1


def test_missing_yesterday_puts_the_streak_at_risk():
    streak = day_streak(days_ago(2, 3, 4), TODAY)
    assert streak.current == 3 and streak.at_risk
    assert not day_streak(days_ago(0, 2, 3), TODAY).at_risk      # done today: safe again


def test_missing_yesterday_and_today_ends_it_tomorrow():
    tomorrow = TODAY + timedelta(days=1)
    assert current_streak(days_ago(2, 3, 4), tomorrow) == 0


def test_every_other_day_keeps_going():
    assert current_streak(days_ago(0, 2, 4, 6, 8), TODAY) == 5


def test_a_frozen_day_bridges_two_misses():
    history = days_ago(0, 3, 4)            # missed 1 and 2 days ago
    assert current_streak(history, TODAY) == 1
    assert current_streak(history, TODAY, frozen=days_ago(1)) == 3


def test_best_streak_finds_the_longest_run():
    history = days_ago(0, 1, 5, 6, 7, 8, 20)
    assert best_streak(history) == 4
    assert best_streak(days_ago(0, 0, 1, 1)) == 2     # duplicates ignored


def test_certain_days_only_skip_the_rest():
    """Mon/Wed/Fri habit: weekends and Tue/Thu don't count against you."""
    mwf = lambda d: d.weekday() in (0, 2, 4)  # noqa: E731
    history = days_ago(0, 3, 5, 7)            # Mon, Fri, Wed, Mon
    assert day_streak(history, TODAY, scheduled=mwf).current == 4
    assert day_streak(history, TODAY, scheduled=lambda d: True).current == 1


def test_doing_it_on_a_day_off_still_counts():
    weekdays = lambda d: d.weekday() < 5     # noqa: E731
    assert day_streak(days_ago(0, 1, 2), TODAY, scheduled=weekdays).current == 3   # Mon + Sun + Sat


def test_times_a_week_counts_weeks():
    # Target 2 a week: last 3 weeks hit it, this week has 1 so far (still in progress)
    history = days_ago(0, 1, 3, 8, 10, 15, 17)
    streak = week_streak(history, TODAY, per_week=2)
    assert streak.current == 3


def test_one_missed_week_is_forgiven_two_are_not():
    # Weeks starting: Oct 19 met, Oct 26 missed, Nov 2 met, Nov 9 missed, Nov 16 (now) in progress
    streak = week_streak(days_ago(28, 27, 14, 13), TODAY, per_week=2)
    assert streak.current == 2 and streak.at_risk
    # Oct 19 met, Oct 26 met, then Nov 2 and Nov 9 both missed: over
    assert week_streak(days_ago(28, 27, 21, 20), TODAY, per_week=2).current == 0


def test_quit_habit_counts_days_clean():
    start = TODAY - timedelta(days=30)
    assert clean_streak(start, [], TODAY).current == 30
    slips = [TODAY - timedelta(days=10)]
    streak = clean_streak(start, slips, TODAY)
    assert streak.current == 10 and streak.best == 20
    assert clean_streak(start, [TODAY], TODAY).current == 0
