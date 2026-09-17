"""Checks on the generated NUMBERS features.

The seed SQL is generated once and then hand-run against Snowflake, so a wrong
feature here silently poisons the hypothesis comparison at the very end of the
project. These are cheap and catch that early.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import MAX_NUMBER, MIN_NUMBER  # noqa: E402
from scripts.generate_number_features import (  # noqa: E402
    build_rows,
    distance_to_multiple,
    gravity_score,
    min_coin_count,
    to_roman,
)


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (1, 1),    # penny
        (5, 1),    # nickel
        (15, 2),   # dime + nickel
        (16, 3),   # dime + nickel + penny
        (35, 2),   # quarter + dime
        (49, 7),   # 25 + 10 + 10 + 1 + 1 + 1 + 1
        (99, 9),   # 25*3 + 10*2 + 1*4
        (100, 1),  # dollar bill, not four quarters
    ],
)
def test_min_coin_count(n: int, expected: int) -> None:
    assert min_coin_count(n) == expected


def test_min_coin_count_matches_hypothesis_1_examples() -> None:
    # The writeup's claims: R($0.35) > R($0.49) and R(15) > R(16).
    assert min_coin_count(35) < min_coin_count(49)
    assert min_coin_count(15) < min_coin_count(16)


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1, "I"), (4, "IV"), (9, "IX"), (14, "XIV"), (40, "XL"),
     (49, "XLIX"), (50, "L"), (88, "LXXXVIII"), (90, "XC"), (100, "C")],
)
def test_to_roman(n: int, expected: str) -> None:
    assert to_roman(n) == expected


def test_distance_to_multiple() -> None:
    assert distance_to_multiple(50, 10) == 0
    assert distance_to_multiple(47, 10) == 3
    assert distance_to_multiple(96, 25) == 4  # nearest is 100
    assert distance_to_multiple(26, 50) == 24  # nearest is 50


def test_gravity_rises_toward_heavy_numbers() -> None:
    # Hypothesis 3: the heaviest numbers dominate, and the field strengthens
    # with the size of the landmark.
    assert gravity_score(100) > gravity_score(50) > gravity_score(10)
    assert gravity_score(96) < gravity_score(100)
    assert gravity_score(53) < gravity_score(50)


def test_every_number_seeded_exactly_once() -> None:
    rows = build_rows()
    values = [row["NUMBER_VALUE"] for row in rows]
    assert values == list(range(MIN_NUMBER, MAX_NUMBER + 1))


def test_feature_invariants() -> None:
    for row in build_rows():
        n = row["NUMBER_VALUE"]
        assert 0 < row["ASPECT_RATIO"] <= 1.0
        assert row["IS_PERFECT_SQUARE"] == (row["ASPECT_RATIO"] == 1.0)
        assert row["IS_PRIME"] == (row["DIVISOR_COUNT"] == 2)
        assert row["IS_MULT_5"] == (n % 5 == 0)
        assert row["ROMAN_LENGTH"] == len(row["ROMAN_NUMERAL"])
        assert row["MIN_COIN_COUNT"] >= 1
