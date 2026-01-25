"""
scoring.py
==========

Everything about turning a classified statement into numbers lives here. Two
halves, kept in one file because they were always tightly coupled -- the diff
logic is meaningless without the schema it diffs against.

PART 1 -- SCHEMA: the fixed menu the LLM must choose from (in extract.py) and the
ordinal maps that turn each choice into a number. This is the single source of
truth for WHAT the LLM is allowed to say and HOW we score it.

PART 2 -- SCORE: pure functions that turn a validated record into a single index,
and that compare two records (statement vs the one before it) to get a change.
Nothing here touches the network or a database -- given the same record, the
output is always the same. That is exactly why this file is the one under
property-based tests (test_properties.py).

Convention used everywhere: positive = hawkish (leaning tighter policy),
negative = dovish (leaning easier), zero = neutral.
"""

# =============================================================================
# PART 1: SCHEMA
# =============================================================================

SCHEMA_VERSION = "v1"

# ---------------------------------------------------------------------------
# The ordinal maps. Each field maps a small set of words the LLM must choose
# from onto an integer. Symmetric ranges so "all hawkish" and "all dovish" are
# mirror images of each other.
# ---------------------------------------------------------------------------
ORDINALS = {
    # How the statement characterises inflation. More worry -> more reason to
    # tighten -> more hawkish.
    "inflation_stance": {
        "too_low": -2,    # inflation running clearly below target, a reason to ease
        "soft": -1,       # subdued / declining
        "balanced": 0,    # near target / no strong signal
        "firming": +1,    # picking up
        "elevated": +2,   # clearly above target / "much too high"
    },
    # How the statement characterises growth and the labour market. A hot
    # economy supports tightening, so stronger = more hawkish.
    "growth_labor_stance": {
        "weak": -2,
        "slowing": -1,
        "moderate": 0,
        "solid": +1,
        "strong": +2,
    },
    # The actual policy decision this meeting. A fact, not a vibe, but it still
    # sits on the same scale so it fits naturally into the sum.
    "rate_action": {
        "cut_50plus": -2,
        "cut_25": -1,
        "hold": 0,
        "hike_25": +1,
        "hike_50plus": +2,
    },
    # Balance-sheet language. QE (buying assets) is dovish; QT / runoff is
    # hawkish. Kept ternary because the statement language here is coarse.
    "balance_sheet": {
        "expanding": -1,  # active asset purchases / QE
        "neutral": 0,     # no change / not mentioned
        "runoff": +1,     # reducing the balance sheet / QT
    },
    # Forward guidance: what the statement signals about FUTURE moves. Often
    # the part that matters most for markets.
    "forward_guidance": {
        "easing_bias": -2,     # explicitly signalling more cuts
        "lean_easing": -1,
        "neutral": 0,
        "lean_tightening": +1,
        "tightening_bias": +2, # "ongoing increases will be appropriate" etc.
    },
    # Net direction of any formal dissent. A hawkish dissent (wanted MORE
    # tightening) is a small extra hawkish signal, and vice versa. Weighted low.
    "dissent_direction": {
        "dovish": -1,
        "none": 0,
        "hawkish": +1,
    },
}

SCORED_FIELDS = list(ORDINALS.keys())

# Equal weights on purpose. I did NOT tune these against market data -- that
# would be overfitting on ~80 statements and impossible to defend in an
# interview. This dict is the one line someone would change to argue a
# different weighting, and I can show the score is robust to that.
WEIGHTS = {field: 1.0 for field in SCORED_FIELDS}

# Extra fields the LLM returns that are stored but NOT scored:
#   - dissent_count: integer, just for context / sanity checks
#   - quotes: {field: short supporting quote} -- my cheap validation hook, lets
#     me spot-check the model against the source text.
UNSCORED_FIELDS = ["dissent_count", "quotes"]


def raw_score_bounds():
    """Smallest and largest possible raw (un-normalised) score.

    Used to check the index never escapes [-1, 1], and to normalise it.
    Computed from ORDINALS/WEIGHTS so it stays correct if I edit those.
    """
    lo = sum(WEIGHTS[f] * min(ORDINALS[f].values()) for f in SCORED_FIELDS)
    hi = sum(WEIGHTS[f] * max(ORDINALS[f].values()) for f in SCORED_FIELDS)
    return lo, hi


def validate_record(record):
    """Check a record only uses allowed categories. Raises ValueError if not.

    Called on every LLM response BEFORE trusting it (see extract.py). If the
    model returns a category that isn't on the menu, I want a loud failure,
    not a silent 0.
    """
    for field in SCORED_FIELDS:
        if field not in record:
            raise ValueError(f"record is missing field '{field}'")
        if record[field] not in ORDINALS[field]:
            allowed = list(ORDINALS[field])
            raise ValueError(
                f"field '{field}' has value '{record[field]}', "
                f"which is not one of {allowed}"
            )
    return True


def record_to_index(record):
    """Turn a validated record into a single hawkish/dovish index in [-1, 1].

    +1 = maximally hawkish, -1 = maximally dovish, 0 = neutral. Just a
    weighted sum of the ordinals, rescaled. No magic.
    """
    validate_record(record)
    raw = sum(WEIGHTS[f] * ORDINALS[f][record[f]] for f in SCORED_FIELDS)
    lo, hi = raw_score_bounds()
    # Rescale [lo, hi] -> [-1, 1]. Since lo == -hi here it's just raw / hi,
    # but written the general way so it survives me changing the weights.
    return 2 * (raw - lo) / (hi - lo) - 1


# =============================================================================
# PART 2: SCORE (diff between two statements)
# =============================================================================
#
# Two quantities matter:
#
# 1. The LEVEL: how hawkish/dovish a single statement is (record_to_index,
#    above). 2. The CHANGE vs the previous statement -- the more interesting
#    one. Markets respond to the SURPRISE relative to expectations, and the
#    change in the Fed's own language is a (rough) proxy for that surprise.
#    The level alone should NOT be expected to line up cleanly with the next-
#    day yield move; the change should line up better. analyze.py leans on
#    this distinction.

index_of = record_to_index  # alias so callers can do `from scoring import index_of`


def change_vector(prev_record, curr_record):
    """Per-field change in ordinal value, current minus previous.

    Returns a dict {field: int}. Positive means the language moved in the
    hawkish direction on that field since last meeting.

    Example: inflation went 'firming' (+1) -> 'elevated' (+2) gives +1.
    """
    out = {}
    for field in SCORED_FIELDS:
        prev_val = ORDINALS[field][prev_record[field]]
        curr_val = ORDINALS[field][curr_record[field]]
        out[field] = curr_val - prev_val
    return out


def delta_index(prev_record, curr_record):
    """Change in the overall index from one statement to the next.

    This single number is my "hawkish/dovish shift" signal -- the thing I
    expect to relate (noisily, with caveats) to the move in rates around the
    meeting.
    """
    return record_to_index(curr_record) - record_to_index(prev_record)


def describe_change(prev_record, curr_record):
    """Human-readable summary of what moved. Handy for eyeballing results and
    for the writeup; not used in any calculation."""
    cv = change_vector(prev_record, curr_record)
    moved = {f: d for f, d in cv.items() if d != 0}
    if not moved:
        return "no change in any scored field"
    parts = []
    for f, d in moved.items():
        direction = "more hawkish" if d > 0 else "more dovish"
        parts.append(f"{f}: {prev_record[f]} -> {curr_record[f]} ({direction})")
    return "; ".join(parts)
