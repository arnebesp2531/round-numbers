"""Coverage-weighted pair selection.

The instrument needs all 4,950 pairs covered, but a respondent only ever sees a
few dozen. Uniform sampling would leave a long tail of pairs at zero forever, so
each candidate pair is weighted by how rarely it has been compared:

    w = 1 / (n_comparisons + 1) ** alpha

With the default alpha of 2.0 a never-shown pair is 4x as likely as a pair seen
once and 9x as likely as one seen twice, which spreads coverage fast without
ever making a well-covered pair impossible.

Pure logic: no Streamlit, no Snowflake, no pandas import. `pair_counts_from_frame`
reads a DataFrame duck-typed by column name so this module stays unit-testable
without a connection.
"""

from __future__ import annotations

import math
import random
from typing import Iterable, Mapping, Sequence

from app.config import MAX_NUMBER, MIN_NUMBER, SAMPLING_ALPHA

Pair = tuple[int, int]

# Column names as V_PAIR_COUNTS returns them.
COL_PAIR_LOW = "PAIR_LOW"
COL_PAIR_HIGH = "PAIR_HIGH"
COL_N_COMPARISONS = "N_COMPARISONS"


def canonical(a: int, b: int) -> Pair:
    """Order a pair as (low, high) — the key form used everywhere else.

    Display order is a separate concern handled by `randomize_display_order`.
    """
    a, b = int(a), int(b)
    if a == b:
        raise ValueError(f"a pair needs two distinct numbers, got {a} twice")
    _validate_number(a)
    _validate_number(b)
    return (a, b) if a < b else (b, a)


def _validate_number(n: int) -> None:
    if not MIN_NUMBER <= n <= MAX_NUMBER:
        raise ValueError(f"number {n} outside {MIN_NUMBER}-{MAX_NUMBER}")


def all_pairs() -> list[Pair]:
    """Every unordered pair in the experiment domain, 4,950 of them."""
    return [
        (low, high)
        for low in range(MIN_NUMBER, MAX_NUMBER + 1)
        for high in range(low + 1, MAX_NUMBER + 1)
    ]


def pair_counts_from_frame(df) -> dict[Pair, int]:
    """Turn a V_PAIR_COUNTS DataFrame into a {(low, high): n_comparisons} map.

    Accessed by column name only, so this works for anything DataFrame-shaped
    and keeps pandas out of this module's imports.
    """
    return {
        canonical(low, high): int(n)
        for low, high, n in zip(
            df[COL_PAIR_LOW], df[COL_PAIR_HIGH], df[COL_N_COMPARISONS]
        )
    }


def merge_overlay(
    pair_counts: Mapping[Pair, int], overlay: Mapping[Pair, int]
) -> dict[Pair, int]:
    """Add a session's local counts on top of the cached view counts.

    V_PAIR_COUNTS is cached for a minute, so within one long session the cached
    numbers go stale and the same cold pairs keep winning the weighting. The
    caller keeps an overlay of pairs served this session; merging it here makes
    a long session keep spreading out instead of circling.
    """
    merged = dict(pair_counts)
    for pair, extra in overlay.items():
        key = canonical(*pair)
        merged[key] = merged.get(key, 0) + int(extra)
    return merged


def pair_weight(n_comparisons: int, alpha: float = SAMPLING_ALPHA) -> float:
    """Sampling weight for a pair seen `n_comparisons` times. Always positive."""
    if n_comparisons < 0:
        raise ValueError(f"n_comparisons must be >= 0, got {n_comparisons}")
    return 1.0 / (n_comparisons + 1.0) ** alpha


def select_pairs(
    pair_counts: Mapping[Pair, int],
    n: int,
    exclude_pairs: Iterable[Pair] = (),
    rng: random.Random | None = None,
    alpha: float = SAMPLING_ALPHA,
) -> list[Pair]:
    """Pick `n` distinct pairs, favouring the least-compared ones.

    `pair_counts` need not be complete — the candidate set is always the full
    domain and a missing pair counts as zero, so a pair absent from the view can
    still be sampled rather than being frozen out forever.

    Returns fewer than `n` pairs only when the respondent has nearly exhausted
    the domain; an empty list means they have seen all 4,950 and the caller
    should route straight to the survey.
    """
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    if rng is None:
        rng = random.Random()

    excluded = {canonical(*p) for p in exclude_pairs}
    candidates = [p for p in all_pairs() if p not in excluded]
    if n == 0 or not candidates:
        return []

    # Weighted sampling without replacement, Efraimidis-Spirakis: give each
    # candidate the key u ** (1 / w) and keep the largest n. Done in log space
    # (log(u) / w, still largest-first) because w spans several orders of
    # magnitude and the direct form loses precision at the top end.
    keyed = []
    for pair in candidates:
        weight = pair_weight(pair_counts.get(pair, 0), alpha)
        u = rng.random()
        # random() can return exactly 0.0; nudge it so log is defined.
        if u <= 0.0:
            u = 1e-18
        keyed.append((math.log(u) / weight, pair))

    keyed.sort(key=lambda item: item[0], reverse=True)
    return [pair for _, pair in keyed[:n]]


def randomize_display_order(
    pairs: Sequence[Pair], rng: random.Random | None = None
) -> list[Pair]:
    """Coin-flip which number of each pair renders on the left.

    Independent of sampling, and the reason COMPARISONS stores left/right apart
    from winner/loser: without randomizing here, left-side position bias would
    be baked in rather than measurable.
    """
    if rng is None:
        rng = random.Random()
    return [(high, low) if rng.random() < 0.5 else (low, high) for low, high in pairs]


def remaining_pair_count(exclude_pairs: Iterable[Pair]) -> int:
    """How many pairs a respondent has not yet seen."""
    return len(all_pairs()) - len({canonical(*p) for p in exclude_pairs})
