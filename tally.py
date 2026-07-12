"""Pure vote-counting helpers. No I/O, no LLM."""

VOTE_VALUES = ("guilty", "not_guilty", "undecided")


def tally(votes):
    """votes: {seat: vote_string} -> counts per category."""
    counts = {v: 0 for v in VOTE_VALUES}
    for v in votes.values():
        counts[v] += 1
    return counts


def unanimous(counts):
    """Return the verdict string if all 12 agree, else None."""
    if counts["guilty"] == 12:
        return "guilty"
    if counts["not_guilty"] == 12:
        return "not_guilty"
    return None
