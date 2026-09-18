"""End-to-end tests of the shell, the router and the intro page.

Streamlit's own ``AppTest`` harness runs ``streamlit_app.py`` headlessly, so
this exercises the real script -- page config, CSS, gate and all -- without a
browser. The two database writes the intro makes are stubbed, so nothing here
needs Snowflake.

What these are actually guarding:

* A respondent cannot reach a later stage by having ``stage`` set to it. The
  gate runs on every rerun, and :mod:`tests.test_state` covers its rules in
  isolation; here it is checked as the router actually applies it.
* The intro writes ``RESPONDENTS`` and ``INTRO_RESPONSES`` together, under one
  respondent id, and only after both questions are answered.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from app import queries  # noqa: E402

APP = str(ROOT / "streamlit_app.py")


@pytest.fixture(autouse=True)
def no_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every page's reads off the network.

    A run that ends on the comparisons or results stage renders that page, and
    both of them read. Unstubbed, that is a real connection attempt from a test
    suite that is meant to need no Snowflake at all. Empty frames are enough
    here: the sampler's candidate set is the whole domain and a missing pair
    counts as zero, and the results page has its own empty state.
    :mod:`tests.test_comparisons` and :mod:`tests.test_results` cover the two
    pages properly.
    """
    import streamlit as st

    empty_pairs = pd.DataFrame(
        {"PAIR_LOW": [], "PAIR_HIGH": [], "N_COMPARISONS": []}, dtype="int64"
    )
    monkeypatch.setattr(queries, "fetch_pair_counts", lambda: empty_pairs)
    monkeypatch.setattr(
        queries, "fetch_comparisons_for_scoring", lambda: pd.DataFrame()
    )
    monkeypatch.setattr(queries, "fetch_number_stats", lambda: pd.DataFrame())
    # The results page memoizes its fit; a stale entry from another test would
    # otherwise outlive these stubs.
    st.cache_data.clear()


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Capture the intro's inserts instead of sending them to Snowflake."""
    captured: list[tuple] = []
    monkeypatch.setattr(
        queries,
        "insert_respondent",
        lambda rid, *a, **k: captured.append(("respondent", rid)),
    )
    monkeypatch.setattr(
        queries,
        "insert_intro_response",
        lambda rid, random_number, unique_guess_number: captured.append(
            ("intro", rid, random_number, unique_guess_number)
        ),
    )
    return captured


def _fresh() -> AppTest:
    return AppTest.from_file(APP, default_timeout=30).run()


def _seeded(**session_state: object) -> AppTest:
    """A first run with session state pre-seeded, as if mid-session.

    Seeded before the first run rather than set on an already-rendered tree:
    ``app.state.init`` only fills in what is missing, and AppTest cannot
    rebuild widget state for a form that stops being rendered part-way through
    a run, which is exactly what the gate causes.
    """
    at = AppTest.from_file(APP, default_timeout=30)
    for key, value in session_state.items():
        at.session_state[key] = value
    return at.run()


# --- The shell ---------------------------------------------------------------


def test_the_app_starts_on_the_intro_without_errors() -> None:
    at = _fresh()
    assert not at.exception
    assert at.session_state["stage"] == "intro"
    assert len(at.number_input) == 2


def test_a_new_session_gets_a_respondent_id_but_writes_nothing(
    writes: list[tuple],
) -> None:
    # Minting the id is not the same as becoming a respondent: someone who
    # loads the app and leaves should not appear in the respondent count.
    at = _fresh()
    assert at.session_state["respondent_id"]
    assert writes == []


# --- The gate, as the router applies it --------------------------------------


@pytest.mark.parametrize("stage", ["comparisons", "survey", "results"])
def test_setting_a_later_stage_directly_lands_back_on_the_intro(stage: str) -> None:
    at = _fresh()
    at.session_state["stage"] = stage
    at.run()
    assert not at.exception
    assert at.session_state["stage"] == "intro"


def test_results_stay_gated_even_with_the_intro_done() -> None:
    at = _seeded(stage="results", intro_submitted=True)
    # Sent to finish the survey, not to the leaderboard -- seeing the current
    # standings first would anchor their own answers.
    assert not at.exception
    assert at.session_state["stage"] == "survey"


def test_results_open_once_the_survey_unlocks_them() -> None:
    at = _seeded(stage="results", intro_submitted=True, results_unlocked=True)
    assert not at.exception
    assert at.session_state["stage"] == "results"


# --- The intro ---------------------------------------------------------------


def test_submitting_with_a_question_unanswered_warns_and_writes_nothing(
    writes: list[tuple],
) -> None:
    at = _fresh()
    at.number_input[0].set_value(37)  # second question left empty
    at.button[0].click().run()

    assert [w.value for w in at.warning] == [
        "Both questions need an answer before you start."
    ]
    assert writes == []
    assert at.session_state["stage"] == "intro"


def test_submitting_writes_both_rows_and_opens_the_comparisons(
    writes: list[tuple],
) -> None:
    at = _submit_intro(37, 73)

    assert not at.exception
    respondent_id = at.session_state["respondent_id"]
    assert writes == [
        ("respondent", respondent_id),
        ("intro", respondent_id, 37, 73),
    ]
    assert at.session_state["intro_submitted"] is True
    assert at.session_state["stage"] == "comparisons"


def test_the_respondent_row_is_written_only_once(
    writes: list[tuple], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Snowflake does not enforce the primary key, so a retry after a failed
    # intro insert must not leave two respondent rows for one person.
    from app.db import DatabaseError

    def fail(*_a, **_k):
        raise DatabaseError("simulated write failure")

    monkeypatch.setattr(queries, "insert_intro_response", fail)

    at = _fresh()
    at.number_input[0].set_value(37)
    at.number_input[1].set_value(73)
    at.button[0].click().run()

    assert [e.value for e in at.error]  # the failure is surfaced
    assert at.session_state["stage"] == "intro"
    assert at.session_state["respondent_row_written"] is True
    assert writes == [("respondent", at.session_state["respondent_id"])]

    # Retry: the respondent row is not written again.
    monkeypatch.setattr(
        queries,
        "insert_intro_response",
        lambda rid, r, u: writes.append(("intro", rid, r, u)),
    )
    at.button[0].click().run()

    assert at.session_state["stage"] == "comparisons"
    assert [row[0] for row in writes] == ["respondent", "intro"]


# --- Router ------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["intro", "comparisons", "survey", "results"])
def test_every_stage_has_a_page_with_a_render_function(stage: str) -> None:
    # The router imports its page by name, so a renamed or missing module would
    # otherwise only show up when a respondent reaches that stage. Imported
    # directly rather than through streamlit_app, which runs the app on import.
    import importlib

    module = importlib.import_module(f"app.pages.{stage}")
    assert callable(getattr(module, "render", None))


def test_the_intro_cannot_be_submitted_twice(writes: list[tuple]) -> None:
    # A second INTRO_RESPONSES row for one respondent would double-count their
    # picks in the random-number analysis, so a session that has already
    # submitted is sent straight on rather than shown the form again.
    at = _seeded(stage="intro", intro_submitted=True)

    assert not at.exception
    assert at.session_state["stage"] == "comparisons"
    assert not at.number_input  # the form is not rendered at all
    assert writes == []


def _submit_intro(random_number: int, unique_guess_number: int) -> AppTest:
    at = _fresh()
    at.number_input[0].set_value(random_number)
    at.number_input[1].set_value(unique_guess_number)
    return at.button[0].click().run()
