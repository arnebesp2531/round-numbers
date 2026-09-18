"""How many comparisons buy a stable ranking? Answer it by simulation.

The open question in the writeup is what the recruiting target should be.
Guessing is avoidable: the instrument can be run against a synthetic population
whose true roundness is known, and the point where the recovered ranking stops
moving can be read off.

    python scripts/power_simulation.py
    python scripts/power_simulation.py --max 20000 --trials 5 --csv out.csv

Three numbers are reported at each checkpoint, averaged over trials:

``rho_vs_truth``
    Spearman correlation between the fitted scores and the planted ones. The
    honest measure of accuracy, and the one you cannot compute on real data --
    which is exactly why it is worth simulating.
``split_half_rho``
    Split the collected comparisons in two, fit each half separately, and
    correlate the halves. This *is* computable on real data, so it is the
    stand-in to watch once collection is under way.
``top10_hits``
    How many of the true top ten appear in the fitted top ten. Most of what
    anyone will quote from this experiment is the leaderboard's head, so it is
    worth scoring separately from the whole-range correlation.

The simulation uses the real sampler from :mod:`app.sampling`, so the coverage
weighting being modelled is the one the app actually ships. Everything here is
pure numpy and stdlib -- no Streamlit, no Snowflake.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from analysis import bradley_terry, metrics  # noqa: E402
from app import sampling  # noqa: E402
from app.config import MAX_NUMBER, MIN_NUMBER, PAIRS_PER_PAGE, SAMPLING_ALPHA  # noqa: E402
from scripts.generate_number_features import build_rows  # noqa: E402

#: Comparisons one simulated respondent contributes before leaving. Three pages
#: is roughly what a real session looks like -- most people stop early, a few
#: keep going, and the mean lands here.
DEFAULT_SESSION_SIZE = 3 * PAIRS_PER_PAGE

#: Spread of the planted strengths, in Elo-equivalent terms. A larger value
#: means roundness is a stronger signal and less data is needed; 1.0 puts the
#: roundest and least round numbers about 90:10 apart, which is roughly how
#: decisively people answer the easy pairs.
DEFAULT_SIGNAL = 1.0

#: What "stable enough" means. Both have to hold before a checkpoint is called
#: sufficient.
TARGET_SPLIT_HALF_RHO = 0.90
TARGET_TOP10_HITS = 8


def planted_strengths(signal: float = DEFAULT_SIGNAL) -> np.ndarray:
    """Log-strengths for 1-100, built from the real hypothesis features.

    Not a claim about which hypothesis is true -- it is a stand-in population
    whose *shape* is plausible: coin count, divisor count and landmark gravity,
    standardized and blended. What matters for a power analysis is that the
    strengths are spread like real ones, not that the blend is correct.
    """
    rows = build_rows()
    coins = _z([float(r["MIN_COIN_COUNT"]) for r in rows])
    divisors = _z([float(r["DIVISOR_COUNT"]) for r in rows])
    gravity = _z([float(r["GRAVITY_SCORE"]) for r in rows])
    blended = _z(-1.2 * coins + 0.6 * divisors + 0.8 * gravity)
    return signal * blended


def _z(values) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    spread = array.std()
    return (array - array.mean()) / spread if spread else array - array.mean()


def simulate(
    n_comparisons: int,
    strengths: np.ndarray,
    seed: int,
    alpha: float = SAMPLING_ALPHA,
    session_size: int = DEFAULT_SESSION_SIZE,
) -> tuple[np.ndarray, np.ndarray, dict[sampling.Pair, int]]:
    """Run simulated respondents through the real sampler.

    Returns winners, losers, and how many times each pair came up.

    Each respondent gets a coverage-weighted draw excluding the pairs they have
    already seen, which is exactly what the app does; the running pair counts
    are shared across respondents, which is what the cached ``V_PAIR_COUNTS``
    plus the session overlay approximate.
    """
    numbers = np.arange(MIN_NUMBER, MAX_NUMBER + 1)
    position = {int(value): index for index, value in enumerate(numbers)}

    rng = random.Random(seed)
    outcome_rng = np.random.default_rng(seed)
    counts: dict[sampling.Pair, int] = {}
    winners: list[int] = []
    losers: list[int] = []

    while len(winners) < n_comparisons:
        wanted = min(session_size, n_comparisons - len(winners))
        seen: set[sampling.Pair] = set()
        while wanted > 0:
            page = sampling.select_pairs(
                counts, min(PAIRS_PER_PAGE, wanted), exclude_pairs=seen, rng=rng, alpha=alpha
            )
            if not page:
                break  # this respondent has exhausted all 4,950 pairs
            for low, high in page:
                counts[(low, high)] = counts.get((low, high), 0) + 1
                seen.add((low, high))
                # Bradley-Terry itself is the response model: the probability
                # the rounder number wins is logistic in the strength gap, so a
                # near-tie is close to a coin flip.
                gap = strengths[position[low]] - strengths[position[high]]
                low_wins = outcome_rng.random() < 1.0 / (1.0 + np.exp(-gap))
                winners.append(low if low_wins else high)
                losers.append(high if low_wins else low)
            wanted -= len(page)

    return np.asarray(winners, dtype=int), np.asarray(losers, dtype=int), counts


def _fit_elo(winners: np.ndarray, losers: np.ndarray) -> np.ndarray:
    return bradley_terry.fit(winners, losers).elo


def evaluate(
    winners: np.ndarray, losers: np.ndarray, strengths: np.ndarray, seed: int
) -> dict[str, float]:
    """Accuracy against the planted truth, plus the measure real data can use."""
    fitted = _fit_elo(winners, losers)

    order = np.random.default_rng(seed).permutation(winners.shape[0])
    half = order.shape[0] // 2
    first, second = order[:half], order[half:]
    split_half = (
        metrics.spearman(_fit_elo(winners[first], losers[first]),
                         _fit_elo(winners[second], losers[second]))
        if half >= 2
        else float("nan")
    )

    true_top = set(np.argsort(-strengths)[:10].tolist())
    fitted_top = set(np.argsort(-fitted)[:10].tolist())
    return {
        "rho_vs_truth": metrics.spearman(fitted, strengths),
        "split_half_rho": split_half,
        "top10_hits": float(len(true_top & fitted_top)),
    }


def run(
    checkpoints: list[int],
    trials: int,
    signal: float,
    seed: int,
    alpha: float,
    session_size: int,
) -> list[dict[str, float]]:
    strengths = planted_strengths(signal)
    total_pairs = len(sampling.all_pairs())
    results = []
    for n_comparisons in checkpoints:
        scored = []
        coverage = []
        for trial in range(trials):
            winners, losers, counts = simulate(
                n_comparisons,
                strengths,
                seed=seed + trial,
                alpha=alpha,
                session_size=session_size,
            )
            scored.append(evaluate(winners, losers, strengths, seed=seed + trial))
            # Measured, not approximated: coverage-weighted sampling beats the
            # uniform coupon-collector expectation comfortably, which is most of
            # the reason the sampler is weighted at all.
            coverage.append(100.0 * len(counts) / total_pairs)
        row = {"n_comparisons": float(n_comparisons)}
        for key in ("rho_vs_truth", "split_half_rho", "top10_hits"):
            row[key] = float(np.mean([s[key] for s in scored]))
        row["respondents"] = float(np.ceil(n_comparisons / session_size))
        row["pairs_covered_pct"] = float(np.mean(coverage))
        results.append(row)
    return results


def recommend(results: list[dict[str, float]]) -> dict[str, float] | None:
    """The first checkpoint that clears both stability targets."""
    for row in results:
        if (
            row["split_half_rho"] >= TARGET_SPLIT_HALF_RHO
            and row["top10_hits"] >= TARGET_TOP10_HITS
        ):
            return row
    return None


def _print_table(results: list[dict[str, float]]) -> None:
    header = (
        f"{'comparisons':>12}  {'respondents':>11}  {'pairs seen':>10}  "
        f"{'rho vs truth':>12}  {'split-half':>10}  {'top-10':>7}"
    )
    print(header)
    print("-" * len(header))
    for row in results:
        print(
            f"{int(row['n_comparisons']):>12,}  {int(row['respondents']):>11,}  "
            f"{row['pairs_covered_pct']:>9.0f}%  {row['rho_vs_truth']:>12.3f}  "
            f"{row['split_half_rho']:>10.3f}  {row['top10_hits']:>6.1f}/10"
        )


def _write_csv(results: list[dict[str, float]], path: Path) -> None:
    import csv

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max", type=int, default=10000, help="largest checkpoint")
    parser.add_argument("--steps", type=int, default=10, help="number of checkpoints")
    parser.add_argument("--trials", type=int, default=3, help="repeats per checkpoint")
    parser.add_argument(
        "--signal",
        type=float,
        default=DEFAULT_SIGNAL,
        help="spread of the planted strengths; lower means a subtler effect and "
        "more data needed",
    )
    parser.add_argument("--alpha", type=float, default=SAMPLING_ALPHA)
    parser.add_argument("--session-size", type=int, default=DEFAULT_SESSION_SIZE)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--csv", type=Path, default=None)
    args = parser.parse_args()

    checkpoints = sorted(
        {
            int(round(value))
            for value in np.geomspace(max(PAIRS_PER_PAGE, args.max // 100), args.max, args.steps)
        }
    )
    print(
        f"Simulating {args.trials} trial(s) per checkpoint, signal {args.signal}, "
        f"sessions of {args.session_size} comparisons.\n"
    )
    results = run(
        checkpoints,
        trials=args.trials,
        signal=args.signal,
        seed=args.seed,
        alpha=args.alpha,
        session_size=args.session_size,
    )
    _print_table(results)

    best = recommend(results)
    print()
    if best is None:
        print(
            f"No checkpoint up to {args.max:,} comparisons reached a split-half "
            f"correlation of {TARGET_SPLIT_HALF_RHO} with "
            f"{TARGET_TOP10_HITS}/10 of the true top ten. Re-run with a larger "
            "--max."
        )
    else:
        print(
            f"Recruiting target: about {int(best['n_comparisons']):,} comparisons "
            f"(~{int(best['respondents']):,} respondents at "
            f"{args.session_size} each). That is where the split-half "
            f"correlation reaches {best['split_half_rho']:.2f} and "
            f"{best['top10_hits']:.0f} of the true top ten are recovered."
        )
    print(
        "\nOn real data the split-half figure is the one to watch -- it needs no "
        "ground truth. The results page's median confidence interval is the "
        "same signal seen per number."
    )

    if args.csv:
        _write_csv(results, args.csv)
        print(f"\nWrote {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
