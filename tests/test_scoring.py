from scoring import compute_score


def test_lowest_possible_score():
    # ratings of 1, no hours, nothing written
    assert compute_score(1, 1, 1, 0, "", "") == 12


def test_perfect_day_is_exactly_100():
    assert compute_score(5, 5, 5, 4, "Built a feature", "Shipped it") == 100


def test_hours_brackets():
    base = compute_score(3, 3, 3, 0, "", "")          # 36
    assert compute_score(3, 3, 3, 1, "", "") == base + 5
    assert compute_score(3, 3, 3, 2, "", "") == base + 15
    assert compute_score(3, 3, 3, 4, "", "") == base + 25


def test_writing_notes_adds_points():
    base = compute_score(3, 3, 3, 0, "", "")
    assert compute_score(3, 3, 3, 0, "did stuff", "") == base + 10
    assert compute_score(3, 3, 3, 0, "", "a win") == base + 5
