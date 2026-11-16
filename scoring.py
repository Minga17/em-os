def compute_score(energy, mood, focus, focused_hours, accomplished, wins):
    """Daily score from 0 to 100.

    Ratings (energy + mood + focus): up to 60 points
    Focused hours:                   up to 25 points
    Wrote what you accomplished:     10 points
    Wrote a win:                      5 points
    """
    score = (energy + mood + focus) * 4

    if focused_hours >= 4:
        score += 25
    elif focused_hours >= 2:
        score += 15
    elif focused_hours > 0:
        score += 5

    if accomplished:
        score += 10
    if wins:
        score += 5

    return min(score, 100)