"""The intro page: what the experiment is, and the two number questions.

Three decisions here are about data quality rather than presentation:

* **The description says nothing about the hypotheses.** Mentioning coins,
  factor counts, landmark numbers or Roman numerals would prime the respondent
  toward one of the four explanations the experiment is meant to adjudicate
  between. Priming is a *future* experiment with its own variant column; this
  one is the control, so the copy stays deliberately vague about mechanism.

* **The number inputs start empty, not at 50.** A default would anchor the
  "pick a random number" answer, and that answer is itself data -- the results
  page asks whether "random" picks are drawn toward round numbers. An empty
  input is the only honest starting state.

* **Both rows are written on submit, before any comparison.** A respondent who
  bails halfway through the comparisons still contributes their intro answers
  and shows up in the respondent count.
"""

from __future__ import annotations

import streamlit as st

from app import config, queries, state
from app.db import DatabaseError, DbConfigError


def render() -> None:
    if state.intro_submitted():
        # This session's intro rows are already written. Re-submitting would
        # add a second INTRO_RESPONSES row for one respondent, which
        # double-counts their picks in the random-number analysis --
        # V_RANDOM_NUMBER_PICKS joins intro to survey and would emit two rows.
        state.go_to(state.STAGE_COMPARISONS)

    st.markdown("# What makes a number *round*?")

    st.markdown(
        """
Some numbers just feel rounder than others. Most people agree 50 feels rounder
than 47, but nobody agrees on *why* — and it turns out you can't settle that by
arguing about it.

So this is the experiment instead. You'll be shown two numbers between 1 and
100 and asked which one feels rounder. There are no wrong answers, and the whole
point is your first reaction rather than a considered one. Ten pairs per page,
and you can stop after any page.

First, two quick questions for their own sake.
"""
    )

    with st.form("intro", clear_on_submit=False):
        random_number = st.number_input(
            "Pick a random number between 1 and 100.",
            min_value=config.MIN_NUMBER,
            max_value=config.MAX_NUMBER,
            # Empty rather than defaulted -- see the module docstring.
            value=None,
            step=1,
            format="%d",
            placeholder="1 to 100",
        )
        unique_guess_number = st.number_input(
            "Now pick a number between 1 and 100 that you think the fewest "
            "other people will choose.",
            min_value=config.MIN_NUMBER,
            max_value=config.MAX_NUMBER,
            value=None,
            step=1,
            format="%d",
            placeholder="1 to 100",
        )
        submitted = st.form_submit_button("Start comparing", type="primary")

    if not submitted:
        return

    if random_number is None or unique_guess_number is None:
        st.warning("Both questions need an answer before you start.")
        return

    _submit(int(random_number), int(unique_guess_number))


def _submit(random_number: int, unique_guess_number: int) -> None:
    """Write the two intro rows, then open the comparisons stage.

    The respondent row is guarded by a session flag because Snowflake does not
    enforce the primary key: if the intro insert fails and the respondent hits
    submit again, re-inserting the respondent row would leave two sessions'
    worth of them for one person.
    """
    respondent_id = state.respondent_id()
    try:
        if not state.respondent_row_written():
            queries.insert_respondent(respondent_id)
            state.mark_respondent_row_written()
        queries.insert_intro_response(
            respondent_id, random_number, unique_guess_number
        )
    except DbConfigError:
        # Not a write problem -- the secrets block is missing or malformed.
        # The router reports that one, with the fix.
        raise
    except DatabaseError as exc:
        st.error(
            "Couldn't save that — the database didn't accept the write. "
            "Try the button again in a moment."
        )
        st.caption(str(exc))
        return
    except ValueError as exc:
        # Server-side validation in app.queries rejected a number. Reachable
        # only via a tampered request, since the widgets clamp to 1-100.
        st.error(f"That didn't look like a valid answer: {exc}")
        return

    state.mark_intro_submitted()
    state.go_to(state.STAGE_COMPARISONS)
