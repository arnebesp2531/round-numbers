"""Checks on coverage-weighted pair selection.

Two failure modes matter here and neither is visible by eye in the running app:
a respondent being shown the same pair twice, and the weighting quietly not
working so coverage never reaches the cold pairs. Both are cheap to pin down
with a seeded rng.
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import MAX_NUMBER, MIN_NUMBER, TOTAL_PAIRS  # noqa: E402
from app.sampling import (  # noqa: E402
    all_pairs,
    canonical,
    merge_overlay,
    pair_counts_from_frame,
    pair_weight,
    randomize_display_order,
    remaining_pair_count,
    select_pairs,
)


def test_all_pairs_covers_the_domain() -> None:
    pairs = all_pairs()
    assert len(pairs) == TOTAL_PAIRS == 4950
    assert len(set(pairs)) == len(pairs)
    assert all(low < high for low, high in pairs)
    assert (MIN_NUMBER, MAX_NUMBER) in pairs


def test_canonical_orders_and_validates() -> None:
    assert canonical(7, 3) == (3, 7)
    assert canonical(3, 7) == (3, 7)
    with pytest.raises(ValueError):
        canonical(5, 5)
    with pytest.raises(ValueError):
        canonical(0, 5)
    with pytest.raises(ValueError):
        canonical(5, 101)


def test_pair_weight_falls_with_exposure() -> None:
    # alpha=2: a never-shown pair is 4x a once-shown pair, 9x a twice-shown one.
    assert pair_weight(0, alpha=2.0) == 1.0
    assert pair_weight(1, alpha=2.0) == pytest.approx(0.25)
    assert pair_weight(2, alpha=2.0) == pytest.approx(1 / 9)
    # alpha=0 is uniform, the documented escape hatch.
    assert pair_weight(99, alpha=0.0) == 1.0


def test_select_pairs_returns_distinct_pairs() -> None:
    rng = random.Random(0)
    selected = select_pairs({}, 10, rng=rng)
    assert len(selected) == 10
    assert len(set(selected)) == 10
    assert all(low < high for low, high in selected)


def test_select_pairs_never_returns_an_excluded_pair() -> None:
    rng = random.Random(1)
    # Exclude everything a respondent could plausibly have seen, then check
    # across many draws that none of it comes back.
    excluded = set(all_pairs()[:2000])
    for _ in range(50):
        selected = select_pairs({}, 10, exclude_pairs=excluded, rng=rng)
        assert not (set(selected) & excluded)


def test_exclusions_are_matched_regardless_of_order() -> None:
    rng = random.Random(2)
    # Caller hands back (high, low) as displayed; it must still exclude.
    excluded = [(high, low) for low, high in all_pairs()[:4949]]
    selected = select_pairs({}, 10, exclude_pairs=excluded, rng=rng)
    assert selected == [all_pairs()[4949]]


def test_select_pairs_favours_least_compared_pairs() -> None:
    rng = random.Random(3)
    cold = set(all_pairs()[:20])
    counts = {pair: (0 if pair in cold else 50) for pair in all_pairs()}

    hits = Counter()
    for _ in range(100):
        for pair in select_pairs(counts, 10, rng=rng):
            hits[pair in cold] += 1

    # 20 cold pairs out of 4,950 would be ~0.4% of draws under uniform
    # sampling; the 1/(n+1)**2 weighting should make them dominate instead.
    assert hits[True] > 0.9 * (hits[True] + hits[False])


def test_a_hot_pair_is_still_reachable() -> None:
    # Weighting must never drive a well-covered pair to probability zero, or
    # the ranking stops refining once coverage is even.
    rng = random.Random(4)
    hot = canonical(10, 50)
    assert pair_weight(500) > 0

    # With every other pair equally cold the hot pair is heavily disfavoured,
    # so reach for it where it is cheap to observe: alpha=0, the uniform case.
    seen = set()
    for _ in range(200):
        seen.update(select_pairs({hot: 500}, 10, rng=rng, alpha=0.0))
    assert hot in seen


def test_missing_counts_are_treated_as_zero() -> None:
    # A pair absent from V_PAIR_COUNTS must still be sampleable, otherwise a
    # gap in the view would freeze those pairs out permanently.
    rng = random.Random(5)
    counts = {canonical(1, 2): 3}
    selected = select_pairs(counts, 10, rng=rng)
    assert len(selected) == 10


def test_select_pairs_degrades_when_the_domain_runs_out() -> None:
    rng = random.Random(6)
    almost_all = all_pairs()[:-3]
    selected = select_pairs({}, 10, exclude_pairs=almost_all, rng=rng)
    assert len(selected) == 3

    exhausted = select_pairs({}, 10, exclude_pairs=all_pairs(), rng=rng)
    assert exhausted == []


def test_select_pairs_rejects_a_negative_count() -> None:
    assert select_pairs({}, 0) == []
    with pytest.raises(ValueError):
        select_pairs({}, -1)


def test_select_pairs_is_reproducible_for_a_given_seed() -> None:
    first = select_pairs({}, 10, rng=random.Random(42))
    second = select_pairs({}, 10, rng=random.Random(42))
    assert first == second


def test_merge_overlay_adds_session_counts() -> None:
    base = {canonical(1, 2): 4, canonical(3, 4): 0}
    merged = merge_overlay(base, {(2, 1): 1, (5, 6): 2})
    assert merged[canonical(1, 2)] == 5
    assert merged[canonical(3, 4)] == 0
    assert merged[canonical(5, 6)] == 2
    assert base[canonical(1, 2)] == 4  # caller's cached frame is untouched


def test_overlay_cools_a_pair_off_within_one_session() -> None:
    # The point of the overlay: a pair served this page should stop looking
    # like the coldest pair on the next page, before the cache refreshes.
    rng = random.Random(7)
    cold = set(all_pairs()[:20])
    counts = {pair: (0 if pair in cold else 50) for pair in all_pairs()}
    served = select_pairs(counts, 10, rng=rng)

    counts = merge_overlay(counts, {pair: 50 for pair in served})
    hits = Counter()
    for _ in range(50):
        for pair in select_pairs(counts, 10, exclude_pairs=served, rng=rng):
            hits[pair in served] += 1
    assert hits[True] == 0  # excluded outright
    assert hits[False] > 0


def test_pair_counts_from_frame_reads_by_column_name() -> None:
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame(
        {
            "PAIR_LOW": [1, 3],
            "PAIR_HIGH": [2, 4],
            "N_COMPARISONS": [7, 0],
            "LOW_WINS": [4, 0],
            "HIGH_WINS": [3, 0],
        }
    )
    assert pair_counts_from_frame(df) == {(1, 2): 7, (3, 4): 0}


def test_randomize_display_order_flips_sides_without_changing_pairs() -> None:
    rng = random.Random(8)
    pairs = all_pairs()[:200]
    displayed = randomize_display_order(pairs, rng=rng)

    assert len(displayed) == len(pairs)
    assert [canonical(*p) for p in displayed] == pairs
    flipped = sum(1 for (a, b) in displayed if a > b)
    # A coin flip over 200 pairs: anything this lopsided is a broken flip, and
    # a systematic side preference here would show up as fake position bias.
    assert 60 < flipped < 140


def test_remaining_pair_count() -> None:
    assert remaining_pair_count([]) == TOTAL_PAIRS
    assert remaining_pair_count([(2, 1), (1, 2)]) == TOTAL_PAIRS - 1
    assert remaining_pair_count(all_pairs()) == 0
