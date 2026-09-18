"""Entry point: page config, global CSS, and the stage router.

The app is a linear flow -- ``intro -> comparisons -> survey -> results`` --
routed off ``st.session_state.stage`` rather than ``st.navigation``. Every rerun
passes the requested stage through :func:`app.state.resolve_stage`, so the
results gate is enforced in one place no matter how the stage got set.

Runs unchanged on Streamlit Community Cloud and in Streamlit in Snowflake. The
only thing that differs between the two is which backend ``app.db`` detects, and
nothing in this file knows or cares which it is.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import streamlit as st

# SiS runs the entry point from the stage directory rather than the repo root,
# so the project root is not always already on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import state  # noqa: E402
from app.db import DbConfigError  # noqa: E402

PAGE_TITLE = "What makes a number round?"

#: Stage -> the module whose ``render()`` draws it.
PAGE_MODULES: dict[str, str] = {
    state.STAGE_INTRO: "app.pages.intro",
    state.STAGE_COMPARISONS: "app.pages.comparisons",
    state.STAGE_SURVEY: "app.pages.survey",
    state.STAGE_RESULTS: "app.pages.results",
}

# Deliberately small, and limited to `block-container` (a long-stable Streamlit
# class) plus classes defined here. Anything that has to target Streamlit's
# generated markup more deeply belongs in the page that needs it, where a
# Streamlit upgrade breaking it is easier to spot.
GLOBAL_CSS = """
<style>
  div.block-container {
      max-width: 46rem;
      padding-top: 3rem;
      padding-bottom: 5rem;
  }
  /* Body copy a touch larger: most of this app is a question being read. */
  div.block-container p, div.block-container li {
      font-size: 1.05rem;
      line-height: 1.6;
  }
  div.block-container h1 {
      font-weight: 700;
      letter-spacing: -0.02em;
  }
</style>
"""


def main() -> None:
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon="⭕",
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

    state.init()

    # The gate. Applied on every rerun, not just on transition, so a stage set
    # directly or surviving from an earlier state still has to qualify.
    stage = state.resolve_stage(
        state.stage(),
        intro_submitted=state.intro_submitted(),
        results_unlocked=state.results_unlocked(),
    )
    if stage != state.stage():
        state.go_to(stage, rerun=False)

    try:
        _render(stage)
    except DbConfigError as exc:
        _render_config_error(exc)


def _render(stage: str) -> None:
    # Imported on demand rather than up front: a stage nobody visits costs
    # nothing, and the results page in particular pulls in Altair and the
    # analysis modules that a respondent working through the comparisons has
    # no use for. An ImportError from inside a page is a real bug and is left
    # to propagate.
    importlib.import_module(PAGE_MODULES[stage]).render()


def _render_config_error(exc: DbConfigError) -> None:
    """A missing or malformed ``[snowflake]`` secrets block.

    Only reachable on the external backend, and in practice only on a first
    local run or a fresh Community Cloud deploy, so it is worth naming the fix
    rather than showing a traceback.
    """
    st.error("This app can't reach Snowflake yet.")
    st.markdown(
        "Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` "
        "and fill it in for a local run, or paste the same block into "
        "Streamlit Community Cloud's secrets manager. "
        "`python scripts/smoke_test_connection.py` checks it end to end."
    )
    st.caption(str(exc))


main()
