"""The comparison page: ten pairs, two taps each, one write.

Faithful to [wireframe.png](wireframe.png) -- a "Which number is more round?"
header over a list of rows, each a large left number, a small "or", and a large
right number, separated by rules. The numbers themselves are the buttons.

The things in here that are experiment decisions rather than UI choices:

* **One batched write per submitted page, never one per click.** Clicks only
  move a dict in ``session_state``; the whole page goes to Snowflake in a
  single statement when the respondent submits it. The warehouse is XS with a
  60s auto-suspend, so every avoided round trip is both latency and credits.

* **Choices are recorded in ``on_click`` callbacks.** A callback runs before
  the rerun's script body, so the clicked card is already highlighted on the
  render that follows the click. Reading the button's return value instead
  would leave the highlight a rerun behind.

* **Pairs are sampled once and parked in ``session_state``.** Every click
  reruns the script; resampling here would reshuffle a page the respondent is
  half way through.

* **Which number goes left is a coin flip, and is recorded.** That is what
  makes left-side position bias measurable rather than baked in.

* **The gut-instinct framing is stated once, at the top.** Repeating it beside
  every row would turn a prompt into nagging, and a respondent who starts
  second-guessing is no longer producing the System 1 signal being measured.
"""

from __future__ import annotations

import streamlit as st

from app import config, queries, sampling, state
from app.db import DatabaseError, DbConfigError

#: Scoped to the pair list's container class (Streamlit renders a
#: ``st-key-<key>`` class for a keyed container, which needs Streamlit 1.39 --
#: pick 1.39 or later when creating the SiS app), so it cannot reach the submit
#: button or anything on another page. If a Streamlit upgrade renames that
#: class the cards render at default button size -- plain, but still usable.
PAGE_CSS = """
<style>
  div.st-key-pair-list button {
      width: 100%;
      height: 4.5rem;
  }
  /* Streamlit wraps a button label in a <p>, so the size has to go there. */
  div.st-key-pair-list button p {
      font-size: 2.1rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      line-height: 1;
  }
  div.st-key-pair-list p.or {
      text-align: center;
      font-size: 0.85rem;
      opacity: 0.55;
      margin: 0;
  }
</style>
"""


def render() -> None:
    if state.awaiting_continue():
        _render_continue()
        return

    if state.page_pairs() is None and not _sample_next_page():
        # Either the respondent has seen all 4,950 pairs -- in which case
        # _sample_next_page has already routed them to the survey -- or the
        # sampling read failed and has reported itself.
        return

    st.markdown(PAGE_CSS, unsafe_allow_html=True)
    _render_header()
    _render_pairs()
    _render_submit()


# --- Sampling ----------------------------------------------------------------


def _sample_next_page() -> bool:
    """Sample, order and park the next page. False if there is nothing to show.

    The cached ``V_PAIR_COUNTS`` numbers are merged with this session's local
    overlay, so a long session keeps spreading across cold pairs instead of
    circling the same ones until the 60s cache expires.
    """
    try:
        pair_counts = sampling.pair_counts_from_frame(queries.fetch_pair_counts())
    except DbConfigError:
        raise  # the router names the fix for a missing secrets block
    except DatabaseError as exc:
        st.error(
            "Couldn't load the next set of numbers. "
            "Reload the page to try again — nothing you've answered is lost."
        )
        st.caption(str(exc))
        return False

    merged = sampling.merge_overlay(pair_counts, state.pair_count_overlay())
    picked = sampling.select_pairs(
        merged,
        config.PAIRS_PER_PAGE,
        exclude_pairs=state.seen_pairs(),
        rng=state.rng(),
    )
    if not picked:
        # All 4,950 pairs seen this session. Straight to the survey.
        state.go_to(state.STAGE_SURVEY)
        return False

    # Noted as served rather than as submitted: a pair shown on a page the
    # respondent abandons should not come round again in the same session.
    state.record_served_pairs(picked)
    state.start_page(sampling.randomize_display_order(picked, state.rng()))
    return True


# --- Rendering ---------------------------------------------------------------


def _render_header() -> None:
    st.markdown("# Which number is more round?")
    st.caption(
        "Go with your gut — first reaction, not a considered one. "
        "There are no wrong answers."
    )


def _render_pairs() -> None:
    pairs = state.page_pairs() or []
    answers = state.page_answers()
    with st.container(key="pair-list"):
        for index, (left, right) in enumerate(pairs):
            chosen = answers.get(index, {}).get("winner")
            _render_row(index, left, right, chosen)
            if index < len(pairs) - 1:
                st.divider()


def _render_row(index: int, left: int, right: int, chosen: int | None) -> None:
    left_col, or_col, right_col = st.columns([1, 0.24, 1], vertical_alignment="center")
    for column, number in ((left_col, left), (right_col, right)):
        side = "left" if number == left else "right"
        with column:
            st.button(
                str(number),
                key=f"pair_{index}_{side}",
                on_click=state.record_answer,
                args=(index, number),
                type="primary" if chosen == number else "secondary",
            )
    or_col.markdown("<p class='or'>or</p>", unsafe_allow_html=True)


def _render_submit() -> None:
    pairs = state.page_pairs() or []
    answered = len(state.page_answers())
    complete = state.page_is_complete()

    st.write("")
    if not complete:
        st.caption(f"{answered} of {len(pairs)} answered.")
    if st.button(
        "Submit this page",
        key="submit_page",
        type="primary",
        disabled=not complete,
        help=None if complete else "Pick one from every row first.",
    ):
        _submit_page()


def _render_continue() -> None:
    """Between pages: thanks, the running count, and the two ways out."""
    total = state.comparisons_submitted()

    if total >= config.THANK_YOU_THRESHOLD:
        st.success(f"You've done plenty — {total} comparisons. Thank you!")
        st.caption("You're very welcome to keep going if you're enjoying it.")
    else:
        st.markdown("### Saved.")
        st.caption(f"{total} comparisons so far.")

    another, done = st.columns(2)
    if another.button("Another page", key="another_page", type="primary"):
        state.set_awaiting_continue(False)
        st.rerun()
    if done.button("I'm done", key="done_comparing"):
        state.set_awaiting_continue(False)
        state.go_to(state.STAGE_SURVEY)


# --- The write ---------------------------------------------------------------


def _submit_page() -> None:
    """Write the page as one statement, then ask whether to keep going.

    On failure the page is left exactly as it is: the answers stay in
    ``session_state`` so the respondent can hit submit again without redoing
    the row-by-row work.
    """
    pairs = state.page_pairs() or []
    answers = state.page_answers()
    rows = [
        {
            "left_number": left,
            "right_number": right,
            "winner_number": answers[index]["winner"],
            "response_ms": answers[index]["response_ms"],
            "position_in_page": index,
        }
        for index, (left, right) in enumerate(pairs)
        if index in answers
    ]
    if not rows:
        return

    try:
        written = queries.insert_comparisons(
            state.respondent_id(), state.page_number(), rows
        )
    except DbConfigError:
        raise
    except DatabaseError as exc:
        st.error(
            "Couldn't save this page — the database didn't accept the write. "
            "Try the button again in a moment; your answers are still here."
        )
        st.caption(str(exc))
        return
    except ValueError as exc:
        # Server-side validation in app.queries rejected a row. Reachable only
        # from a bug here or a tampered request, since every winner comes from
        # a button labelled with one of the two numbers presented.
        st.error(f"Something was wrong with this page: {exc}")
        return

    state.record_submitted_page(written)
    state.clear_page()
    state.set_awaiting_continue(True)
    st.rerun()
