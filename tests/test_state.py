"""Tests for the stage gate.

The gate is the reason a respondent cannot see the leaderboard before
answering: results anchored by the current standings are worthless as data.
:func:`app.state.resolve_stage` is a pure function of the requested stage and
two booleans precisely so that rule can be tested without a Streamlit script
run or a database.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.state import (  # noqa: E402
    STAGE_COMPARISONS,
    STAGE_INTRO,
    STAGE_RESULTS,
    STAGE_SURVEY,
    STAGES,
    resolve_stage,
)


# --- Fresh session -----------------------------------------------------------


@pytest.mark.parametrize("requested", STAGES)
def test_a_fresh_session_only_ever_gets_the_intro(requested: str) -> None:
    # Nothing submitted yet, so no stage but the intro is reachable, whichever
    # one the session asks for.
    assert (
        resolve_stage(requested, intro_submitted=False, results_unlocked=False)
        == STAGE_INTRO
    )


@pytest.mark.parametrize("requested", [None, "", "leaderboard", "Results", 42])
def test_an_unknown_stage_falls_back_to_the_intro(requested: object) -> None:
    assert (
        resolve_stage(requested, intro_submitted=True, results_unlocked=True)  # type: ignore[arg-type]
        == STAGE_INTRO
    )


# --- After the intro ---------------------------------------------------------


@pytest.mark.parametrize("requested", [STAGE_INTRO, STAGE_COMPARISONS, STAGE_SURVEY])
def test_the_intro_opens_comparisons_and_the_survey(requested: str) -> None:
    assert (
        resolve_stage(requested, intro_submitted=True, results_unlocked=False)
        == requested
    )


def test_results_are_gated_until_the_survey_is_submitted() -> None:
    # The whole point of the gate: asking for results mid-session sends the
    # respondent to finish the survey instead.
    assert (
        resolve_stage(STAGE_RESULTS, intro_submitted=True, results_unlocked=False)
        == STAGE_SURVEY
    )


def test_results_open_once_unlocked() -> None:
    assert (
        resolve_stage(STAGE_RESULTS, intro_submitted=True, results_unlocked=True)
        == STAGE_RESULTS
    )


def test_unlocked_results_still_need_the_intro() -> None:
    # Should not be reachable -- results_unlocked is only set on survey submit,
    # which needs the intro. Pinned so the gate stays safe if that ever slips.
    assert (
        resolve_stage(STAGE_RESULTS, intro_submitted=False, results_unlocked=True)
        == STAGE_INTRO
    )


@pytest.mark.parametrize("requested", STAGES)
def test_every_resolved_stage_is_a_real_stage(requested: str) -> None:
    for intro_submitted in (False, True):
        for results_unlocked in (False, True):
            resolved = resolve_stage(
                requested,
                intro_submitted=intro_submitted,
                results_unlocked=results_unlocked,
            )
            assert resolved in STAGES
