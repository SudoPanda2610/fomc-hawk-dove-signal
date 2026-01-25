"""
test_properties.py
==================

Property-based tests with `hypothesis`. The point of property tests (vs checking
hardcoded numbers) is that they assert RULES that must hold for EVERY valid input,
and hypothesis tries hundreds of random records to break them.

I can only test the deterministic layer (schema + score). I cannot property-test
the LLM itself -- it is not deterministic -- which is exactly why extraction is
validated separately, by hand-labelling a few statements (see concept_note.md).

Run with:  pytest -q   (or)   python -m pytest test_properties.py
"""

from hypothesis import given, strategies as st

from scoring import (
    SCORED_FIELDS, ORDINALS, record_to_index, raw_score_bounds,
    change_vector, delta_index,
)


# A strategy that builds a random VALID record: one allowed category per field.
record_strategy = st.fixed_dictionaries(
    {f: st.sampled_from(list(ORDINALS[f].keys())) for f in SCORED_FIELDS}
)


def _sorted_categories(field):
    """Categories of a field ordered from most dovish to most hawkish."""
    return [k for k, _ in sorted(ORDINALS[field].items(), key=lambda kv: kv[1])]


# 1. The index always lands inside [-1, 1]. No input should ever escape it.
@given(record_strategy)
def test_index_in_bounds(rec):
    idx = record_to_index(rec)
    assert -1.0 - 1e-9 <= idx <= 1.0 + 1e-9


# 2. Extremes hit the corners exactly, and neutral sits at 0.
def test_extremes_and_neutral():
    most_hawkish = {f: _sorted_categories(f)[-1] for f in SCORED_FIELDS}
    most_dovish = {f: _sorted_categories(f)[0] for f in SCORED_FIELDS}
    neutral = {f: next(k for k, v in ORDINALS[f].items() if v == 0)
               for f in SCORED_FIELDS}
    assert abs(record_to_index(most_hawkish) - 1.0) < 1e-9
    assert abs(record_to_index(most_dovish) + 1.0) < 1e-9
    assert abs(record_to_index(neutral)) < 1e-9


# 3. Monotonicity: nudging ONE field one step in the hawkish direction never
#    lowers the index (weights are non-negative). This is the property that
#    makes the score interpretable -- "more hawkish language => higher score".
@given(record_strategy, st.sampled_from(SCORED_FIELDS))
def test_monotonic_in_each_field(rec, field):
    cats = _sorted_categories(field)
    i = cats.index(rec[field])
    if i + 1 < len(cats):  # only if there's a more-hawkish category to move to
        bumped = dict(rec)
        bumped[field] = cats[i + 1]
        assert record_to_index(bumped) >= record_to_index(rec) - 1e-12


# 4. A statement diffed against ITSELF shows no change anywhere.
@given(record_strategy)
def test_self_diff_is_zero(rec):
    cv = change_vector(rec, rec)
    assert all(v == 0 for v in cv.values())
    assert abs(delta_index(rec, rec)) < 1e-12


# 5. The change is antisymmetric: going A->B is the exact negative of B->A,
#    both per field and in the overall index.
@given(record_strategy, record_strategy)
def test_change_is_antisymmetric(a, b):
    fwd = change_vector(a, b)
    bwd = change_vector(b, a)
    for f in SCORED_FIELDS:
        assert fwd[f] == -bwd[f]
    assert abs(delta_index(a, b) + delta_index(b, a)) < 1e-12


# 6. Scoring is deterministic: same input, same output, every time.
@given(record_strategy)
def test_deterministic(rec):
    assert record_to_index(rec) == record_to_index(dict(rec))
