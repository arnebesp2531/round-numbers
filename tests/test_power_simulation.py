"""The power simulation, kept honest at a size that runs in a second.

The script's job is to answer "how many comparisons do we need?", and the only
way it can answer wrongly without anyone noticing is by being insensitive to
the thing it is measuring. So the assertions here are about direction: more
comparisons must recover the planted ranking better, and a subtler planted
effect must be harder to recover.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import sampling  # noqa: E402
from scripts import power_simulation as power  # noqa: E402


def _row(n_comparisons: int, signal: float = power.DEFAULT_SIGNAL) -> dict[str, float]:
    (result,) = power.run(
        [n_comparisons],
        trials=1,
        signal=signal,
        seed=0,
        alpha=2.0,
        session_size=power.DEFAULT_SESSION_SIZE,
    )
    return result


def test_the_planted_strengths_rank_the_landmarks_highest() -> None:
    # Not a claim about which hypothesis is true -- just that the stand-in
    # population is shaped like a plausible one, so the sample sizes it implies
    # mean something.
    strengths = power.planted_strengths()
    assert strengths.shape == (100,)
    ranking = (strengths.argsort()[::-1] + 1).tolist()
    assert 100 in ranking[:5]
    assert 50 in ranking[:10]


def test_more_comparisons_recover_the_ranking_better() -> None:
    few = _row(200)
    many = _row(2000)
    assert many["rho_vs_truth"] > few["rho_vs_truth"]
    assert many["split_half_rho"] > few["split_half_rho"]
    assert many["pairs_covered_pct"] > few["pairs_covered_pct"]


def test_a_subtler_effect_is_harder_to_recover() -> None:
    strong = _row(1000, signal=1.0)
    faint = _row(1000, signal=0.2)
    assert strong["rho_vs_truth"] > faint["rho_vs_truth"]


def test_the_simulation_uses_the_real_sampler_and_never_repeats_within_a_session() -> None:
    # A session that re-served a pair would overstate coverage, and the
    # recruiting target would come out too low.
    winners, losers, counts = power.simulate(
        power.DEFAULT_SESSION_SIZE, power.planted_strengths(), seed=1
    )
    assert winners.shape == losers.shape == (power.DEFAULT_SESSION_SIZE,)
    assert all(pair == sampling.canonical(*pair) for pair in counts)
    assert set(counts.values()) == {1}
    assert not (winners == losers).any()


def test_the_recommendation_picks_the_first_checkpoint_that_clears_both_targets() -> None:
    results = [
        {"n_comparisons": 100.0, "split_half_rho": 0.5, "top10_hits": 9.0},
        # Clears the correlation but not the leaderboard head.
        {"n_comparisons": 200.0, "split_half_rho": 0.95, "top10_hits": 6.0},
        {"n_comparisons": 300.0, "split_half_rho": 0.92, "top10_hits": 8.0},
        {"n_comparisons": 400.0, "split_half_rho": 0.99, "top10_hits": 10.0},
    ]
    assert power.recommend(results)["n_comparisons"] == 300.0
    assert power.recommend(results[:2]) is None
