"""Tests for the validation that stands between the UI and the fact table.

The app is internet-facing and the repo is public, so these checks are not
defensive style -- they are the reason a hostile or buggy caller cannot write a
row that quietly corrupts the experiment. Nothing here touches Snowflake.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.queries import (  # noqa: E402
    clip_free_text,
    validate_comparison,
    validate_count,
    validate_number,
    validate_optional_number,
    validate_respondent_id,
)


# --- validate_number ---------------------------------------------------------


@pytest.mark.parametrize("value", [1, 50, 100])
def test_numbers_in_range_are_accepted(value: int) -> None:
    assert validate_number(value) == value


@pytest.mark.parametrize("value", [0, -1, 101, 1000])
def test_numbers_out_of_range_are_rejected(value: int) -> None:
    with pytest.raises(ValueError, match="between"):
        validate_number(value)


@pytest.mark.parametrize("value", ["50", 50.5, None, [50], object()])
def test_non_integers_are_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="integer"):
        validate_number(value)


def test_booleans_are_rejected_despite_being_ints() -> None:
    # bool subclasses int in Python, so True would otherwise land as the
    # number 1 and look like a real answer.
    with pytest.raises(ValueError, match="integer"):
        validate_number(True)


def test_numpy_integers_are_accepted() -> None:
    # Values arriving from a DataFrame are numpy scalars, not Python ints.
    np = pytest.importorskip("numpy")
    assert validate_number(np.int64(42)) == 42
    assert isinstance(validate_number(np.int64(42)), int)


def test_range_follows_config() -> None:
    assert validate_number(config.MIN_NUMBER) == config.MIN_NUMBER
    assert validate_number(config.MAX_NUMBER) == config.MAX_NUMBER


# --- optional numbers --------------------------------------------------------


def test_optional_number_allows_none() -> None:
    # Intro and survey number questions may be skipped; the respondent's
    # comparisons still count.
    assert validate_optional_number(None) is None


def test_optional_number_allows_nan() -> None:
    assert validate_optional_number(float("nan")) is None


def test_optional_number_still_validates_a_value() -> None:
    assert validate_optional_number(73) == 73
    with pytest.raises(ValueError):
        validate_optional_number(0)


# --- validate_comparison -----------------------------------------------------


def test_loser_is_derived_not_supplied() -> None:
    assert validate_comparison(10, 37, 10) == (10, 37, 10, 37)
    assert validate_comparison(10, 37, 37) == (10, 37, 37, 10)


def test_winner_must_be_one_of_the_numbers_presented() -> None:
    with pytest.raises(ValueError, match="not one of the numbers presented"):
        validate_comparison(10, 37, 50)


def test_a_pair_must_be_two_different_numbers() -> None:
    with pytest.raises(ValueError, match="two different numbers"):
        validate_comparison(7, 7, 7)


def test_display_order_does_not_affect_the_outcome() -> None:
    # Display order is recorded separately so position bias is measurable, but
    # it must not change who won.
    _, _, winner_left, loser_left = validate_comparison(10, 37, 10)
    _, _, winner_right, loser_right = validate_comparison(37, 10, 10)
    assert (winner_left, loser_left) == (winner_right, loser_right) == (10, 37)


@pytest.mark.parametrize(
    ("left", "right", "winner"),
    [(0, 37, 37), (10, 101, 10), (10, 37, 0), (True, 37, 37)],
)
def test_comparison_validates_every_number(left: object, right: object, winner: object) -> None:
    with pytest.raises(ValueError):
        validate_comparison(left, right, winner)


# --- ids, counts, free text --------------------------------------------------


def test_respondent_id_must_be_a_uuid() -> None:
    generated = str(uuid.uuid4())
    assert validate_respondent_id(generated) == generated
    for bad in ("", "not-a-uuid", None, 12345, "'; DROP TABLE COMPARISONS; --"):
        with pytest.raises(ValueError, match="UUID"):
            validate_respondent_id(bad)


def test_respondent_id_is_canonicalized() -> None:
    raw = uuid.uuid4()
    assert validate_respondent_id(raw.hex.upper()) == str(raw)


def test_counts_reject_negatives_and_non_integers() -> None:
    assert validate_count(0, "page_number") == 0
    assert validate_count(9, "position_in_page") == 9
    with pytest.raises(ValueError, match="negative"):
        validate_count(-1, "page_number")
    with pytest.raises(ValueError, match="integer"):
        validate_count(1.5, "response_ms")


def test_free_text_is_capped_at_the_column_width() -> None:
    clipped = clip_free_text("x" * (config.MAX_FREE_TEXT_CHARS + 500))
    assert len(clipped) == config.MAX_FREE_TEXT_CHARS


def test_free_text_empties_become_null() -> None:
    assert clip_free_text(None) is None
    assert clip_free_text("   ") is None
    assert clip_free_text("  round numbers feel like coins  ") == "round numbers feel like coins"


def test_free_text_is_not_escaped_or_altered() -> None:
    # Escaping is not this layer's job: the value is bound as a parameter, and
    # the results page renders it as text rather than markdown.
    markup = "<b>ten</b> & 50% **round**"
    assert clip_free_text(markup) == markup
