"""The results page, driven through Streamlit's headless ``AppTest``.

Every read is stubbed with a synthetic dataset built to a known answer: 50 wins
every comparison it appears in and 47 loses every one of its, so whatever the
scoring path does, the headline is not in doubt. That makes these tests about
the page -- the gate, the metric switch, the panels that hide themselves, the
error paths -- while :mod:`tests.test_bradley_terry` and
:mod:`tests.test_metrics` cover the arithmetic underneath it.

The bootstrap sample count is turned down here. At the production 200 it is
several seconds per fit, which is fine for a page cached for five minutes and
absurd for a test suite that is meant to run in seconds.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from analysis import metrics  # noqa: E402
from app import config, queries  # noqa: E402
from app.db import DatabaseError  # noqa: E402
from scripts.generate_number_features import build_rows  # noqa: E402

APP = str(ROOT / "streamlit_app.py")

#: The synthetic dataset's planted answer.
ALWAYS_WINS = 50
ALWAYS_LOSES = 47


def _comparisons() -> pd.DataFrame:
    """50 beats everyone, 47 loses to everyone, across two respondents."""
    rows = []
    for number in range(config.MIN_NUMBER, config.MAX_NUMBER + 1):
        if number not in (ALWAYS_WINS, ALWAYS_LOSES):
            rows.append(("r1", ALWAYS_WINS, number))
            rows.append(("r2", number, ALWAYS_LOSES))
    rows.append(("r1", ALWAYS_WINS, ALWAYS_LOSES))
    frame = pd.DataFrame(rows, columns=["RESPONDENT_ID", "WINNER_NUMBER", "LOSER_NUMBER"])
    counts = frame["RESPONDENT_ID"].value_counts()
    frame["WEIGHT"] = frame["RESPONDENT_ID"].map(1.0 / counts)
    return frame


def _number_stats(comparisons: pd.DataFrame) -> pd.DataFrame:
    """V_NUMBER_STATS as the view returns it -- WIN_PCT as a fraction."""
    stats = metrics.win_percentages(
        comparisons["WINNER_NUMBER"], comparisons["LOSER_NUMBER"]
    )
    return pd.DataFrame(
        {
            "NUMBER_VALUE": stats["number"],
            "N_COMPARISONS": stats["comparisons"],
            "N_WINS": stats["wins"],
            "N_LOSSES": stats["losses"],
            "WIN_PCT": stats["win_pct"] / 100.0,
        }
    )


def _pair_counts(comparisons: pd.DataFrame) -> pd.DataFrame:
    low = comparisons[["WINNER_NUMBER", "LOSER_NUMBER"]].min(axis=1)
    high = comparisons[["WINNER_NUMBER", "LOSER_NUMBER"]].max(axis=1)
    counted = (
        pd.DataFrame({"PAIR_LOW": low, "PAIR_HIGH": high})
        .value_counts()
        .reset_index(name="N_COMPARISONS")
    )
    counted["LOW_WINS"] = 0
    counted["HIGH_WINS"] = 0
    return counted


@pytest.fixture
def data() -> dict[str, pd.DataFrame]:
    """Every read the page makes, keyed by the query function's name."""
    comparisons = _comparisons()
    return {
        "fetch_comparisons_for_scoring": comparisons,
        "fetch_number_stats": _number_stats(comparisons),
        "fetch_pair_counts": _pair_counts(comparisons),
        "fetch_numbers": pd.DataFrame(build_rows()),
        "fetch_collection_totals": pd.DataFrame(
            [
                {
                    "N_COMPARISONS": len(comparisons),
                    "N_RESPONDENTS": 2,
                    "N_RESPONDENTS_COMPARING": 2,
                    "N_SURVEYS_SUBMITTED": 2,
                    "N_PAIRS_COVERED": 197,
                    "N_PAIRS_TOTAL": config.TOTAL_PAIRS,
                }
            ]
        ),
        "fetch_position_bias": pd.DataFrame(
            [
                {
                    "POSITION_IN_PAGE": 0,
                    "N_COMPARISONS": len(comparisons),
                    "N_LEFT_WINS": len(comparisons) // 2,
                    "LEFT_WIN_PCT": 0.5,
                    "MEDIAN_RESPONSE_MS": 1200,
                }
            ]
        ),
        "fetch_random_number_picks": pd.DataFrame(
            [
                {
                    "RESPONDENT_ID": "r1",
                    "RANDOM_NUMBER": 7,
                    "UNIQUE_GUESS_NUMBER": 83,
                    "POST_RANDOM_NUMBER": 50,
                    "LEAST_ROUND_NUMBER": 47,
                },
                {
                    "RESPONDENT_ID": "r2",
                    "RANDOM_NUMBER": 37,
                    "UNIQUE_GUESS_NUMBER": 83,
                    "POST_RANDOM_NUMBER": 13,
                    "LEAST_ROUND_NUMBER": 47,
                },
            ]
        ),
        # Empty until sql/06_ai_classify_survey.sql has been hand-run, which is
        # the case these two have to degrade gracefully in.
        "fetch_survey_classification_counts": pd.DataFrame(
            columns=["CLASSIFICATION", "N_RESPONDENTS"]
        ),
        "fetch_survey_free_text": pd.DataFrame(
            columns=["RESPONDENT_ID", "ROUNDNESS_DEFINITION", "INTUITION_FACTORS", "SUBMITTED_AT"]
        ),
    }


@pytest.fixture(autouse=True)
def stub_reads(
    monkeypatch: pytest.MonkeyPatch, data: dict[str, pd.DataFrame]
) -> None:
    import streamlit as st

    for name, frame in data.items():
        monkeypatch.setattr(queries, name, (lambda f: lambda: f)(frame))
    monkeypatch.setattr(config, "BT_BOOTSTRAP_SAMPLES", 12)
    # The page memoizes its fit for five minutes; without this the second test
    # would score the first test's data.
    st.cache_data.clear()


def _on_results() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["stage"] = "results"
    at.session_state["intro_submitted"] = True
    at.session_state["results_unlocked"] = True
    return at.run()


def _all_text(at: AppTest) -> str:
    return "\n".join(
        [element.value for element in at.markdown]
        + [element.value for element in at.caption]
        + [element.value for element in at.info]
    )


# --- The headline ------------------------------------------------------------


def test_the_planted_answer_comes_out_of_the_scoring() -> None:
    at = _on_results()

    assert not at.exception
    values = [m.value for m in at.metric]
    assert str(ALWAYS_WINS) in values
    assert str(ALWAYS_LOSES) in values


def test_switching_metric_keeps_the_same_answer() -> None:
    # Bradley-Terry and raw win% disagree in general -- that is the point of
    # showing both -- but not on a number that never lost.
    at = _on_results()
    at.radio[0].set_value("Raw win %").run()

    assert not at.exception
    values = [m.value for m in at.metric]
    assert str(ALWAYS_WINS) in values
    assert str(ALWAYS_LOSES) in values


def test_the_metric_picker_offers_all_three_scores() -> None:
    at = _on_results()
    from app.pages import results

    assert list(at.radio[0].options) == list(results.METRICS)


# --- Panels ------------------------------------------------------------------


def test_the_hypothesis_panel_names_a_winner() -> None:
    at = _on_results()
    text = _all_text(at)
    assert "best-fitting explanation" in text


def test_the_classification_panel_is_hidden_until_the_batch_has_run() -> None:
    at = _on_results()
    assert "How people explained it" not in _all_text(at)


def test_the_classification_panel_appears_once_there_are_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        queries,
        "fetch_survey_classification_counts",
        lambda: pd.DataFrame(
            {"CLASSIFICATION": ["money", "factors"], "N_RESPONDENTS": [5, 3]}
        ),
    )
    at = _on_results()
    assert "How people explained it" in _all_text(at)


def test_free_text_is_rendered_as_text_never_as_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Written by one anonymous respondent and shown to others, so a heading or
    # a link in someone's answer must land as the characters they typed.
    answer = "# not a heading [not a link](http://example.com)"
    monkeypatch.setattr(
        queries,
        "fetch_survey_classification_counts",
        lambda: pd.DataFrame({"CLASSIFICATION": ["money"], "N_RESPONDENTS": [1]}),
    )
    monkeypatch.setattr(
        queries,
        "fetch_survey_free_text",
        lambda: pd.DataFrame(
            [
                {
                    "RESPONDENT_ID": "r1",
                    "ROUNDNESS_DEFINITION": answer,
                    "INTUITION_FACTORS": None,
                    "SUBMITTED_AT": pd.Timestamp("2026-01-01"),
                }
            ]
        ),
    )
    at = _on_results()

    assert not at.exception
    assert answer in [element.value for element in at.text]
    assert answer not in _all_text(at)


def test_the_coverage_and_footer_report_the_real_totals() -> None:
    at = _on_results()
    values = [m.value for m in at.metric]
    assert f"{len(_comparisons()):,}" in values  # comparisons
    assert "197" in values  # pairs covered


# --- Degrading ---------------------------------------------------------------


def test_no_comparisons_yet_says_so_instead_of_charting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(queries, "fetch_comparisons_for_scoring", lambda: pd.DataFrame())
    at = _on_results()

    assert not at.exception
    assert any("No comparisons" in info.value for info in at.info)


def test_a_failed_read_is_reported_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail():
        raise DatabaseError("simulated read failure")

    monkeypatch.setattr(queries, "fetch_comparisons_for_scoring", fail)
    at = _on_results()

    assert not at.exception
    assert [e.value for e in at.error]


def test_an_optional_panel_failing_does_not_take_the_page_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The classification panel needs a view the service role may not have been
    # granted, or a Cortex feature that may not exist in the region.
    def fail():
        raise DatabaseError("simulated missing grant")

    monkeypatch.setattr(queries, "fetch_survey_classification_counts", fail)
    at = _on_results()

    assert not at.exception
    assert not [e.value for e in at.error]
    assert "How much to trust this" in _all_text(at)
