"""The survey page, driven through Streamlit's headless ``AppTest``.

Only ``execute_many`` is stubbed, so the real
:func:`app.queries.insert_survey_response` does the validating and the
trimming -- the row asserted on here is the row Snowflake would receive.

What these guard:

* Submitting is the only thing that unlocks the results, and a failed write
  does not unlock them.
* Every question is optional. A respondent who has already given their
  comparisons is not held hostage over an empty text box.
* One survey row per respondent per session, so the random-number analysis
  (which joins intro to survey) cannot double-count anyone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from app import config, queries  # noqa: E402
from app.db import DatabaseError  # noqa: E402

APP = str(ROOT / "streamlit_app.py")


@pytest.fixture
def written() -> list[tuple]:
    """Rows handed to ``execute_many``."""
    return []


@pytest.fixture(autouse=True)
def stub_db(monkeypatch: pytest.MonkeyPatch, written: list[tuple]) -> None:
    import streamlit as st

    def capture(sql: str, rows) -> int:
        written.append((sql, list(rows)))
        return len(rows)

    monkeypatch.setattr(queries, "execute_many", capture)
    # A run that lands on the results page after submitting must not reach for
    # a connection; its own empty state covers the no-data case.
    monkeypatch.setattr(queries, "fetch_comparisons_for_scoring", lambda: pd.DataFrame())
    monkeypatch.setattr(queries, "fetch_number_stats", lambda: pd.DataFrame())
    st.cache_data.clear()


def _on_survey(**session_state: object) -> AppTest:
    """A session that has cleared the intro gate and finished comparing."""
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["stage"] = "survey"
    at.session_state["intro_submitted"] = True
    for key, value in session_state.items():
        at.session_state[key] = value
    return at.run()


def _submit(at: AppTest) -> AppTest:
    return at.button[0].click().run()


def _survey_row(written: list[tuple]) -> tuple:
    assert len(written) == 1, f"expected one write, got {len(written)}"
    _, rows = written[0]
    assert len(rows) == 1
    return rows[0]


# --- The page ----------------------------------------------------------------


def test_the_survey_renders_its_four_questions() -> None:
    at = _on_survey()
    assert not at.exception
    assert len(at.text_area) == 2
    assert len(at.number_input) == 2


def test_submitting_writes_the_row_and_unlocks_the_results(
    written: list[tuple],
) -> None:
    at = _on_survey()
    at.text_area[0].set_value("Numbers you can pay for in two coins.")
    at.text_area[1].set_value("Money, mostly.")
    at.number_input[0].set_value(97)
    at.number_input[1].set_value(42)
    at = _submit(at)

    assert not at.exception
    row = _survey_row(written)
    assert row == (
        at.session_state["respondent_id"],
        "Numbers you can pay for in two coins.",
        "Money, mostly.",
        97,
        42,
    )
    assert at.session_state["results_unlocked"] is True
    assert at.session_state["stage"] == "results"


def test_every_question_can_be_left_blank(written: list[tuple]) -> None:
    # A respondent who has given their comparisons has done the valuable part.
    at = _submit(_on_survey())

    assert not at.exception
    respondent_id, definition, factors, least_round, post_random = _survey_row(written)
    assert respondent_id == at.session_state["respondent_id"]
    assert (definition, factors, least_round, post_random) == (None, None, None, None)
    assert at.session_state["stage"] == "results"


def test_a_failed_write_leaves_the_results_locked(
    monkeypatch: pytest.MonkeyPatch, written: list[tuple]
) -> None:
    def fail(*_a, **_k):
        raise DatabaseError("simulated write failure")

    monkeypatch.setattr(queries, "execute_many", fail)

    at = _on_survey()
    at.number_input[0].set_value(97)
    at = _submit(at)

    assert [e.value for e in at.error]  # the failure is surfaced
    assert at.session_state["results_unlocked"] is False
    assert at.session_state["stage"] == "survey"
    assert written == []


def test_a_second_submit_writes_nothing_and_goes_to_the_results(
    written: list[tuple],
) -> None:
    # A second SURVEY_RESPONSES row for one respondent would double-count them
    # in V_RANDOM_NUMBER_PICKS, which joins intro to survey one row apiece.
    at = _on_survey(results_unlocked=True)

    assert not at.exception
    assert at.session_state["stage"] == "results"
    assert not at.text_area  # the form is not rendered at all
    assert written == []


def test_long_free_text_is_trimmed_to_the_column_width(written: list[tuple]) -> None:
    # The widget caps typing at MAX_FREE_TEXT_CHARS, but the cap that matters
    # is the one in app.queries: VARCHAR(2000) rejects anything longer.
    at = _on_survey()
    at.text_area[0].set_value("x" * (config.MAX_FREE_TEXT_CHARS + 500))
    at = _submit(at)

    _, definition, _, _, _ = _survey_row(written)
    assert len(definition) == config.MAX_FREE_TEXT_CHARS
