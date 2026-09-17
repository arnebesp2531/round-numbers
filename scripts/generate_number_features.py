"""Compute the per-hypothesis features for 1-100 and emit sql/03_seed_numbers.sql.

Roman numerals and minimum-coin counts are miserable to express in SQL, so they
are computed here in Python and written out as literal INSERT rows: reviewable
in a diff, deterministic, and hand-runnable like the rest of the SQL.

    python scripts/generate_number_features.py

Re-run this whenever a feature definition changes, then re-run the emitted SQL.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.config import MAX_NUMBER, MIN_NUMBER  # noqa: E402

OUTPUT_PATH = REPO_ROOT / "sql" / "03_seed_numbers.sql"

# US denominations in cents, including the dollar bill, so 100 costs one unit
# rather than four quarters. Greedy is provably optimal for this set.
DENOMINATIONS = (100, 25, 10, 5, 1)

ROMAN_SYMBOLS = (
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)

# Hypothesis 3 says heavy numbers pull roundness out of their neighbours, and
# that the field strengthens with the size of the landmark. Masses below encode
# that ordering; the exact values are a modelling choice, not a measurement, so
# the raw DIST_TO_* columns are stored alongside for anyone who disagrees.
LANDMARK_MASSES: dict[int, float] = {}
for _n in range(MIN_NUMBER, MAX_NUMBER + 1):
    if _n == 100:
        LANDMARK_MASSES[_n] = 4.0
    elif _n == 50:
        LANDMARK_MASSES[_n] = 3.0
    elif _n % 10 == 0:
        LANDMARK_MASSES[_n] = 2.5
    elif _n % 25 == 0:  # 25 and 75
        LANDMARK_MASSES[_n] = 2.0
    elif _n % 5 == 0:
        LANDMARK_MASSES[_n] = 1.0


def min_coin_count(n: int) -> int:
    """Fewest US coins/bills making n cents. Hypothesis 1."""
    remaining, count = n, 0
    for denomination in DENOMINATIONS:
        count += remaining // denomination
        remaining %= denomination
    return count


def divisors(n: int) -> list[int]:
    return [d for d in range(1, n + 1) if n % d == 0]


def is_prime(n: int) -> bool:
    if n < 2:
        return False
    return all(n % d for d in range(2, int(math.isqrt(n)) + 1))


def aspect_ratio(n: int) -> float:
    """Ratio of the most-square factor pair (short side / long side).

    1.0 is a perfect square; a prime is 1/n, the most elongated rectangle
    available. Hypothesis 2 predicts roundness rises with this value.
    """
    best = max(d for d in divisors(n) if d * d <= n)
    return best / (n // best)


def distance_to_multiple(n: int, base: int) -> int:
    """Distance to the nearest multiple of `base`, counting 0 as a landmark
    only where it is not below the domain (so 1 is 1 away from 0, not 9 from 10)."""
    below = (n // base) * base
    above = below + base
    return min(n - below, above - n)


def gravity_score(n: int) -> float:
    """Total pull on n from every landmark, attenuating with squared distance.

    A landmark exerts its own full mass on itself (distance 0), so 100 scores
    highest and the numbers just below it inherit a share of that pull.
    """
    return sum(
        mass / (1 + abs(n - landmark)) ** 2
        for landmark, mass in LANDMARK_MASSES.items()
    )


def to_roman(n: int) -> str:
    out, remaining = [], n
    for value, symbol in ROMAN_SYMBOLS:
        whole, remaining = divmod(remaining, value)
        out.append(symbol * whole)
    return "".join(out)


def trailing_zeros(n: int) -> int:
    count = 0
    while n % 10 == 0:
        count += 1
        n //= 10
    return count


def feature_row(n: int) -> dict[str, object]:
    return {
        "NUMBER_VALUE": n,
        "MIN_COIN_COUNT": min_coin_count(n),
        "DIVISOR_COUNT": len(divisors(n)),
        "IS_PRIME": is_prime(n),
        "IS_PERFECT_SQUARE": math.isqrt(n) ** 2 == n,
        "ASPECT_RATIO": aspect_ratio(n),
        "DIST_TO_MULT_10": distance_to_multiple(n, 10),
        "DIST_TO_MULT_25": distance_to_multiple(n, 25),
        "DIST_TO_MULT_50": distance_to_multiple(n, 50),
        "GRAVITY_SCORE": gravity_score(n),
        "ROMAN_NUMERAL": to_roman(n),
        "ROMAN_LENGTH": len(to_roman(n)),
        "TRAILING_ZEROS": trailing_zeros(n),
        "IS_MULT_5": n % 5 == 0,
    }


def build_rows() -> list[dict[str, object]]:
    return [feature_row(n) for n in range(MIN_NUMBER, MAX_NUMBER + 1)]


def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def render_sql(rows: list[dict[str, object]]) -> str:
    columns = list(rows[0].keys())
    values = ",\n".join(
        "    (" + ", ".join(_literal(row[c]) for c in columns) + ")" for row in rows
    )
    return f"""-- 03_seed_numbers.sql
-- GENERATED FILE - do not edit by hand.
-- Regenerate with: python scripts/generate_number_features.py
--
-- Numbers {MIN_NUMBER}-{MAX_NUMBER} with one precomputed feature per hypothesis.
-- Idempotent: the DELETE makes a re-run replace the seed rather than duplicate it.

USE DATABASE ROUNDNESS_LAB;
USE SCHEMA APP;

DELETE FROM NUMBERS;

INSERT INTO NUMBERS (
    {", ".join(columns)}
) VALUES
{values};
"""


def main() -> None:
    rows = build_rows()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_sql(rows), encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
