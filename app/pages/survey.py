"""The closing survey, and the thing that unlocks the results.

Four questions, all optional, then the results open. The decisions worth
stating:

* **The survey comes after the comparisons, never before.** Asking someone to
  define roundness first would hand them a rule to apply, and the instrument is
  measuring the gut reaction they had before they had a rule.

* **Nothing is required.** A respondent who has already given ten or a hundred
  comparisons has done the valuable part; blocking their results over an empty
  text box would only buy a row of filler. Blanks are written as NULL and the
  analyses that use these columns skip them.

* **Submitting is what sets ``results_unlocked``.** Results are gated so that no
  respondent sees the current leaderboard before contributing -- the standings
  would anchor exactly the intuition being measured. The gate itself lives in
  :func:`app.state.resolve_stage` and is applied by the router; this page only
  sets the flag.

* **Free text is capped and never re-rendered as markdown.** ``app.queries``
  trims it to the column width on the way in, and the results page renders it
  as text: it is written by one anonymous respondent and shown to others.
"""

from __future__ import annotations

import streamlit as st

from app import config, queries, state
from app.db import DatabaseError, DbConfigError


def render() -> None:
    if state.results_unlocked():
        # Already submitted this session. A second SURVEY_RESPONSES row for one
        # respondent would double-count them in V_RANDOM_NUMBER_PICKS, which
        # joins intro to survey one row per respondent.
        state.go_to(state.STAGE_RESULTS)

    _render_header()

    with st.form("survey", clear_on_submit=False):
        roundness_definition = st.text_area(
            "In your own words, what does it mean for a number to be *round*?",
            max_chars=config.MAX_FREE_TEXT_CHARS,
            height=120,
            placeholder="However you'd explain it to someone.",
        )
        intuition_factors = st.text_area(
            "What do you think went into those gut reactions?",
            max_chars=config.MAX_FREE_TEXT_CHARS,
            height=120,
            placeholder="Anything you noticed yourself doing.",
        )
        least_round_number = st.number_input(
            "Which number between 1 and 100 is the *least* round?",
            min_value=config.MIN_NUMBER,
            max_value=config.MAX_NUMBER,
            # Empty rather than defaulted, for the same reason as the intro:
            # a prefilled value is an anchor, and this answer is itself data.
            value=None,
            step=1,
            format="%d",
            placeholder="1 to 100",
        )
        post_random_number = st.number_input(
            "One last time: pick a random number between 1 and 100.",
            min_value=config.MIN_NUMBER,
            max_value=config.MAX_NUMBER,
            value=None,
            step=1,
            format="%d",
            placeholder="1 to 100",
        )
        submitted = st.form_submit_button("Submit and see the results", type="primary")

    if submitted:
        _submit(
            roundness_definition,
            intuition_factors,
            least_round_number,
            post_random_number,
        )


def _render_header() -> None:
    st.markdown("# Almost done")
    total = state.comparisons_submitted()
    if total:
        st.caption(
            f"Thank you — {total} comparisons. Four last questions, all optional, "
            "and then the results."
        )
    else:
        st.caption("Four last questions, all optional, and then the results.")


def _submit(
    roundness_definition: str | None,
    intuition_factors: str | None,
    least_round_number: int | None,
    post_random_number: int | None,
) -> None:
    """Write the survey row, then open the results.

    On a write failure the results stay locked and the answers stay in the
    form: unlocking anyway would let a respondent through without their row
    landing, and the page is only worth seeing once the contribution is in.
    """
    try:
        queries.insert_survey_response(
            state.respondent_id(),
            roundness_definition,
            intuition_factors,
            None if least_round_number is None else int(least_round_number),
            None if post_random_number is None else int(post_random_number),
        )
    except DbConfigError:
        # Not a write problem -- the secrets block is missing or malformed.
        # The router reports that one, with the fix.
        raise
    except DatabaseError as exc:
        st.error(
            "Couldn't save that — the database didn't accept the write. "
            "Try the button again in a moment; your answers are still here."
        )
        st.caption(str(exc))
        return
    except ValueError as exc:
        # Server-side validation in app.queries rejected an answer. Reachable
        # only via a tampered request, since the widgets clamp to 1-100.
        st.error(f"That didn't look like a valid answer: {exc}")
        return

    state.unlock_results()
    state.go_to(state.STAGE_RESULTS)
