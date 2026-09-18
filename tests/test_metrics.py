"""Checks on the descriptive metrics and the hypothesis scoreboard.

The scoreboard tests plant a known answer -- roundness is generated *from* one
hypothesis's feature -- and assert that the scoreboard names that hypothesis.
If it cannot recover a planted answer it cannot be trusted on the real one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import metrics  # noqa: E402
from scripts.generate_number_features import build_rows  # noqa: E402


@pytest.fixture(scope="module")
def features() -> pd.DataFrame:
    return pd.DataFrame(build_rows())


# --- Raw counts --------------------------------------------------------------


def test_win_percentages() -> None:
    frame = metrics.win_percentages(
        winners=[10, 10, 7, 50], losers=[7, 3, 10, 7], numbers=[3, 7, 10, 50]
    ).set_index("number")

    assert frame.loc[10, "wins"] == 2
    assert frame.loc[10, "losses"] == 1
    assert frame.loc[10, "comparisons"] == 3
    assert frame.loc[10, "win_pct"] == pytest.approx(100 * 2 / 3)
    assert frame.loc[3, "win_pct"] == 0.0


def test_unseen_numbers_have_no_win_percentage() -> None:
    frame = metrics.win_percentages([10], [7]).set_index("number")
    assert frame.loc[99, "comparisons"] == 0
    assert np.isnan(frame.loc[99, "win_pct"])


def test_respondent_weights_equalize_volume() -> None:
    ids = ["a"] * 4 + ["b"] * 1
    weights = metrics.respondent_weights(ids)
    assert weights["a"] == pytest.approx(0.25)
    assert weights["b"] == pytest.approx(1.0)

    per_row = metrics.comparison_weights(ids)
    assert per_row.sum() == pytest.approx(2.0)  # one unit per respondent


def test_left_win_rate() -> None:
    assert metrics.left_win_rate([10, 7, 3], [10, 10, 3]) == pytest.approx(2 / 3)
    assert np.isnan(metrics.left_win_rate([], []))


# --- Correlation -------------------------------------------------------------


def test_average_ranks_share_ties() -> None:
    assert metrics.average_ranks([5, 1, 5, 3]).tolist() == [3.5, 1.0, 3.5, 2.0]


def test_spearman_is_one_for_a_monotone_transform() -> None:
    x = np.arange(1, 21, dtype=float)
    assert metrics.spearman(x, np.exp(x)) == pytest.approx(1.0)
    assert metrics.spearman(x, -x) == pytest.approx(-1.0)


def test_spearman_of_a_constant_is_undefined() -> None:
    assert np.isnan(metrics.spearman([1, 2, 3], [7, 7, 7]))


# --- Regression --------------------------------------------------------------


def test_regression_recovers_a_planted_relationship() -> None:
    rng = np.random.default_rng(0)
    frame = pd.DataFrame({"a": rng.normal(size=200), "b": rng.normal(size=200)})
    y = 2.0 * frame["a"] - 1.0 * frame["b"]

    result = metrics.standardized_regression(frame, y)

    betas = dict(zip(result.features, result.coefficients))
    assert result.r_squared == pytest.approx(1.0)
    assert betas["a"] > 0 > betas["b"]
    assert abs(betas["a"]) > abs(betas["b"])
    assert result.as_frame().iloc[0]["feature"] == "a"


def test_regression_drops_constant_columns() -> None:
    frame = pd.DataFrame({"useful": [1.0, 2.0, 3.0, 4.0], "flat": [9.0] * 4})
    result = metrics.standardized_regression(frame, [1.0, 2.0, 3.0, 4.0])
    assert result.dropped == ("flat",)
    assert result.features == ("useful",)


def test_regression_on_a_constant_target_is_zero() -> None:
    frame = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    result = metrics.standardized_regression(frame, [5.0, 5.0, 5.0])
    assert result.r_squared == 0.0


# --- Hypothesis scoreboard ---------------------------------------------------


def _scores_from(features: pd.DataFrame, column: str, sign: int) -> pd.Series:
    """Synthetic R(x) driven entirely by one feature."""
    values = features[column].astype(float).to_numpy()
    return pd.Series(sign * values, index=features["NUMBER_VALUE"].astype(int))


@pytest.mark.parametrize(
    ("hypothesis", "column", "sign"),
    [
        ("additive_units", "MIN_COIN_COUNT", -1),
        ("geometric", "DIVISOR_COUNT", +1),
        ("gravity", "GRAVITY_SCORE", +1),
        ("roman", "ROMAN_LENGTH", -1),
    ],
)
def test_scoreboard_names_the_planted_hypothesis(
    features: pd.DataFrame, hypothesis: str, column: str, sign: int
) -> None:
    scores = _scores_from(features, column, sign)
    scoreboard = metrics.compare_hypotheses(scores, features)
    assert metrics.winning_hypothesis(scoreboard) == hypothesis

    row = scoreboard.set_index("hypothesis").loc[hypothesis]
    assert row["r_squared"] > 0.9
    assert row["unique_r_squared"] > 0.0


def test_correlations_flag_the_predicted_direction(features: pd.DataFrame) -> None:
    scores = _scores_from(features, "ROMAN_LENGTH", -1)
    frame = metrics.feature_correlations(scores, features).set_index("feature")

    assert frame.loc["ROMAN_LENGTH", "spearman_rho"] == pytest.approx(-1.0)
    assert bool(frame.loc["ROMAN_LENGTH", "as_predicted"])

    # Same feature, roundness generated the wrong way round: still a perfect
    # correlation, but now evidence against the hypothesis.
    backwards = metrics.feature_correlations(
        _scores_from(features, "ROMAN_LENGTH", +1), features
    ).set_index("feature")
    assert backwards.loc["ROMAN_LENGTH", "spearman_rho"] == pytest.approx(1.0)
    assert not bool(backwards.loc["ROMAN_LENGTH", "as_predicted"])


def test_scoreboard_covers_every_hypothesis(features: pd.DataFrame) -> None:
    scores = _scores_from(features, "GRAVITY_SCORE", +1)
    scoreboard = metrics.compare_hypotheses(scores, features)
    assert set(scoreboard["hypothesis"]) == set(metrics.HYPOTHESES)
    controls = scoreboard.set_index("hypothesis").loc["baseline", "is_control"]
    assert bool(controls)
    assert metrics.winning_hypothesis(scoreboard) != "baseline"


def test_scoreboard_accepts_number_value_as_the_index(
    features: pd.DataFrame,
) -> None:
    indexed = features.set_index("NUMBER_VALUE")
    scores = _scores_from(features, "DIVISOR_COUNT", +1)
    assert metrics.winning_hypothesis(
        metrics.compare_hypotheses(scores, indexed)
    ) == "geometric"


def test_scoreboard_tolerates_a_partial_score_set(features: pd.DataFrame) -> None:
    """Scores only exist for numbers that have been compared."""
    scores = _scores_from(features, "DIVISOR_COUNT", +1).iloc[:40]
    scoreboard = metrics.compare_hypotheses(scores, features)
    assert scoreboard.set_index("hypothesis").loc["geometric", "r_squared"] > 0.5


def test_scoreboard_rejects_a_disjoint_score_set(features: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="no numbers in common"):
        metrics.compare_hypotheses(pd.Series({999: 1.0, 1000: 2.0}), features)
