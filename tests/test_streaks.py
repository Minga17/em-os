from datetime import date, timedelta

from streaks import best_streak, current_streak

TODAY = date(2026, 11, 16)


def days_ago(*numbers):
    return [TODAY - timedelta(days=n) for n in numbers]


def test_no_history_means_no_streak():
    assert current_streak([], TODAY) == 0
    assert best_streak([]) == 0


def test_three_days_in_a_row_including_today():
    assert current_streak(days_ago(0, 1, 2), TODAY) == 3


def test_streak_survives_until_today_is_over():
    # Done yesterday and before, not yet today: streak still counts
    assert current_streak(days_ago(1, 2, 3), TODAY) == 3


def test_a_missed_day_breaks_the_streak():
    assert current_streak(days_ago(0, 1, 3, 4, 5), TODAY) == 2


def test_streak_is_zero_after_missing_yesterday_and_today():
    assert current_streak(days_ago(2, 3, 4), TODAY) == 0


def test_best_streak_finds_the_longest_run():
    history = days_ago(0, 1, 5, 6, 7, 8, 20)
    assert best_streak(history) == 4


def test_duplicates_are_ignored():
    assert best_streak(days_ago(0, 0, 1, 1)) == 2
