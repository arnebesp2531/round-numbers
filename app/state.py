"""Session state: respondent identity, stage transitions and the results gate.

The flow is linear -- ``intro -> comparisons -> survey -> results`` -- driven by
``st.session_state.stage`` rather than ``st.navigation``. A linear router keeps
the results gate trivial to enforce and behaves identically in Community Cloud
and in SiS.

Two things in here are experiment-critical rather than housekeeping:

* **The results gate.** Results are unlocked only once a respondent has
  submitted their own survey. Showing the current leaderboard earlier would
  anchor their answers, which is exactly the intuition the instrument is trying
  to measure. :func:`resolve_stage` decides what a session is allowed to see and
  is applied by the router on every rerun, so setting ``stage`` by hand -- or a
  stale value surviving in a reloaded session -- cannot get past it.
* **The pair-count overlay.** ``V_PAIR_COUNTS`` is cached for a minute, so
  within one long session the cached counts go stale and the same cold pairs
  keep winning the coverage weighting. Every pair served this session is
  recorded here and merged over the cached counts, so a long session keeps
  spreading out instead of circling.

The stage rules live in :func:`resolve_stage` as a pure function of three
booleans, so they are unit-testable without a Streamlit script run. Everything
else in this module is thin plumbing over ``st.session_state``.
"""

from __future__ import annotations

import random
import time
import uuid
from typing import Any, Iterable

import streamlit as st

from app.config import MAX_RESPONSE_MS
from app.sampling import Pair, canonical

# --- Stages ------------------------------------------------------------------

STAGE_INTRO = "intro"
STAGE_COMPARISONS = "comparisons"
STAGE_SURVEY = "survey"
STAGE_RESULTS = "results"

#: The linear flow, in order.
STAGES: tuple[str, ...] = (STAGE_INTRO, STAGE_COMPARISONS, STAGE_SURVEY, STAGE_RESULTS)

# --- session_state keys ------------------------------------------------------
# Namespaced so nothing here can collide with a widget key.

KEY_RESPONDENT_ID = "respondent_id"
KEY_STAGE = "stage"
KEY_INTRO_SUBMITTED = "intro_submitted"
KEY_RESPONDENT_ROW_WRITTEN = "respondent_row_written"
KEY_RESULTS_UNLOCKED = "results_unlocked"
KEY_SEEN_PAIRS = "seen_pairs"
KEY_PAIR_COUNT_OVERLAY = "pair_count_overlay"
KEY_PAGE_NUMBER = "page_number"
KEY_COMPARISONS_SUBMITTED = "comparisons_submitted"
KEY_RNG = "rng"
KEY_PAGE_PAIRS = "page_pairs"
KEY_PAGE_ANSWERS = "page_answers"
KEY_ANSWER_MARK = "answer_mark"
KEY_AWAITING_CONTINUE = "awaiting_continue"


# --- The gate ----------------------------------------------------------------


def resolve_stage(
    requested: str | None,
    *,
    intro_submitted: bool,
    results_unlocked: bool,
) -> str:
    """The stage a session is actually allowed to render.

    Pure, and the single place the flow's rules are written down:

    * An unknown or missing stage falls back to the intro.
    * *Every* later stage needs the intro submitted first, because the
      ``RESPONDENTS`` row written there is what every later row hangs off.
    * Results additionally need ``results_unlocked``, set only on survey
      submit. A session that asks for results without it is sent to finish the
      survey.
    """
    if requested not in STAGES or requested == STAGE_INTRO:
        return STAGE_INTRO
    if not intro_submitted:
        return STAGE_INTRO
    if requested == STAGE_RESULTS and not results_unlocked:
        return STAGE_SURVEY
    return requested


# --- Initialization ----------------------------------------------------------


def init() -> None:
    """Seed session state if this is a new session. Idempotent.

    Called at the top of every rerun. The respondent id is minted here and
    never leaves the session; no personal data is collected.

    Note that minting the id does *not* write a ``RESPONDENTS`` row -- that
    happens on intro submit. A visitor who loads the app and leaves should not
    show up as a respondent.
    """
    defaults = {
        KEY_RESPONDENT_ID: lambda: str(uuid.uuid4()),
        KEY_STAGE: lambda: STAGE_INTRO,
        KEY_INTRO_SUBMITTED: lambda: False,
        KEY_RESPONDENT_ROW_WRITTEN: lambda: False,
        KEY_RESULTS_UNLOCKED: lambda: False,
        KEY_SEEN_PAIRS: set,
        KEY_PAIR_COUNT_OVERLAY: dict,
        KEY_PAGE_NUMBER: lambda: 1,
        KEY_COMPARISONS_SUBMITTED: lambda: 0,
        # One Random per session, so a rerun mid-page does not reshuffle the
        # pairs a respondent is part-way through answering.
        KEY_RNG: random.Random,
        # The page currently in front of the respondent. None means the next
        # render has to sample one.
        KEY_PAGE_PAIRS: lambda: None,
        KEY_PAGE_ANSWERS: dict,
        KEY_ANSWER_MARK: lambda: None,
        KEY_AWAITING_CONTINUE: lambda: False,
    }
    for key, factory in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = factory()


# --- Identity ----------------------------------------------------------------


def respondent_id() -> str:
    """This session's random UUID, stored on every row it writes."""
    return str(st.session_state[KEY_RESPONDENT_ID])


def rng() -> random.Random:
    """The session's random source, used for pair sampling and display order."""
    return st.session_state[KEY_RNG]


# --- Stage transitions -------------------------------------------------------


def stage() -> str:
    """The currently requested stage, before the gate is applied."""
    return str(st.session_state[KEY_STAGE])


def go_to(next_stage: str, *, rerun: bool = True) -> None:
    """Move to ``next_stage``, by default rerunning immediately.

    Callers pass ``rerun=False`` only when more state has to be written before
    the next render -- the gate still runs either way.
    """
    if next_stage not in STAGES:
        raise ValueError(f"unknown stage {next_stage!r}; expected one of {STAGES}")
    st.session_state[KEY_STAGE] = next_stage
    if rerun:
        st.rerun()


# --- Intro -------------------------------------------------------------------


def intro_submitted() -> bool:
    return bool(st.session_state[KEY_INTRO_SUBMITTED])


def mark_intro_submitted() -> None:
    """Record that the intro rows landed. Opens the comparisons stage."""
    st.session_state[KEY_INTRO_SUBMITTED] = True


def respondent_row_written() -> bool:
    """Whether this session's ``RESPONDENTS`` row has already been inserted.

    Snowflake does not enforce the primary key, so a retry after a partially
    failed intro submit would otherwise duplicate the row.
    """
    return bool(st.session_state[KEY_RESPONDENT_ROW_WRITTEN])


def mark_respondent_row_written() -> None:
    st.session_state[KEY_RESPONDENT_ROW_WRITTEN] = True


# --- Comparison pages --------------------------------------------------------


def page_number() -> int:
    """The 1-based page the respondent is on."""
    return int(st.session_state[KEY_PAGE_NUMBER])


def comparisons_submitted() -> int:
    """How many comparisons this session has successfully written."""
    return int(st.session_state[KEY_COMPARISONS_SUBMITTED])


def seen_pairs() -> set[Pair]:
    """Canonical pairs already served this session, to exclude from sampling."""
    return st.session_state[KEY_SEEN_PAIRS]


def pair_count_overlay() -> dict[Pair, int]:
    """Local counts to merge over the cached ``V_PAIR_COUNTS`` numbers."""
    return st.session_state[KEY_PAIR_COUNT_OVERLAY]


def record_served_pairs(pairs: Iterable[Pair]) -> None:
    """Note pairs put in front of the respondent, however they answer.

    Served rather than submitted on purpose: a pair shown on an abandoned page
    should not come round again in the same session, and the coverage weighting
    should already treat it as spoken for.
    """
    seen = seen_pairs()
    overlay = pair_count_overlay()
    for pair in pairs:
        key = canonical(*pair)
        seen.add(key)
        overlay[key] = overlay.get(key, 0) + 1


def record_submitted_page(n_comparisons: int) -> None:
    """Advance the page counter after a page's batched write succeeded."""
    if n_comparisons < 0:
        raise ValueError(f"n_comparisons must be >= 0, got {n_comparisons}")
    st.session_state[KEY_COMPARISONS_SUBMITTED] = comparisons_submitted() + n_comparisons
    st.session_state[KEY_PAGE_NUMBER] = page_number() + 1


# --- The page in front of the respondent -------------------------------------
# Pairs are held in session_state rather than resampled each render: a rerun
# happens on every click, and resampling would reshuffle a page the respondent
# is part-way through answering.


def page_pairs() -> list[Pair] | None:
    """The current page as displayed, ``(left, right)`` per row.

    These are *display*-ordered, not canonical, because which number sits on
    the left is itself recorded. ``None`` means no page has been sampled yet.
    """
    return st.session_state[KEY_PAGE_PAIRS]


def start_page(pairs: Iterable[Pair]) -> None:
    """Put a freshly sampled, display-ordered page in front of the respondent."""
    st.session_state[KEY_PAGE_PAIRS] = [(int(left), int(right)) for left, right in pairs]
    st.session_state[KEY_PAGE_ANSWERS] = {}
    st.session_state[KEY_ANSWER_MARK] = time.monotonic()


def clear_page() -> None:
    """Drop the current page, so the next render samples a new one."""
    st.session_state[KEY_PAGE_PAIRS] = None
    st.session_state[KEY_PAGE_ANSWERS] = {}
    st.session_state[KEY_ANSWER_MARK] = None


def page_answers() -> dict[int, dict[str, Any]]:
    """Row index -> ``{"winner": int, "response_ms": int | None}``.

    Answers are held here until the whole page is submitted, so a page costs
    one batched write rather than one per click.
    """
    return st.session_state[KEY_PAGE_ANSWERS]


def record_answer(index: int, winner: int) -> None:
    """Note a click. Also stamps how long the respondent took over that row.

    A row may be re-answered; the later click replaces the earlier one, timed
    from the same mark, because the whole row-plus-rethink is the response.
    """
    # Reached from a button callback, which runs before the rerun's script
    # body -- and therefore before ``init`` has had a chance to run on a
    # session whose state somehow lost these keys.
    if KEY_PAGE_ANSWERS not in st.session_state:
        st.session_state[KEY_PAGE_ANSWERS] = {}
    st.session_state[KEY_PAGE_ANSWERS][int(index)] = {
        "winner": int(winner),
        "response_ms": _take_elapsed_ms(),
    }


def _take_elapsed_ms() -> int | None:
    """Milliseconds since the page opened or the previous row was answered.

    Respondents work down the page, so the gap between consecutive clicks is a
    usable per-row response time -- fast answers are the purest System 1
    signal. Implausibly long gaps mean someone walked away rather than
    deliberated, so they are recorded as unknown instead of as a huge number.
    """
    now = time.monotonic()
    mark = st.session_state.get(KEY_ANSWER_MARK)
    st.session_state[KEY_ANSWER_MARK] = now
    if mark is None:
        return None
    elapsed_ms = int((now - mark) * 1000)
    if elapsed_ms < 0 or elapsed_ms > MAX_RESPONSE_MS:
        return None
    return elapsed_ms


def page_is_complete() -> bool:
    """Whether every row on the current page has been answered."""
    pairs = page_pairs()
    if not pairs:
        return False
    return len(page_answers()) == len(pairs)


def awaiting_continue() -> bool:
    """Whether a page has just been written and the respondent is being asked
    whether to keep going."""
    return bool(st.session_state[KEY_AWAITING_CONTINUE])


def set_awaiting_continue(value: bool) -> None:
    st.session_state[KEY_AWAITING_CONTINUE] = bool(value)


# --- Results gate ------------------------------------------------------------


def results_unlocked() -> bool:
    return bool(st.session_state[KEY_RESULTS_UNLOCKED])


def unlock_results() -> None:
    """Called only on survey submit. See the module docstring on anchoring."""
    st.session_state[KEY_RESULTS_UNLOCKED] = True
