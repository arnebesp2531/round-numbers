"""Bradley-Terry strength model for the pairwise roundness comparisons.

Raw win percentage is biased by which opponents a number happened to face --
beating 7 twenty times says less than beating 50 once. Bradley-Terry fits a
latent strength p_i per number such that

    P(i beats j) = p_i / (p_i + p_j)

and so scores every number on a common scale regardless of its schedule.

Fitting uses the MM / Zermelo iteration

    p_i  <-  W_i / sum_{j != i} ( N_ij / (p_i + p_j) )

which is monotonically convergent and needs no step size or gradient tuning.

**Regularization is not optional.** Early in data collection the comparison
graph is disconnected, and an undefeated number can stay undefeated forever;
in either case the unregularized MLE diverges (that number's strength runs off
to infinity). Every pair therefore gets `epsilon` virtual wins and losses in
each direction, which keeps every score finite and sensible from the first
respondent onward.

Pure numpy: no scipy, no Streamlit, no Snowflake. Unit-testable without a
connection.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import (  # noqa: E402
    BT_BOOTSTRAP_SAMPLES,
    BT_EPSILON,
    BT_MAX_ITERATIONS,
    BT_TOLERANCE,
    MAX_NUMBER,
    MIN_NUMBER,
)

# Converts a strength ratio to the familiar Elo spacing: 400 points per factor
# of 10 in odds.
ELO_SCALE = 400.0 / math.log(10.0)


def default_numbers() -> np.ndarray:
    """The full 1-100 domain, used when no explicit label set is supplied."""
    return np.arange(MIN_NUMBER, MAX_NUMBER + 1, dtype=int)


@dataclass(frozen=True)
class BradleyTerryResult:
    """A fitted model. Arrays are all parallel to `numbers`."""

    numbers: np.ndarray
    strengths: np.ndarray  # p_i, normalized to sum to 1
    elo: np.ndarray  # ELO_SCALE * log(p_i), centered on 0
    normalized: np.ndarray  # elo rescaled to 0-100 for charting
    wins: np.ndarray  # observed (weighted) wins, excluding virtual ones
    comparisons: np.ndarray  # observed (weighted) comparisons per number
    iterations: int
    converged: bool
    elo_ci_low: np.ndarray | None = None
    elo_ci_high: np.ndarray | None = None

    def score(self, number: int) -> float:
        """Elo score for a single number. Convenience for tests and callers."""
        (index,) = np.nonzero(self.numbers == number)
        if index.size == 0:
            raise KeyError(f"{number} is not in this fit")
        return float(self.elo[index[0]])

    def ranking(self) -> np.ndarray:
        """Numbers ordered strongest (roundest) first."""
        return self.numbers[np.argsort(-self.elo, kind="stable")]


def _index_map(numbers: np.ndarray) -> dict[int, int]:
    return {int(value): position for position, value in enumerate(numbers)}


def build_win_matrix(
    winners,
    losers,
    numbers: np.ndarray | None = None,
    weights=None,
) -> tuple[np.ndarray, np.ndarray]:
    """Accumulate comparisons into a weighted win matrix.

    Returns `(numbers, A)` where `A[i, j]` is the weighted number of times
    number i beat number j. Weights default to 1.0 per comparison; the
    respondent-weighted variant passes each comparison's respondent weight
    here, which is the whole of what makes that variant different.
    """
    numbers = default_numbers() if numbers is None else np.asarray(numbers, dtype=int)
    winners = np.asarray(winners, dtype=int)
    losers = np.asarray(losers, dtype=int)
    if winners.shape != losers.shape:
        raise ValueError("winners and losers must be the same length")
    if weights is None:
        weights = np.ones(winners.shape[0], dtype=float)
    else:
        weights = np.asarray(weights, dtype=float)
        if weights.shape != winners.shape:
            raise ValueError("weights must be the same length as winners")

    position = _index_map(numbers)
    size = numbers.shape[0]
    matrix = np.zeros((size, size), dtype=float)
    for winner, loser, weight in zip(winners, losers, weights):
        if winner == loser:
            raise ValueError(f"a number cannot beat itself ({winner})")
        try:
            i, j = position[int(winner)], position[int(loser)]
        except KeyError as exc:  # pragma: no cover - guards caller error
            raise KeyError(f"comparison references unknown number {exc}") from exc
        matrix[i, j] += weight
    return numbers, matrix


def fit_from_matrix(
    matrix: np.ndarray,
    numbers: np.ndarray | None = None,
    epsilon: float = BT_EPSILON,
    max_iterations: int = BT_MAX_ITERATIONS,
    tolerance: float = BT_TOLERANCE,
) -> BradleyTerryResult:
    """Fit strengths from a win matrix via the regularized MM iteration."""
    matrix = np.asarray(matrix, dtype=float)
    size = matrix.shape[0]
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError("win matrix must be square")
    if epsilon <= 0:
        raise ValueError(
            "epsilon must be positive; the unregularized MLE diverges on a "
            "disconnected graph or an undefeated number"
        )
    numbers = default_numbers() if numbers is None else np.asarray(numbers, dtype=int)
    if numbers.shape[0] != size:
        raise ValueError("numbers and win matrix disagree on size")

    observed_wins = matrix.sum(axis=1)
    observed_comparisons = observed_wins + matrix.sum(axis=0)

    # Virtual wins and losses on every ordered pair. This is the prior that
    # keeps an undefeated number finite: it now has a notional loss to everyone.
    regularized = matrix + epsilon
    np.fill_diagonal(regularized, 0.0)

    wins = regularized.sum(axis=1)
    pair_totals = regularized + regularized.T  # N_ij, symmetric, zero diagonal

    strengths = np.ones(size, dtype=float) / size
    converged = False
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        # denominator_i = sum_{j != i} N_ij / (p_i + p_j)
        sums = strengths[:, None] + strengths[None, :]
        np.fill_diagonal(sums, 1.0)  # diagonal is zeroed by pair_totals anyway
        denominator = (pair_totals / sums).sum(axis=1)
        updated = wins / denominator
        updated /= updated.sum()
        shift = np.max(np.abs(updated - strengths) / np.maximum(strengths, 1e-300))
        strengths = updated
        if shift < tolerance:
            converged = True
            break

    elo = ELO_SCALE * np.log(strengths)
    elo -= elo.mean()
    return BradleyTerryResult(
        numbers=numbers,
        strengths=strengths,
        elo=elo,
        normalized=_normalize(elo),
        wins=observed_wins,
        comparisons=observed_comparisons,
        iterations=iterations,
        converged=converged,
    )


def _normalize(elo: np.ndarray) -> np.ndarray:
    """Rescale Elo scores to 0-100 for the chart axis."""
    low, high = float(elo.min()), float(elo.max())
    if high - low < 1e-12:
        return np.full_like(elo, 50.0)
    return 100.0 * (elo - low) / (high - low)


def fit(
    winners,
    losers,
    numbers: np.ndarray | None = None,
    weights=None,
    epsilon: float = BT_EPSILON,
    max_iterations: int = BT_MAX_ITERATIONS,
    tolerance: float = BT_TOLERANCE,
) -> BradleyTerryResult:
    """Fit Bradley-Terry strengths from parallel winner/loser sequences.

    `weights` accepts fractional per-comparison weights, which is how the
    respondent-weighted variant is produced -- pass each comparison's
    `V_RESPONDENT_WEIGHTS` value.
    """
    numbers, matrix = build_win_matrix(winners, losers, numbers, weights)
    return fit_from_matrix(
        matrix,
        numbers=numbers,
        epsilon=epsilon,
        max_iterations=max_iterations,
        tolerance=tolerance,
    )


def fit_with_bootstrap(
    winners,
    losers,
    numbers: np.ndarray | None = None,
    weights=None,
    epsilon: float = BT_EPSILON,
    samples: int = BT_BOOTSTRAP_SAMPLES,
    confidence: float = 0.95,
    seed: int | None = 0,
    max_iterations: int = BT_MAX_ITERATIONS,
    tolerance: float = BT_TOLERANCE,
) -> BradleyTerryResult:
    """Fit, then attach percentile bootstrap confidence intervals on the Elo scale.

    Comparisons are resampled with replacement `samples` times and the model
    refit on each resample. On a 100x100 matrix this is fast, and the caller is
    expected to cache the result rather than refit per rerun.

    The seed is fixed by default so a results page does not show slightly
    different error bars on every rerun of the same data.
    """
    point = fit(
        winners,
        losers,
        numbers=numbers,
        weights=weights,
        epsilon=epsilon,
        max_iterations=max_iterations,
        tolerance=tolerance,
    )
    winners = np.asarray(winners, dtype=int)
    losers = np.asarray(losers, dtype=int)
    count = winners.shape[0]
    if count == 0 or samples <= 0:
        return point
    weight_array = (
        np.ones(count, dtype=float) if weights is None else np.asarray(weights, float)
    )

    rng = np.random.default_rng(seed)
    draws = np.empty((samples, point.numbers.shape[0]), dtype=float)
    for draw in range(samples):
        picks = rng.integers(0, count, size=count)
        draws[draw] = fit(
            winners[picks],
            losers[picks],
            numbers=point.numbers,
            weights=weight_array[picks],
            epsilon=epsilon,
            max_iterations=max_iterations,
            tolerance=tolerance,
        ).elo

    tail = (1.0 - confidence) / 2.0
    low = np.percentile(draws, 100.0 * tail, axis=0)
    high = np.percentile(draws, 100.0 * (1.0 - tail), axis=0)
    return BradleyTerryResult(
        numbers=point.numbers,
        strengths=point.strengths,
        elo=point.elo,
        normalized=point.normalized,
        wins=point.wins,
        comparisons=point.comparisons,
        iterations=point.iterations,
        converged=point.converged,
        elo_ci_low=low,
        elo_ci_high=high,
    )


def median_ci_width(result: BradleyTerryResult) -> float:
    """Median Elo interval width -- the confidence footer's headline number."""
    if result.elo_ci_low is None or result.elo_ci_high is None:
        return float("nan")
    return float(np.median(result.elo_ci_high - result.elo_ci_low))
