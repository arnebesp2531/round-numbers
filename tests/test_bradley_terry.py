"""Checks on the Bradley-Terry fit.

The two that matter most are the divergence guards: an undefeated number and a
disconnected comparison graph are both guaranteed early in data collection, and
an unregularized fit produces infinities on either.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import bradley_terry as bt  # noqa: E402
from analysis.metrics import comparison_weights, spearman  # noqa: E402


def simulate(strengths: dict[int, float], n: int, rng) -> tuple[list[int], list[int]]:
    """Draw `n` comparisons from a true Bradley-Terry model."""
    numbers = list(strengths)
    winners, losers = [], []
    for _ in range(n):
        a, b = rng.choice(numbers, size=2, replace=False)
        p_a = strengths[int(a)] / (strengths[int(a)] + strengths[int(b)])
        winner, loser = (a, b) if rng.random() < p_a else (b, a)
        winners.append(int(winner))
        losers.append(int(loser))
    return winners, losers


def test_recovers_a_known_ranking() -> None:
    truth = {10: 8.0, 25: 4.0, 50: 6.0, 7: 1.0, 37: 0.5, 63: 0.7}
    rng = np.random.default_rng(1)
    winners, losers = simulate(truth, 4000, rng)

    result = bt.fit(winners, losers, numbers=sorted(truth))

    fitted = [result.score(n) for n in sorted(truth)]
    expected = [np.log(truth[n]) for n in sorted(truth)]
    assert spearman(fitted, expected) > 0.95
    assert result.ranking()[0] == 10
    assert result.ranking()[-1] == 37


def test_head_to_head_margin_matches_the_model() -> None:
    # 400 Elo points is one factor of 10 in odds, by construction of the scale.
    winners = [50] * 100 + [7] * 10
    losers = [7] * 100 + [50] * 10
    result = bt.fit(winners, losers, numbers=[7, 50])
    margin = result.score(50) - result.score(7)
    assert margin == pytest.approx(400.0, abs=15.0)


def test_undefeated_number_stays_finite() -> None:
    """The classic divergence case: 100 never loses, so its MLE is infinite."""
    winners = [100] * 50
    losers = list(range(1, 51))
    result = bt.fit(winners, losers)

    assert np.all(np.isfinite(result.elo))
    assert result.converged
    assert result.ranking()[0] == 100


def test_disconnected_graph_stays_finite() -> None:
    """Two comparison islands that never meet -- normal on day one."""
    winners = [10, 10, 10, 90, 90, 90]
    losers = [3, 3, 3, 91, 91, 91]
    result = bt.fit(winners, losers)

    assert np.all(np.isfinite(result.elo))
    assert result.score(10) > result.score(3)
    assert result.score(90) > result.score(91)


def test_no_comparisons_at_all_is_flat() -> None:
    result = bt.fit([], [])
    assert np.all(np.isfinite(result.elo))
    assert result.elo.std() == pytest.approx(0.0, abs=1e-9)
    assert np.allclose(result.normalized, 50.0)


def test_epsilon_must_be_positive() -> None:
    with pytest.raises(ValueError, match="epsilon"):
        bt.fit([10], [3], epsilon=0.0)


def test_rejects_a_number_beating_itself() -> None:
    with pytest.raises(ValueError, match="cannot beat itself"):
        bt.fit([10], [10])


def test_normalized_scores_span_zero_to_one_hundred() -> None:
    winners = [10, 10, 50, 50, 25]
    losers = [3, 7, 3, 7, 3]
    result = bt.fit(winners, losers)
    assert result.normalized.min() == pytest.approx(0.0)
    assert result.normalized.max() == pytest.approx(100.0)


def test_observed_counts_exclude_virtual_wins() -> None:
    winners = [10, 10, 50]
    losers = [3, 7, 10]
    result = bt.fit(winners, losers)
    index = {int(n): i for i, n in enumerate(result.numbers)}
    assert result.wins[index[10]] == 2
    assert result.comparisons[index[10]] == 3
    assert result.comparisons[index[99]] == 0


def test_respondent_weighting_neutralizes_a_heavy_user() -> None:
    """One contrarian answering 300 pairs must not outvote 30 ordinary people."""
    crowd_winners = [10] * 30
    crowd_losers = [7] * 30
    crowd_ids = [f"person-{i}" for i in range(30)]

    heavy_winners = [7] * 300
    heavy_losers = [10] * 300
    heavy_ids = ["heavy"] * 300

    winners = crowd_winners + heavy_winners
    losers = crowd_losers + heavy_losers
    ids = crowd_ids + heavy_ids

    unweighted = bt.fit(winners, losers, numbers=[7, 10])
    assert unweighted.score(7) > unweighted.score(10)  # heavy user wins outright

    weighted = bt.fit(
        winners, losers, numbers=[7, 10], weights=comparison_weights(ids)
    )
    assert weighted.score(10) > weighted.score(7)  # one vote each restores the crowd


def test_bootstrap_brackets_the_point_estimate_and_is_deterministic() -> None:
    rng = np.random.default_rng(7)
    winners, losers = simulate({10: 6.0, 50: 4.0, 7: 1.0}, 600, rng)

    result = bt.fit_with_bootstrap(
        winners, losers, numbers=[7, 10, 50], samples=50, seed=3
    )
    assert result.elo_ci_low is not None and result.elo_ci_high is not None
    assert np.all(result.elo_ci_low <= result.elo_ci_high)
    # Percentile intervals are not guaranteed to cover, but on well-sampled
    # data they should sit around the point estimate.
    assert np.all(result.elo_ci_low <= result.elo + 1e-6)
    assert np.all(result.elo_ci_high >= result.elo - 1e-6)
    assert bt.median_ci_width(result) > 0

    repeat = bt.fit_with_bootstrap(
        winners, losers, numbers=[7, 10, 50], samples=50, seed=3
    )
    assert np.allclose(result.elo_ci_low, repeat.elo_ci_low)


def test_bootstrap_intervals_narrow_with_more_data() -> None:
    rng = np.random.default_rng(11)
    truth = {10: 6.0, 50: 4.0, 7: 1.0}
    small = bt.fit_with_bootstrap(
        *simulate(truth, 100, rng), numbers=[7, 10, 50], samples=60, seed=1
    )
    large = bt.fit_with_bootstrap(
        *simulate(truth, 2000, rng), numbers=[7, 10, 50], samples=60, seed=1
    )
    assert bt.median_ci_width(large) < bt.median_ci_width(small)


def test_bootstrap_on_no_data_returns_the_point_fit() -> None:
    result = bt.fit_with_bootstrap([], [], samples=10)
    assert result.elo_ci_low is None


def test_weights_must_match_comparison_count() -> None:
    with pytest.raises(ValueError, match="weights"):
        bt.fit([10, 50], [3, 3], weights=[1.0])


def test_full_domain_is_the_default_label_set() -> None:
    result = bt.fit([10], [3])
    assert result.numbers.tolist() == list(range(1, 101))
