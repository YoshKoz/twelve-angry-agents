"""Pure vote-counting helpers. No I/O, no LLM."""

# A juror who abstains on a secret ballot has not voted at all — that is a
# deliberate act on the floor, distinct from being undecided, and it can never
# add up to a unanimous verdict.
VOTE_VALUES = ("guilty", "not_guilty", "undecided", "abstain")


def tally(votes):
    """votes: {seat: vote_string} -> counts per category."""
    counts = {v: 0 for v in VOTE_VALUES}
    for v in votes.values():
        counts[v] += 1
    return counts


def unanimous(counts):
    """Return the verdict string if every juror agrees, else None."""
    total = sum(counts.values())
    if not total:
        return None
    if counts["guilty"] == total:
        return "guilty"
    if counts["not_guilty"] == total:
        return "not_guilty"
    return None
