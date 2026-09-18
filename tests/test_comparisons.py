"""The comparison page, driven through Streamlit's headless ``AppTest``.

Only two things are stubbed: the ``V_PAIR_COUNTS`` read and ``execute_many``.
Everything between them is the real thing -- the real sampler, the real page,
and crucially the real :func:`app.queries.insert_comparisons`, so the
winner/loser derivation and the one-statement-per-page batching are what is
actually being asserted rather than a test double's idea of them.

What these guard:

* The page writes **once**, on submit, with one row per pair -- not once per
  click. A regression to per-click writes would wake the XS warehouse ten times
  a page.
* ``left_number``/``right_number`` stay the order displayed while
  ``winner_number``/``loser_number`` follow the click, which is what makes
  left-side position bias measurable.
* A page cannot be submitted half-answered, and a failed write loses nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from app import config, queries, sampling  # noqa: E402
from app.db import DatabaseError  # noqa: E402

APP = str(ROOT / "streamlit_app.py")


@pytest.fixture
def written() -> list[tuple]:
    """Rows handed to ``execute_many``, in statement-sized batches."""
    return []


@pytest.fixture(autouse=True)
def stub_db(monkeypatch: pytest.MonkeyPatch, written: list[tuple]) -> None:
    """A fresh, empty experiment: every pair at zero comparisons, writes captured."""
    pairs = sampling.all_pairs()
    counts = pd.DataFrame(
        {
            "PAIR_LOW": [low for low, _ in pairs],
            "PAIR_HIGH": [high for _, high in pairs],
            "N_COMPARISONS": [0] * len(pairs),
            "LOW_WINS": [0] * len(pairs),
            "HIGH_WINS": [0] * len(pairs),
        }
    )
    monkeypatch.setattr(queries, "fetch_pair_counts", lambda: counts)

    def capture(sql: str, rows) -> int:
        written.append((sql, list(rows)))
        return len(rows)

    monkeypatch.setattr(queries, "execute_many", capture)


def _on_comparisons(**session_state: object) -> AppTest:
    """A session that has cleared the intro gate, sitting on the comparisons."""
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["stage"] = "comparisons"
    at.session_state["intro_submitted"] = True
    for key, value in session_state.items():
        at.session_state[key] = value
    return at.run()


def _answer_all(at: AppTest, side: str = "left") -> AppTest:
    """Click the same side of every row on the current page."""
    for index in range(len(at.session_state["page_pairs"])):
        at = at.button(key=f"pair_{index}_{side}").click().run()
    return at


def _submit(at: AppTest) -> AppTest:
    """Hit submit, then follow the ``st.rerun`` the successful write triggers.

    ``AppTest`` stops at the script run the click caused, so the extra ``run``
    is what a browser would do next.
    """
    return at.button(key="submit_page").click().run().run()


def _comparison_rows(written: list[tuple]) -> list[tuple]:
    return [row for _sql, rows in written for row in rows]


# --- The page ----------------------------------------------------------------


def test_a_page_of_ten_pairs_is_dealt_on_arrival() -> None:
    at = _on_comparisons()

    assert not at.exception
    pairs = at.session_state["page_pairs"]
    assert len(pairs) == config.PAIRS_PER_PAGE
    # Two cards per row plus the submit button.
    assert len(at.button) == 2 * config.PAIRS_PER_PAGE + 1
    assert {str(n) for pair in pairs for n in pair} <= {
        b.label for b in at.button
    }


def test_the_dealt_pairs_are_distinct_and_recorded_as_served() -> None:
    at = _on_comparisons()

    pairs = at.session_state["page_pairs"]
    canonical = {sampling.canonical(*pair) for pair in pairs}
    assert len(canonical) == config.PAIRS_PER_PAGE
    # Served, not submitted: an abandoned page must not come round again, and
    # the coverage weighting should already treat these as spoken for.
    assert at.session_state["seen_pairs"] == canonical
    assert all(at.session_state["pair_count_overlay"][p] == 1 for p in canonical)


def test_a_rerun_does_not_reshuffle_a_part_answered_page() -> None:
    at = _on_comparisons()
    pairs = list(at.session_state["page_pairs"])

    at = at.button(key="pair_0_left").click().run()

    assert at.session_state["page_pairs"] == pairs


# --- Clicks ------------------------------------------------------------------


def test_a_click_records_a_choice_and_writes_nothing(written: list[tuple]) -> None:
    at = _on_comparisons()
    left, _right = at.session_state["page_pairs"][3]

    at = at.button(key="pair_3_left").click().run()

    assert at.session_state["page_answers"][3]["winner"] == left
    # One write per page, not one per click.
    assert written == []


def test_re_answering_a_row_replaces_the_earlier_choice(written: list[tuple]) -> None:
    at = _on_comparisons()
    _left, right = at.session_state["page_pairs"][0]

    at = at.button(key="pair_0_left").click().run()
    at = at.button(key="pair_0_right").click().run()

    assert at.session_state["page_answers"][0]["winner"] == right
    assert len(at.session_state["page_answers"]) == 1


# --- Submitting --------------------------------------------------------------


def test_a_half_answered_page_cannot_be_submitted(written: list[tuple]) -> None:
    at = _on_comparisons()
    at = at.button(key="pair_0_left").click().run()

    assert at.button(key="submit_page").disabled
    assert any("1 of 10 answered" in c.value for c in at.caption)
    assert written == []


def test_a_full_page_is_written_as_one_statement(written: list[tuple]) -> None:
    at = _on_comparisons()
    pairs = list(at.session_state["page_pairs"])

    at = _answer_all(at)
    assert not at.button(key="submit_page").disabled
    at = _submit(at)

    assert not at.exception
    assert len(written) == 1  # one statement for the whole page
    rows = _comparison_rows(written)
    assert len(rows) == config.PAIRS_PER_PAGE

    # COMPARISON_ID, RESPONDENT_ID, LEFT, RIGHT, WINNER, LOSER, PAGE, POSITION, MS
    respondent_id = at.session_state["respondent_id"]
    for position, (row, (left, right)) in enumerate(zip(rows, pairs)):
        assert row[1] == respondent_id
        assert (row[2], row[3]) == (left, right)
        # Clicked the left card of every row: display order is preserved
        # independently of the outcome.
        assert row[4] == left
        assert row[5] == right
        assert row[6] == 1  # page_number
        assert row[7] == position
    assert len({row[0] for row in rows}) == config.PAIRS_PER_PAGE  # distinct ids


def test_a_right_side_click_is_recorded_as_a_right_side_win(
    written: list[tuple],
) -> None:
    at = _on_comparisons()
    pairs = list(at.session_state["page_pairs"])

    at = _answer_all(at, side="right")
    at = _submit(at)

    for row, (left, right) in zip(_comparison_rows(written), pairs):
        assert (row[2], row[3]) == (left, right)
        assert row[4] == right
        assert row[5] == left


def test_submitting_advances_the_page_and_offers_the_way_out() -> None:
    at = _on_comparisons()
    first_page = {sampling.canonical(*p) for p in at.session_state["page_pairs"]}

    at = _answer_all(at)
    at = _submit(at)

    assert at.session_state["comparisons_submitted"] == config.PAIRS_PER_PAGE
    assert at.session_state["page_number"] == 2
    assert at.session_state["awaiting_continue"] is True
    assert {b.label for b in at.button} == {"Another page", "I'm done"}

    at = at.button(key="another_page").click().run().run()

    second_page = {sampling.canonical(*p) for p in at.session_state["page_pairs"]}
    assert not first_page & second_page  # no pair twice in one session


def test_being_done_goes_to_the_survey() -> None:
    at = _on_comparisons()
    at = _answer_all(at)
    at = _submit(at)

    at = at.button(key="done_comparing").click().run()

    assert at.session_state["stage"] == "survey"
    assert at.session_state["awaiting_continue"] is False


def test_past_the_threshold_the_nudge_thanks_them_but_lets_them_continue() -> None:
    at = _on_comparisons(
        comparisons_submitted=config.THANK_YOU_THRESHOLD - config.PAIRS_PER_PAGE
    )
    at = _answer_all(at)
    at = _submit(at)

    assert any("done plenty" in s.value for s in at.success)
    # A nudge, not a wall.
    assert "Another page" in {b.label for b in at.button}


# --- Failure modes -----------------------------------------------------------


def test_a_failed_write_keeps_the_answers_and_stays_on_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _on_comparisons()
    pairs = list(at.session_state["page_pairs"])
    at = _answer_all(at)

    monkeypatch.setattr(
        queries,
        "execute_many",
        lambda *_a, **_k: (_ for _ in ()).throw(DatabaseError("simulated failure")),
    )
    at = at.button(key="submit_page").click().run()

    assert not at.exception
    assert [e.value for e in at.error]
    assert at.session_state["awaiting_continue"] is False
    assert at.session_state["comparisons_submitted"] == 0
    assert at.session_state["page_number"] == 1
    # Nothing to redo: same pairs, answers intact.
    assert at.session_state["page_pairs"] == pairs
    assert len(at.session_state["page_answers"]) == config.PAIRS_PER_PAGE


def test_a_failed_pair_counts_read_reports_itself_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        queries,
        "fetch_pair_counts",
        lambda: (_ for _ in ()).throw(DatabaseError("simulated read failure")),
    )
    at = _on_comparisons()

    assert not at.exception
    assert [e.value for e in at.error]
    assert at.session_state["page_pairs"] is None


def test_exhausting_every_pair_routes_straight_to_the_survey() -> None:
    at = _on_comparisons(seen_pairs=set(sampling.all_pairs()))

    assert not at.exception
    assert at.session_state["stage"] == "survey"


def test_response_times_are_captured_and_plausible(written: list[tuple]) -> None:
    at = _on_comparisons()
    at = _answer_all(at)
    at = _submit(at)

    response_ms = [row[8] for row in _comparison_rows(written)]
    # The first row on a page is timed from when the page opened, so every row
    # gets a number; none of them should be absurd.
    assert all(ms is not None and 0 <= ms <= config.MAX_RESPONSE_MS for ms in response_ms)
