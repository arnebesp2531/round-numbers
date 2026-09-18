"""Descriptive metrics and the hypothesis scoreboard.

Three jobs:

1. **Raw win percentage** -- the sanity check shown next to Bradley-Terry. If
   the two disagree wildly, suspect the winner/loser column mapping.
2. **Respondent weights** -- `1 / comparisons` per respondent, so one
   enthusiast who answers 400 pairs counts the same as someone who answers 10.
   Feed these to `bradley_terry.fit(..., weights=...)`.
3. **Hypothesis scoring** -- Spearman correlation of R(x) against each
   `NUMBERS` feature, plus a standardized regression per hypothesis so the four
   compete on the same footing. This is the deliverable that answers the
   question the experiment was built to answer.

Pure numpy/pandas: no Streamlit, no Snowflake, no scipy.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.bradley_terry import default_numbers  # noqa: E402

# Which NUMBERS columns speak for which hypothesis, and which way each one is
# expected to point if that hypothesis is right. `expected_sign` is the sign of
# the correlation with roundness: more coins should mean *less* round, so -1.
#
# Keys match the column names emitted by scripts/generate_number_features.py.
HYPOTHESES: dict[str, dict[str, object]] = {
    "additive_units": {
        "label": "Additive units (coins and bills)",
        "features": {"MIN_COIN_COUNT": -1},
    },
    "geometric": {
        "label": "Geometric (factor count, squareness)",
        "features": {
            "DIVISOR_COUNT": +1,
            "IS_PRIME": -1,
            "IS_PERFECT_SQUARE": +1,
            "ASPECT_RATIO": +1,
        },
    },
    "gravity": {
        "label": "Roundness gravity (pull from landmarks)",
        "features": {
            "DIST_TO_MULT_10": -1,
            "DIST_TO_MULT_25": -1,
            "DIST_TO_MULT_50": -1,
            "GRAVITY_SCORE": +1,
        },
    },
    "roman": {
        "label": "Roman numeral length",
        "features": {"ROMAN_LENGTH": -1},
    },
    "baseline": {
        "label": "Baseline controls",
        "features": {"TRAILING_ZEROS": +1, "IS_MULT_5": +1},
    },
}

# The four the experiment is actually adjudicating; "baseline" is a control and
# is reported but never crowned.
COMPETING_HYPOTHESES = ("additive_units", "geometric", "gravity", "roman")


# --- Raw counts --------------------------------------------------------------


def win_percentages(winners, losers, numbers=None) -> pd.DataFrame:
    """Per-number wins, losses, comparisons and win%.

    Mirrors `V_NUMBER_STATS` so the same figures can be computed locally in
    tests without a connection. Numbers with no comparisons get NaN win%, not
    0 -- unseen is not the same as never wins.
    """
    numbers = default_numbers() if numbers is None else np.asarray(numbers, dtype=int)
    winners = np.asarray(winners, dtype=int)
    losers = np.asarray(losers, dtype=int)

    wins = pd.Series(winners).value_counts().reindex(numbers, fill_value=0)
    losses = pd.Series(losers).value_counts().reindex(numbers, fill_value=0)
    comparisons = wins + losses
    with np.errstate(invalid="ignore"):
        win_pct = np.where(comparisons > 0, 100.0 * wins / comparisons, np.nan)

    return pd.DataFrame(
        {
            "number": numbers,
            "wins": wins.to_numpy(dtype=int),
            "losses": losses.to_numpy(dtype=int),
            "comparisons": comparisons.to_numpy(dtype=int),
            "win_pct": win_pct,
        }
    )


def respondent_weights(respondent_ids) -> pd.Series:
    """`1 / comparisons` per respondent. Mirrors `V_RESPONDENT_WEIGHTS`."""
    counts = pd.Series(list(respondent_ids)).value_counts()
    return (1.0 / counts).rename("weight")


def comparison_weights(respondent_ids) -> np.ndarray:
    """Per-comparison weights, ready to hand to `bradley_terry.fit`.

    Each respondent's rows sum to 1.0 regardless of how many they answered.
    """
    series = pd.Series(list(respondent_ids))
    return series.map(respondent_weights(series)).to_numpy(dtype=float)


def left_win_rate(left_numbers, winner_numbers) -> float:
    """Share of comparisons won by the left-hand number.

    Near 0.5 is healthy. A strong skew means left-side position bias is
    contaminating the results, which is the reason display order is stored
    separately from outcome in the first place.
    """
    left = np.asarray(left_numbers, dtype=int)
    winner = np.asarray(winner_numbers, dtype=int)
    if left.size == 0:
        return float("nan")
    return float(np.mean(left == winner))


# --- Correlation -------------------------------------------------------------


def average_ranks(values) -> np.ndarray:
    """Ranks with ties averaged -- the ranking Spearman's rho expects.

    Hand-rolled because scipy is not a dependency (it is unavailable in
    Snowflake in Streamlit without a package request).
    """
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="stable")
    ranks = np.empty(values.shape[0], dtype=float)
    sorted_values = values[order]
    start = 0
    for position in range(1, values.shape[0] + 1):
        at_end = position == values.shape[0]
        if at_end or sorted_values[position] != sorted_values[start]:
            # 1-based average rank shared by every member of the tie group
            ranks[order[start:position]] = (start + position + 1) / 2.0
            start = position
    return ranks


def pearson(x, y) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.shape != y.shape:
        raise ValueError("x and y must be the same length")
    if x.shape[0] < 2:
        return float("nan")
    x_centered = x - x.mean()
    y_centered = y - y.mean()
    denominator = np.sqrt((x_centered**2).sum() * (y_centered**2).sum())
    if denominator == 0:
        return float("nan")  # a constant feature has no correlation to report
    return float((x_centered * y_centered).sum() / denominator)


def spearman(x, y) -> float:
    """Spearman's rho: Pearson on average ranks."""
    return pearson(average_ranks(x), average_ranks(y))


# --- Regression --------------------------------------------------------------


@dataclass(frozen=True)
class RegressionResult:
    """A standardized OLS fit. Coefficients are in standard deviations."""

    features: tuple[str, ...]
    coefficients: np.ndarray
    r_squared: float
    adjusted_r_squared: float
    n_observations: int
    dropped: tuple[str, ...] = field(default=())

    def as_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {"feature": self.features, "beta": self.coefficients}
        ).sort_values("beta", key=np.abs, ascending=False, ignore_index=True)


def _standardize(frame: pd.DataFrame) -> tuple[np.ndarray, list[str], list[str]]:
    kept, dropped, columns = [], [], []
    for name in frame.columns:
        column = frame[name].to_numpy(dtype=float)
        spread = column.std()
        if spread == 0 or not np.isfinite(spread):
            dropped.append(name)  # constant column carries no information
            continue
        kept.append(name)
        columns.append((column - column.mean()) / spread)
    matrix = np.column_stack(columns) if columns else np.empty((len(frame), 0))
    return matrix, kept, dropped


def standardized_regression(features: pd.DataFrame, y) -> RegressionResult:
    """Regress standardized `y` on standardized `features`.

    Standardizing puts every hypothesis on the same footing: a beta is "how
    many standard deviations of roundness one standard deviation of this
    feature buys", comparable across features measured in coins, divisors and
    characters alike. The intercept is zero by construction and omitted.
    """
    y = np.asarray(y, dtype=float)
    if len(features) != y.shape[0]:
        raise ValueError("features and y must have the same number of rows")

    matrix, kept, dropped = _standardize(features)
    spread = y.std()
    target = (y - y.mean()) / spread if spread else np.zeros_like(y)

    if matrix.shape[1] == 0 or spread == 0:
        return RegressionResult(
            features=tuple(kept),
            coefficients=np.zeros(len(kept)),
            r_squared=0.0,
            adjusted_r_squared=0.0,
            n_observations=y.shape[0],
            dropped=tuple(dropped),
        )

    coefficients, *_ = np.linalg.lstsq(matrix, target, rcond=None)
    residuals = target - matrix @ coefficients
    total = float((target**2).sum())
    r_squared = 1.0 - float((residuals**2).sum()) / total if total else 0.0

    n, p = y.shape[0], matrix.shape[1]
    adjusted = (
        1.0 - (1.0 - r_squared) * (n - 1) / (n - p - 1) if n > p + 1 else float("nan")
    )
    return RegressionResult(
        features=tuple(kept),
        coefficients=coefficients,
        r_squared=r_squared,
        adjusted_r_squared=adjusted,
        n_observations=n,
        dropped=tuple(dropped),
    )


# --- Hypothesis scoreboard ---------------------------------------------------


def _aligned(
    scores: pd.Series | dict[int, float], features: pd.DataFrame
) -> tuple[pd.DataFrame, np.ndarray]:
    """Line the feature table up with the scores, on NUMBER_VALUE.

    Accepts the `NUMBERS` table as it arrives from Snowflake (uppercase column
    names, `NUMBER_VALUE` as a column or as the index).
    """
    features = features.copy()
    features.columns = [str(c).upper() for c in features.columns]
    if "NUMBER_VALUE" in features.columns:
        features = features.set_index("NUMBER_VALUE")
    features.index = features.index.astype(int)

    score_series = pd.Series(scores, dtype=float)
    score_series.index = score_series.index.astype(int)

    shared = features.index.intersection(score_series.index)
    if shared.empty:
        raise ValueError("no numbers in common between scores and features")
    shared = shared.sort_values()
    return features.loc[shared], score_series.loc[shared].to_numpy(dtype=float)


def feature_correlations(
    scores: pd.Series | dict[int, float], features: pd.DataFrame
) -> pd.DataFrame:
    """Spearman rho of R(x) against every known feature.

    `as_predicted` says whether the sign came out the way the hypothesis
    requires -- a strong correlation in the wrong direction is evidence
    *against* the hypothesis, not for it, and is easy to misread from rho alone.
    """
    aligned, y = _aligned(scores, features)
    rows = []
    for key, spec in HYPOTHESES.items():
        for feature, expected_sign in spec["features"].items():  # type: ignore[union-attr]
            if feature not in aligned.columns:
                continue
            rho = spearman(aligned[feature].to_numpy(dtype=float), y)
            rows.append(
                {
                    "hypothesis": key,
                    "hypothesis_label": spec["label"],
                    "feature": feature,
                    "spearman_rho": rho,
                    "expected_sign": expected_sign,
                    "as_predicted": bool(np.sign(rho) == expected_sign)
                    if np.isfinite(rho)
                    else False,
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(
        "spearman_rho", key=np.abs, ascending=False, ignore_index=True
    )


def compare_hypotheses(
    scores: pd.Series | dict[int, float], features: pd.DataFrame
) -> pd.DataFrame:
    """Score each hypothesis against R(x), alone and against the others.

    Two readings per hypothesis:

    - `r_squared` -- its own features regressed on roundness by themselves.
      This is "how much of roundness could this story explain if it were the
      only story?"
    - `unique_r_squared` -- how much the full model loses when this
      hypothesis's features are removed. This is "what does it explain that
      nobody else does?" Correlated features (5-multiples are also short in
      Roman numerals) share credit in the first reading and lose it in the
      second, so the two together are more honest than either alone.
    """
    aligned, y = _aligned(scores, features)
    available = {
        key: [f for f in spec["features"] if f in aligned.columns]  # type: ignore[union-attr]
        for key, spec in HYPOTHESES.items()
    }
    all_features = [f for key in HYPOTHESES for f in available[key]]
    full = standardized_regression(aligned[all_features], y) if all_features else None

    rows = []
    for key, spec in HYPOTHESES.items():
        columns = available[key]
        if not columns:
            continue
        alone = standardized_regression(aligned[columns], y)
        if full is None:
            unique = float("nan")
        else:
            others = [f for f in all_features if f not in columns]
            without = (
                standardized_regression(aligned[others], y).r_squared
                if others
                else 0.0
            )
            unique = full.r_squared - without
        best_feature = max(
            columns, key=lambda c: abs(spearman(aligned[c].to_numpy(float), y))
        )
        rows.append(
            {
                "hypothesis": key,
                "hypothesis_label": spec["label"],
                "n_features": len(columns),
                "r_squared": alone.r_squared,
                "adjusted_r_squared": alone.adjusted_r_squared,
                "unique_r_squared": unique,
                "best_feature": best_feature,
                "best_feature_rho": spearman(
                    aligned[best_feature].to_numpy(float), y
                ),
                "is_control": key not in COMPETING_HYPOTHESES,
            }
        )
    return pd.DataFrame(rows).sort_values(
        "r_squared", ascending=False, ignore_index=True
    )


def winning_hypothesis(scoreboard: pd.DataFrame) -> str | None:
    """The best-explaining competing hypothesis, ignoring the controls."""
    contenders = scoreboard[~scoreboard["is_control"]]
    if contenders.empty:
        return None
    return str(contenders.iloc[contenders["r_squared"].argmax()]["hypothesis"])
