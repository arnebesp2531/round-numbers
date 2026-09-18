"""Every SQL statement in the project, one function each.

Pages never write SQL. They call a function here, which validates its inputs
and hands a parameterized statement to :mod:`app.db`. Two consequences worth
stating plainly:

* **Validation cannot be bypassed.** The checks that numbers are integers in
  1-100 and that ``winner_number`` is one of the two numbers presented live on
  this side of the boundary, not in the UI. A page that forgets to validate
  still cannot write a bad row.
* **Only identifiers are interpolated.** Object names come from
  :mod:`app.config` because Snowflake cannot bind an identifier. Every *value*
  travels as a ``?`` parameter. No statement is built from user input.

Reads are cached with ``st.cache_data``; the warehouse is XS with a 60s
auto-suspend, so a repeated query is a cold-start cost, not a free one.
"""

from __future__ import annotations

import uuid
from numbers import Integral
from typing import Any, Mapping, Sequence

import pandas as pd
import streamlit as st

from app import config
from app.db import execute_many, get_backend, query_df

# --- Object names ------------------------------------------------------------
# Resolved once from config. These are identifiers, never values.
_RESPONDENTS = config.qualified(config.TABLE_RESPONDENTS)
_INTRO_RESPONSES = config.qualified(config.TABLE_INTRO_RESPONSES)
_COMPARISONS = config.qualified(config.TABLE_COMPARISONS)
_SURVEY_RESPONSES = config.qualified(config.TABLE_SURVEY_RESPONSES)
_NUMBERS = config.qualified(config.TABLE_NUMBERS)
_V_PAIR_COUNTS = config.qualified(config.VIEW_PAIR_COUNTS)
_V_NUMBER_STATS = config.qualified(config.VIEW_NUMBER_STATS)
_V_RESPONDENT_WEIGHTS = config.qualified(config.VIEW_RESPONDENT_WEIGHTS)
_V_COMPARISONS_ENRICHED = config.qualified(config.VIEW_COMPARISONS_ENRICHED)
_V_COLLECTION_TOTALS = config.qualified(config.VIEW_COLLECTION_TOTALS)
_V_RANDOM_NUMBER_PICKS = config.qualified(config.VIEW_RANDOM_NUMBER_PICKS)
_V_SURVEY_FREE_TEXT = config.qualified(config.VIEW_SURVEY_FREE_TEXT)
_V_SURVEY_CLASSIFICATION_COUNTS = config.qualified(
    config.VIEW_SURVEY_CLASSIFICATION_COUNTS
)

# Static reference data: 100 rows that never change while the app is running.
_NUMBERS_CACHE_TTL_SECONDS = 3600


# --- Validation --------------------------------------------------------------


def app_source() -> str:
    """The `APP_SOURCE` tag for rows written from this process."""
    return (
        config.APP_SOURCE_SIS
        if get_backend() == "sis"
        else config.APP_SOURCE_COMMUNITY_CLOUD
    )


def validate_number(value: Any, field: str = "number") -> int:
    """Return ``value`` as an int, or raise if it is not an integer in 1-100.

    ``bool`` is an ``int`` subclass in Python and is rejected explicitly --
    ``True`` would otherwise sail through as the number 1.
    """
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{field} must be an integer, got {value!r}")
    number = int(value)
    if not config.MIN_NUMBER <= number <= config.MAX_NUMBER:
        raise ValueError(
            f"{field} must be between {config.MIN_NUMBER} and "
            f"{config.MAX_NUMBER}, got {number}"
        )
    return number


def validate_optional_number(value: Any, field: str = "number") -> int | None:
    """Like :func:`validate_number`, but ``None`` is allowed through.

    Intro and survey number questions are optional; a respondent who skips one
    still contributes their comparisons.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return validate_number(value, field)


def validate_comparison(left: Any, right: Any, winner: Any) -> tuple[int, int, int, int]:
    """Validate one comparison and derive the loser.

    Returns ``(left, right, winner, loser)``. The loser is never passed in --
    deriving it here makes a winner/loser column swap impossible, which is the
    first thing to suspect if 10 and 50 ever rank badly.
    """
    left_number = validate_number(left, "left_number")
    right_number = validate_number(right, "right_number")
    if left_number == right_number:
        raise ValueError(f"a pair must be two different numbers, got {left_number} twice")

    winner_number = validate_number(winner, "winner_number")
    if winner_number not in (left_number, right_number):
        raise ValueError(
            f"winner_number {winner_number} was not one of the numbers presented "
            f"({left_number}, {right_number})"
        )
    loser_number = right_number if winner_number == left_number else left_number
    return left_number, right_number, winner_number, loser_number


def validate_respondent_id(value: Any) -> str:
    """Return ``value`` as a canonical UUID string, or raise."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"respondent_id must be a UUID, got {value!r}") from exc


def validate_count(value: Any, field: str) -> int:
    """Validate a non-negative counter such as a page number or a duration."""
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{field} must be an integer, got {value!r}")
    number = int(value)
    if number < 0:
        raise ValueError(f"{field} must not be negative, got {number}")
    return number


def clip_free_text(value: Any) -> str | None:
    """Trim free text to the column width. Rendering stays the caller's job.

    Survey free text is length-capped here and must be rendered as text, never
    as markdown or HTML -- the results page is shown to other respondents.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[: config.MAX_FREE_TEXT_CHARS]


# --- Writes ------------------------------------------------------------------

_INSERT_RESPONDENT = f"""
INSERT INTO {_RESPONDENTS} (RESPONDENT_ID, APP_SOURCE, EXPERIMENT_VARIANT)
VALUES (?, ?, ?)
"""

_INSERT_INTRO_RESPONSE = f"""
INSERT INTO {_INTRO_RESPONSES} (RESPONDENT_ID, RANDOM_NUMBER, UNIQUE_GUESS_NUMBER)
VALUES (?, ?, ?)
"""

_INSERT_COMPARISON = f"""
INSERT INTO {_COMPARISONS} (
    COMPARISON_ID, RESPONDENT_ID, LEFT_NUMBER, RIGHT_NUMBER,
    WINNER_NUMBER, LOSER_NUMBER, PAGE_NUMBER, POSITION_IN_PAGE, RESPONSE_MS
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_SURVEY_RESPONSE = f"""
INSERT INTO {_SURVEY_RESPONSES} (
    RESPONDENT_ID, ROUNDNESS_DEFINITION, INTUITION_FACTORS,
    LEAST_ROUND_NUMBER, POST_RANDOM_NUMBER
)
VALUES (?, ?, ?, ?, ?)
"""


def insert_respondent(
    respondent_id: str, experiment_variant: str = config.DEFAULT_EXPERIMENT_VARIANT
) -> None:
    """Open a session's row. Written on intro submit, not on page load."""
    rid = validate_respondent_id(respondent_id)
    execute_many(_INSERT_RESPONDENT, [(rid, app_source(), str(experiment_variant))])


def insert_intro_response(
    respondent_id: str, random_number: Any, unique_guess_number: Any
) -> None:
    """Record the two intro number questions.

    Called together with :func:`insert_respondent` on intro submit, so a
    respondent who bails mid-comparisons still contributes usable data.
    """
    rid = validate_respondent_id(respondent_id)
    execute_many(
        _INSERT_INTRO_RESPONSE,
        [
            (
                rid,
                validate_optional_number(random_number, "random_number"),
                validate_optional_number(unique_guess_number, "unique_guess_number"),
            )
        ],
    )


def insert_comparisons(
    respondent_id: str, page_number: int, answers: Sequence[Mapping[str, Any]]
) -> int:
    """Write one submitted page of comparisons as a single statement.

    Each mapping in ``answers`` needs ``left_number``, ``right_number`` and
    ``winner_number``; ``response_ms`` is optional and ``position_in_page``
    defaults to the answer's index. Comparison ids are minted here.

    One batched write per submitted page, never one per click: the warehouse is
    XS with a 60s auto-suspend.
    """
    rid = validate_respondent_id(respondent_id)
    page = validate_count(page_number, "page_number")

    rows: list[tuple[Any, ...]] = []
    for index, answer in enumerate(answers):
        left, right, winner, loser = validate_comparison(
            answer.get("left_number"), answer.get("right_number"), answer.get("winner_number")
        )
        position = answer.get("position_in_page", index)
        response_ms = answer.get("response_ms")
        rows.append(
            (
                str(uuid.uuid4()),
                rid,
                left,
                right,
                winner,
                loser,
                page,
                validate_count(position, "position_in_page"),
                None if response_ms is None else validate_count(response_ms, "response_ms"),
            )
        )

    return execute_many(_INSERT_COMPARISON, rows)


def insert_survey_response(
    respondent_id: str,
    roundness_definition: Any,
    intuition_factors: Any,
    least_round_number: Any,
    post_random_number: Any,
) -> None:
    """Record the closing survey. Submitting this is what unlocks results."""
    rid = validate_respondent_id(respondent_id)
    execute_many(
        _INSERT_SURVEY_RESPONSE,
        [
            (
                rid,
                clip_free_text(roundness_definition),
                clip_free_text(intuition_factors),
                validate_optional_number(least_round_number, "least_round_number"),
                validate_optional_number(post_random_number, "post_random_number"),
            )
        ],
    )


# --- Reads -------------------------------------------------------------------
# Every read below targets a view or NUMBERS, never a write table. That is what
# lets the service role hold INSERT-only privileges on the fact tables while
# the results page still works.


@st.cache_data(ttl=_NUMBERS_CACHE_TTL_SECONDS, show_spinner=False)
def fetch_numbers() -> pd.DataFrame:
    """The NUMBERS reference table: 1-100 with one feature per hypothesis."""
    return query_df(
        f"""
        SELECT NUMBER_VALUE, MIN_COIN_COUNT, DIVISOR_COUNT, IS_PRIME,
               IS_PERFECT_SQUARE, ASPECT_RATIO, DIST_TO_MULT_10, DIST_TO_MULT_25,
               DIST_TO_MULT_50, GRAVITY_SCORE, ROMAN_NUMERAL, ROMAN_LENGTH,
               TRAILING_ZEROS, IS_MULT_5
        FROM {_NUMBERS}
        ORDER BY NUMBER_VALUE
        """
    )


@st.cache_data(ttl=config.PAIR_COUNTS_CACHE_TTL_SECONDS, show_spinner=False)
def fetch_pair_counts() -> pd.DataFrame:
    """All 4,950 pairs with their comparison counts, zero-count ones included.

    Fetched whole and cached rather than queried per page -- it is small, and
    it drives both pair sampling and the coverage heatmap. Sampling keeps a
    local overlay in session_state so a single long session keeps spreading
    across cold pairs between cache refreshes.
    """
    return query_df(
        f"""
        SELECT PAIR_LOW, PAIR_HIGH, N_COMPARISONS, LOW_WINS, HIGH_WINS
        FROM {_V_PAIR_COUNTS}
        """
    )


@st.cache_data(ttl=config.PAIR_COUNTS_CACHE_TTL_SECONDS, show_spinner=False)
def fetch_number_stats() -> pd.DataFrame:
    """Per-number comparison, win and loss counts with raw win%.

    The sanity-check metric shown beside Bradley-Terry, not the headline.
    """
    return query_df(
        f"""
        SELECT NUMBER_VALUE, N_COMPARISONS, N_WINS, N_LOSSES, WIN_PCT
        FROM {_V_NUMBER_STATS}
        ORDER BY NUMBER_VALUE
        """
    )


@st.cache_data(ttl=config.PAIR_COUNTS_CACHE_TTL_SECONDS, show_spinner=False)
def fetch_comparisons_for_scoring() -> pd.DataFrame:
    """Every comparison outcome, joined to its respondent's weight.

    This is the input to the Bradley-Terry fit. WEIGHT lets the same routine
    produce the respondent-weighted variant without a second query.
    """
    return query_df(
        f"""
        SELECT c.RESPONDENT_ID, c.WINNER_NUMBER, c.LOSER_NUMBER, w.WEIGHT
        FROM {_V_COMPARISONS_ENRICHED} c
        JOIN {_V_RESPONDENT_WEIGHTS} w
          ON w.RESPONDENT_ID = c.RESPONDENT_ID
        """
    )


@st.cache_data(ttl=config.PAIR_COUNTS_CACHE_TTL_SECONDS, show_spinner=False)
def fetch_respondent_weights() -> pd.DataFrame:
    """Per-respondent comparison count and weight (1.0 / count)."""
    return query_df(
        f"""
        SELECT RESPONDENT_ID, N_COMPARISONS, WEIGHT
        FROM {_V_RESPONDENT_WEIGHTS}
        ORDER BY N_COMPARISONS DESC
        """
    )


@st.cache_data(ttl=config.PAIR_COUNTS_CACHE_TTL_SECONDS, show_spinner=False)
def fetch_collection_totals() -> pd.DataFrame:
    """One row for the confidence footer: volume and pair coverage so far."""
    return query_df(
        f"""
        SELECT N_COMPARISONS, N_RESPONDENTS, N_RESPONDENTS_COMPARING,
               N_SURVEYS_SUBMITTED, N_PAIRS_COVERED, N_PAIRS_TOTAL
        FROM {_V_COLLECTION_TOTALS}
        """
    )


@st.cache_data(ttl=config.PAIR_COUNTS_CACHE_TTL_SECONDS, show_spinner=False)
def fetch_position_bias() -> pd.DataFrame:
    """Left-side win rate overall and by page position.

    LEFT_WIN_PCT should hover near 0.5. A strong skew means position bias is
    contaminating the results, which is a real threat to this instrument.
    """
    return query_df(
        f"""
        SELECT
            POSITION_IN_PAGE,
            COUNT(*)                        AS N_COMPARISONS,
            COUNT_IF(LEFT_WON)              AS N_LEFT_WINS,
            COUNT_IF(LEFT_WON) / COUNT(*)   AS LEFT_WIN_PCT,
            MEDIAN(RESPONSE_MS)             AS MEDIAN_RESPONSE_MS
        FROM {_V_COMPARISONS_ENRICHED}
        GROUP BY POSITION_IN_PAGE
        ORDER BY POSITION_IN_PAGE
        """
    )


@st.cache_data(ttl=config.PAIR_COUNTS_CACHE_TTL_SECONDS, show_spinner=False)
def fetch_random_number_picks() -> pd.DataFrame:
    """The intro and closing number questions, one row per respondent.

    Feeds the random-number analysis: are "random" picks drawn to round
    numbers, and did anyone's pick shift after taking the test?
    """
    return query_df(
        f"""
        SELECT RESPONDENT_ID, RANDOM_NUMBER, UNIQUE_GUESS_NUMBER,
               POST_RANDOM_NUMBER, LEAST_ROUND_NUMBER
        FROM {_V_RANDOM_NUMBER_PICKS}
        """
    )


@st.cache_data(ttl=config.PAIR_COUNTS_CACHE_TTL_SECONDS, show_spinner=False)
def fetch_survey_classification_counts() -> pd.DataFrame:
    """Counts per AI_CLASSIFY category. Empty until the batch has been run.

    The app never calls Cortex itself; ``sql/06_ai_classify_survey.sql`` fills
    SURVEY_CLASSIFICATIONS out-of-band. An empty frame here means the results
    page hides the panel.
    """
    return query_df(
        f"""
        SELECT CLASSIFICATION, N_RESPONDENTS
        FROM {_V_SURVEY_CLASSIFICATION_COUNTS}
        ORDER BY N_RESPONDENTS DESC
        """
    )


@st.cache_data(ttl=config.PAIR_COUNTS_CACHE_TTL_SECONDS, show_spinner=False)
def fetch_survey_free_text() -> pd.DataFrame:
    """Survey free text for the results page. Render as text, never markdown."""
    return query_df(
        f"""
        SELECT RESPONDENT_ID, ROUNDNESS_DEFINITION, INTUITION_FACTORS, SUBMITTED_AT
        FROM {_V_SURVEY_FREE_TEXT}
        ORDER BY SUBMITTED_AT DESC
        """
    )


def clear_read_caches() -> None:
    """Drop cached read results. Call after a write that a page must see."""
    for fetch in (
        fetch_pair_counts,
        fetch_number_stats,
        fetch_comparisons_for_scoring,
        fetch_respondent_weights,
        fetch_collection_totals,
        fetch_position_bias,
        fetch_random_number_picks,
        fetch_survey_classification_counts,
        fetch_survey_free_text,
    ):
        fetch.clear()
