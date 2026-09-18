"""The results page: R(x), the hypothesis verdict, and how far to trust it.

Reached only with ``st.session_state.results_unlocked``, which the survey sets.
The router enforces that; this page assumes it.

Charting decisions, since this is the page where they matter:

* **Altair, not a plotting library.** It ships with Streamlit and is available
  in SiS without a package request, so the same code renders in both targets.
* **One hue per job.** Magnitude gets a single blue; the one two-series chart
  (random picks before and after) uses blue and orange, a pair validated for
  colour-vision deficiency against this app's light surface. No chart colours
  a bar by its own height, and no chart has two y-scales.
* **Every chart has a table underneath it.** The values are reachable without
  reading a colour or landing a hover, which also makes the numbers quotable.

Analysis decisions:

* **Bradley-Terry is the headline, win% is the sanity check.** Win% is biased
  by which opponents a number happened to face; beating 7 twenty times says
  less than beating 50 once. Both are offered, with the weighted variant that
  neutralises a heavy respondent.
* **The hypothesis panel follows the selected metric.** If the verdict flips
  when you switch metric, that is worth seeing rather than hiding behind a
  fixed headline number.
* **Nothing here is asserted without its sample size.** The footer carries the
  comparison count, the pair coverage and the median confidence interval, and
  the position-bias check is one expander away.
"""

from __future__ import annotations

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from analysis import bradley_terry, metrics
from app import config, queries
from app.db import DatabaseError, DbConfigError

# --- Palette -----------------------------------------------------------------
# From the reference data-viz palette, validated with its own checker against
# this app's light surface (#FBFAF7, see .streamlit/config.toml): the blue and
# orange pair clears every gate, worst-pair CVD delta-E 24.7. The app is
# light-mode only (`base = "light"`), so no dark steps are needed.
BLUE = "#2a78d6"
ORANGE = "#eb6834"
MUTED_INK = "#898781"
GRIDLINE = "#e1e0d9"
# Single-hue blue, light to dark, for continuous magnitude only.
SEQUENTIAL_BLUE = ["#cde2fb", "#9ec5f4", "#5598e7", "#256abf", "#0d366b"]

# Every tenth number, so a 100-category axis stays readable.
DECADE_TICKS = list(range(10, config.MAX_NUMBER + 1, 10))

# --- Metrics -----------------------------------------------------------------

METRIC_BT = "Bradley-Terry"
METRIC_WEIGHTED = "Per-person weighted"
METRIC_WIN_PCT = "Raw win %"

METRICS: dict[str, dict[str, str]] = {
    METRIC_BT: {
        "axis_title": "Roundness score (Elo scale)",
        "blurb": (
            "The headline. Scores every number on one scale regardless of which "
            "opponents it happened to face, with 95% bootstrap intervals."
        ),
    },
    METRIC_WEIGHTED: {
        "axis_title": "Roundness score (Elo scale)",
        "blurb": (
            "The same model with every respondent's comparisons weighted to sum "
            "to one, so nobody's personal definition of roundness outvotes the rest."
        ),
    },
    METRIC_WIN_PCT: {
        "axis_title": "Comparisons won (%)",
        "blurb": (
            "The sanity check, not the headline: it is biased by which opponents "
            "a number was drawn against."
        ),
    },
}


def render() -> None:
    st.markdown("# What makes a number round?")

    try:
        scores = _score_frame(METRIC_BT)
    except DbConfigError:
        raise  # the router names the fix for a missing secrets block
    except DatabaseError as exc:
        _render_read_failure(exc)
        return

    if scores.empty:
        _render_empty()
        return

    metric = _render_metric_picker()
    try:
        scores = scores if metric == METRIC_BT else _score_frame(metric)
    except DatabaseError as exc:
        _render_read_failure(exc)
        return

    _render_headline(scores)
    _render_score_chart(scores, metric)
    _render_hypotheses(scores, metric)
    _render_random_numbers(scores)
    _render_classifications()
    _render_coverage()
    _render_footer(scores)


# --- Scores ------------------------------------------------------------------


@st.cache_data(ttl=config.RESULTS_CACHE_TTL_SECONDS, show_spinner="Scoring…")
def _score_frame(metric: str) -> pd.DataFrame:
    """One row per number: ``number``, ``score``, ``ci_low``, ``ci_high``, ``comparisons``.

    Cached well past the read TTL: the bootstrap refits the model a couple of
    hundred times, and the standings do not visibly move minute to minute.
    ``ci_low``/``ci_high`` are NaN for win%, which has no model behind it.
    """
    if metric == METRIC_WIN_PCT:
        stats = queries.fetch_number_stats()
        if stats.empty or not stats["N_COMPARISONS"].sum():
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "number": stats["NUMBER_VALUE"].astype(int),
                # The view stores a fraction; the axis is a percentage.
                "score": stats["WIN_PCT"].astype(float) * 100.0,
                "ci_low": np.nan,
                "ci_high": np.nan,
                "comparisons": stats["N_COMPARISONS"].astype(int),
            }
        )

    comparisons = queries.fetch_comparisons_for_scoring()
    if comparisons.empty:
        return pd.DataFrame()

    weights = (
        comparisons["WEIGHT"].to_numpy(dtype=float)
        if metric == METRIC_WEIGHTED
        else None
    )
    result = bradley_terry.fit_with_bootstrap(
        comparisons["WINNER_NUMBER"].to_numpy(dtype=int),
        comparisons["LOSER_NUMBER"].to_numpy(dtype=int),
        weights=weights,
        # Passed explicitly rather than left to the default so the cost of the
        # bootstrap is tunable from one place, config, like every other size in
        # this project.
        samples=config.BT_BOOTSTRAP_SAMPLES,
    )
    # Observed comparison counts, not the weighted ones, because this column is
    # read as "how much evidence is behind this bar".
    observed = (
        pd.concat([comparisons["WINNER_NUMBER"], comparisons["LOSER_NUMBER"]])
        .value_counts()
        .reindex(result.numbers, fill_value=0)
    )
    return pd.DataFrame(
        {
            "number": result.numbers.astype(int),
            "score": result.elo,
            "ci_low": result.elo_ci_low,
            "ci_high": result.elo_ci_high,
            "comparisons": observed.to_numpy(dtype=int),
        }
    )


def _render_metric_picker() -> str:
    """One control row above everything it scopes -- chart, panel and analysis."""
    metric = st.radio(
        "Score",
        list(METRICS),
        horizontal=True,
        label_visibility="collapsed",
        key="results_metric",
    )
    st.caption(METRICS[metric]["blurb"])
    return str(metric)


# --- Headline ----------------------------------------------------------------


def _render_headline(scores: pd.DataFrame) -> None:
    scored = scores.dropna(subset=["score"])
    if scored.empty:
        return
    roundest = scored.loc[scored["score"].idxmax()]
    least = scored.loc[scored["score"].idxmin()]

    roundest_col, least_col = st.columns(2)
    roundest_col.metric("Roundest number", int(roundest["number"]))
    least_col.metric("Least round number", int(least["number"]))
    st.caption(
        f"From {int(scored['comparisons'].sum() // 2):,} comparisons so far. "
        "Both move as more arrive."
    )


# --- The R(x) chart ----------------------------------------------------------


def _render_score_chart(scores: pd.DataFrame, metric: str) -> None:
    st.markdown("### Roundness across 1–100")

    axis_title = METRICS[metric]["axis_title"]
    frame = scores.copy()
    frame["label"] = frame["number"].astype(str)
    has_intervals = frame["ci_low"].notna().any()

    base = alt.Chart(frame).encode(
        x=alt.X(
            "label:O",
            title=None,
            sort=list(frame["label"]),
            axis=alt.Axis(
                values=[str(n) for n in DECADE_TICKS],
                labelAngle=0,
                labelColor=MUTED_INK,
                domainColor=GRIDLINE,
                tickColor=GRIDLINE,
            ),
        ),
        tooltip=[
            alt.Tooltip("number:Q", title="Number"),
            alt.Tooltip("score:Q", title=axis_title, format=".1f"),
            alt.Tooltip("comparisons:Q", title="Comparisons", format=","),
        ],
    )

    bars = base.mark_bar(
        color=BLUE,
        # A 4px rounded data-end would swallow a bar this thin; 2px reads as
        # the same detail at this width.
        cornerRadiusTopLeft=2,
        cornerRadiusTopRight=2,
        cornerRadiusBottomLeft=2,
        cornerRadiusBottomRight=2,
    ).encode(
        y=alt.Y(
            "score:Q",
            title=axis_title,
            axis=alt.Axis(
                grid=True,
                gridColor=GRIDLINE,
                labelColor=MUTED_INK,
                titleColor=MUTED_INK,
                domain=False,
                tickColor=GRIDLINE,
            ),
        )
    )

    chart = bars
    if has_intervals:
        # Recessive hairlines over the bars: the interval is context for the
        # bar, not a second series competing with it.
        intervals = base.mark_rule(color=MUTED_INK, strokeWidth=1, opacity=0.8).encode(
            y=alt.Y("ci_low:Q", title=axis_title), y2=alt.Y2("ci_high:Q")
        )
        chart = bars + intervals

    st.altair_chart(_styled(chart.properties(height=320)), width="stretch")
    if has_intervals:
        st.caption(
            "Bars are the fitted score; the line through each is its 95% "
            "bootstrap interval. Wide intervals mean that number needs more "
            "comparisons, not that it is genuinely ambiguous."
        )

    _render_score_table(scores, axis_title)


def _render_score_table(scores: pd.DataFrame, axis_title: str) -> None:
    ranked = scores.sort_values("score", ascending=False, ignore_index=True)
    ranked.insert(0, "rank", ranked.index + 1)
    st.dataframe(
        ranked.rename(
            columns={
                "rank": "Rank",
                "number": "Number",
                "score": axis_title,
                "ci_low": "CI low",
                "ci_high": "CI high",
                "comparisons": "Comparisons",
            }
        ),
        hide_index=True,
        height=260,
    )


# --- Hypotheses --------------------------------------------------------------


def _render_hypotheses(scores: pd.DataFrame, metric: str) -> None:
    st.markdown("### Which explanation fits?")

    try:
        features = queries.fetch_numbers()
    except DatabaseError as exc:
        _render_read_failure(exc)
        return
    if features.empty:
        st.info("The NUMBERS reference table is empty — run `sql/03_seed_numbers.sql`.")
        return

    series = pd.Series(
        scores["score"].to_numpy(dtype=float), index=scores["number"].astype(int)
    )
    scoreboard = metrics.compare_hypotheses(series, features)
    winner = metrics.winning_hypothesis(scoreboard)
    if scoreboard.empty or winner is None:
        return

    contenders = scoreboard[~scoreboard["is_control"]].reset_index(drop=True)
    won = contenders[contenders["hypothesis"] == winner].iloc[0]
    st.markdown(
        f"On the **{metric.lower()}** scores, the best-fitting explanation is "
        f"**{won['hypothesis_label']}**, accounting for "
        f"{won['r_squared']:.0%} of the variation in roundness on its own "
        f"({won['unique_r_squared']:.0%} that no other hypothesis also explains)."
    )

    _render_hypothesis_chart(contenders, winner)
    _render_hypothesis_scatter(scores, features, contenders)

    control = scoreboard[scoreboard["is_control"]]
    if not control.empty:
        best_control = control.iloc[0]
        st.caption(
            "Control, for scale: the plain baseline features "
            f"(trailing zeros, multiples of 5) explain "
            f"{best_control['r_squared']:.0%} by themselves. A hypothesis that "
            "cannot beat that is not explaining much."
        )

    with st.expander("The full scoreboard"):
        st.dataframe(
            scoreboard.rename(
                columns={
                    "hypothesis_label": "Hypothesis",
                    "n_features": "Features",
                    "r_squared": "R² alone",
                    "adjusted_r_squared": "Adjusted R²",
                    "unique_r_squared": "R² unique to it",
                    "best_feature": "Strongest feature",
                    "best_feature_rho": "Its Spearman ρ",
                    "is_control": "Control",
                }
            ).drop(columns=["hypothesis"]),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "“R² alone” is what a hypothesis could explain if it were the only "
            "story; “R² unique to it” is what the full model loses without it. "
            "Correlated features — multiples of 5 are also short in Roman "
            "numerals — share credit in the first and lose it in the second."
        )


def _render_hypothesis_chart(contenders: pd.DataFrame, winner: str) -> None:
    """Horizontal bars, with the winner picked out and the rest receding.

    Emphasis rather than one hue per bar: the story is which bar is longest,
    and colouring four categories would spend the colour channel on labels the
    axis already carries.
    """
    frame = contenders.assign(is_winner=contenders["hypothesis"] == winner)
    bars = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, height=18)
        .encode(
            x=alt.X(
                "r_squared:Q",
                title="Share of roundness explained (R²)",
                axis=alt.Axis(
                    format="%",
                    grid=True,
                    gridColor=GRIDLINE,
                    labelColor=MUTED_INK,
                    titleColor=MUTED_INK,
                    domain=False,
                ),
            ),
            y=alt.Y(
                "hypothesis_label:N",
                title=None,
                sort="-x",
                axis=alt.Axis(labelLimit=260, domain=False, ticks=False),
            ),
            color=alt.condition(
                alt.datum.is_winner, alt.value(BLUE), alt.value("#9ec5f4")
            ),
            tooltip=[
                alt.Tooltip("hypothesis_label:N", title="Hypothesis"),
                alt.Tooltip("r_squared:Q", title="R² alone", format=".1%"),
                alt.Tooltip("unique_r_squared:Q", title="R² unique", format=".1%"),
                alt.Tooltip("best_feature:N", title="Strongest feature"),
                alt.Tooltip("best_feature_rho:Q", title="Spearman ρ", format=".2f"),
            ],
        )
    )
    labels = bars.mark_text(align="left", dx=6, color=MUTED_INK, fontSize=11).encode(
        text=alt.Text("r_squared:Q", format=".0%"), color=alt.value(MUTED_INK)
    )
    st.altair_chart(
        _styled((bars + labels).properties(height=alt.Step(30))),
        width="stretch",
    )


def _render_hypothesis_scatter(
    scores: pd.DataFrame, features: pd.DataFrame, contenders: pd.DataFrame
) -> None:
    """Each hypothesis's strongest feature against R(x), one panel apiece.

    Small multiples rather than four colours on one plot: the features are
    measured in coins, divisors and characters, so they share no x scale.
    """
    numbers = features.copy()
    numbers["NUMBER_VALUE"] = numbers["NUMBER_VALUE"].astype(int)
    merged = scores.merge(numbers, left_on="number", right_on="NUMBER_VALUE")

    panels = []
    for row in contenders.itertuples():
        feature = str(row.best_feature)
        if feature not in merged.columns:
            continue
        panels.append(
            pd.DataFrame(
                {
                    "number": merged["number"],
                    "score": merged["score"],
                    "feature_value": merged[feature].astype(float),
                    "panel": f"{row.hypothesis_label}  ·  ρ {row.best_feature_rho:+.2f}",
                    "feature": feature,
                }
            )
        )
    if not panels:
        return
    long = pd.concat(panels, ignore_index=True)

    chart = (
        alt.Chart(long)
        .mark_circle(size=45, color=BLUE, opacity=0.65)
        .encode(
            x=alt.X(
                "feature_value:Q",
                title=None,
                axis=alt.Axis(
                    grid=False, labelColor=MUTED_INK, domainColor=GRIDLINE,
                    tickColor=GRIDLINE, labelFontSize=10,
                ),
                scale=alt.Scale(zero=False, nice=True),
            ),
            y=alt.Y(
                "score:Q",
                title="Roundness",
                axis=alt.Axis(
                    grid=True, gridColor=GRIDLINE, labelColor=MUTED_INK,
                    titleColor=MUTED_INK, domain=False, labelFontSize=10,
                ),
                scale=alt.Scale(zero=False),
            ),
            tooltip=[
                alt.Tooltip("number:Q", title="Number"),
                alt.Tooltip("feature:N", title="Feature"),
                alt.Tooltip("feature_value:Q", title="Value", format=".2f"),
                alt.Tooltip("score:Q", title="Roundness", format=".1f"),
            ],
        )
        .properties(width=200, height=150)
        .facet(
            facet=alt.Facet(
                "panel:N", title=None, header=alt.Header(labelFontSize=11, labelLimit=300)
            ),
            columns=2,
        )
        # Each panel's feature has its own units; a shared x scale would be
        # meaningless.
        .resolve_scale(x="independent")
    )
    st.altair_chart(_styled(chart), width="stretch")
    st.caption(
        "Each panel is the strongest single feature behind that hypothesis. ρ is "
        "Spearman's rank correlation with roundness; a strong correlation "
        "pointing the wrong way is evidence against the hypothesis, not for it."
    )


# --- Random numbers ----------------------------------------------------------


def _render_random_numbers(scores: pd.DataFrame) -> None:
    st.markdown("### The “pick a random number” questions")

    try:
        picks = queries.fetch_random_number_picks()
    except DatabaseError as exc:
        _render_read_failure(exc)
        return
    if picks.empty:
        st.caption("No answers yet.")
        return

    percentile = _roundness_percentile(scores)
    before = _clean_picks(picks["RANDOM_NUMBER"])
    after = _clean_picks(picks.get("POST_RANDOM_NUMBER"))

    before_mean = _mean_percentile(before, percentile)
    after_mean = _mean_percentile(after, percentile)

    first, second, third = st.columns(3)
    first.metric(
        "Roundness of a “random” pick",
        "—" if np.isnan(before_mean) else f"{before_mean:.0f}",
        help=(
            "Average roundness percentile of the numbers people picked when "
            "asked for a random one. 50 is what a genuinely uniform pick would "
            "average; above 50 means “random” drifts toward round."
        ),
    )
    second.metric(
        "…after the comparisons",
        "—" if np.isnan(after_mean) else f"{after_mean:.0f}",
        delta=None
        if np.isnan(before_mean) or np.isnan(after_mean)
        else f"{after_mean - before_mean:+.0f}",
        help="The same measure for the closing question, after 20 minutes of "
        "thinking about roundness.",
    )
    third.metric("Numbers offered", f"{len(before) + len(after):,}")

    _render_picks_chart(before, after)
    _render_unique_guesses(picks)
    _render_least_round(picks, scores)


def _clean_picks(column) -> np.ndarray:
    if column is None:
        return np.array([], dtype=int)
    values = pd.to_numeric(pd.Series(column), errors="coerce").dropna()
    return values.astype(int).to_numpy()


def _roundness_percentile(scores: pd.DataFrame) -> pd.Series:
    """Map each number to its 0-100 roundness percentile.

    A percentile rather than the raw score so the neutral baseline is exactly
    50 whatever the metric's units, which is the whole point of the comparison.
    """
    scored = scores.dropna(subset=["score"])
    if scored.empty:
        return pd.Series(dtype=float)
    ranks = metrics.average_ranks(scored["score"].to_numpy(dtype=float))
    return pd.Series(
        100.0 * (ranks - 0.5) / len(ranks), index=scored["number"].astype(int)
    )


def _mean_percentile(picks: np.ndarray, percentile: pd.Series) -> float:
    if picks.size == 0 or percentile.empty:
        return float("nan")
    mapped = pd.Series(picks).map(percentile).dropna()
    return float(mapped.mean()) if len(mapped) else float("nan")


def _render_picks_chart(before: np.ndarray, after: np.ndarray) -> None:
    if before.size == 0 and after.size == 0:
        return
    frame = pd.concat(
        [
            _pick_counts(before, "Before comparing"),
            _pick_counts(after, "After comparing"),
        ],
        ignore_index=True,
    )
    if frame.empty:
        return

    chart = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
        .encode(
            x=alt.X(
                "number:Q",
                title=None,
                scale=alt.Scale(domain=[0.5, config.MAX_NUMBER + 0.5], nice=False),
                axis=alt.Axis(
                    values=DECADE_TICKS, grid=False, labelColor=MUTED_INK,
                    domainColor=GRIDLINE, tickColor=GRIDLINE,
                ),
            ),
            y=alt.Y(
                "count:Q",
                title="People",
                axis=alt.Axis(
                    grid=True, gridColor=GRIDLINE, labelColor=MUTED_INK,
                    titleColor=MUTED_INK, domain=False, tickMinStep=1,
                ),
            ),
            color=alt.Color(
                "when:N",
                title=None,
                # Fixed order, so "before" is blue whether or not anyone has
                # answered the closing question yet.
                scale=alt.Scale(
                    domain=["Before comparing", "After comparing"],
                    range=[BLUE, ORANGE],
                ),
                legend=alt.Legend(orient="top", labelColor=MUTED_INK),
            ),
            xOffset=alt.XOffset("when:N"),
            tooltip=[
                alt.Tooltip("number:Q", title="Number"),
                alt.Tooltip("when:N", title="When"),
                alt.Tooltip("count:Q", title="People"),
            ],
        )
        .properties(height=200)
    )
    st.altair_chart(_styled(chart), width="stretch")


def _pick_counts(picks: np.ndarray, label: str) -> pd.DataFrame:
    if picks.size == 0:
        return pd.DataFrame(columns=["number", "count", "when"])
    counts = pd.Series(picks).value_counts().sort_index()
    return pd.DataFrame(
        {
            "number": counts.index.astype(int),
            "count": counts.to_numpy(dtype=int),
            "when": label,
        }
    )


def _render_unique_guesses(picks: pd.DataFrame) -> None:
    """How the “number fewest others will pick” guesses actually fared."""
    guesses = _clean_picks(picks.get("UNIQUE_GUESS_NUMBER"))
    if guesses.size == 0:
        return
    counts = pd.Series(guesses).value_counts()
    alone = int((counts == 1).sum())

    st.caption(
        f"Asked for the number *fewest other people* would pick, "
        f"{len(guesses):,} respondents chose {counts.size} distinct numbers. "
        f"{alone} managed to be the only one on theirs."
        + (
            f" The most crowded “unique” answer was {int(counts.index[0])}, "
            f"picked by {int(counts.iloc[0])}."
            if int(counts.iloc[0]) > 1
            else ""
        )
    )


def _render_least_round(picks: pd.DataFrame, scores: pd.DataFrame) -> None:
    """What people *named* as least round, against what the comparisons say."""
    named = _clean_picks(picks.get("LEAST_ROUND_NUMBER"))
    if named.size == 0:
        return
    top = pd.Series(named).value_counts()
    scored = scores.dropna(subset=["score"])
    if scored.empty:
        return
    by_comparison = int(scored.loc[scored["score"].idxmin(), "number"])
    st.caption(
        f"Asked outright, respondents named **{int(top.index[0])}** as the least "
        f"round number most often ({int(top.iloc[0])} of {len(named):,}). The "
        f"comparisons, which nobody answered deliberately, say **{by_comparison}**."
    )


# --- Survey classifications --------------------------------------------------


def _render_classifications() -> None:
    """Hidden entirely until the out-of-band AI_CLASSIFY batch has been run.

    The verbatim answers hang off this section rather than standing alone, so
    the categories are always there to read them against -- a wall of
    anonymous free text with no summary over it is not a finding.
    """
    try:
        counts = queries.fetch_survey_classification_counts()
    except DatabaseError:
        # A missing grant or an unavailable Cortex feature must not take the
        # rest of the page down with it; this panel is the optional one.
        return
    if counts.empty:
        return

    st.markdown("### How people explained it")
    frame = counts.rename(
        columns={"CLASSIFICATION": "category", "N_RESPONDENTS": "respondents"}
    )
    bars = (
        alt.Chart(frame)
        .mark_bar(color=BLUE, cornerRadiusTopRight=4, cornerRadiusBottomRight=4, height=18)
        .encode(
            x=alt.X(
                "respondents:Q",
                title="Respondents",
                axis=alt.Axis(
                    grid=True, gridColor=GRIDLINE, labelColor=MUTED_INK,
                    titleColor=MUTED_INK, domain=False, tickMinStep=1,
                ),
            ),
            y=alt.Y(
                "category:N",
                title=None,
                sort="-x",
                axis=alt.Axis(labelLimit=200, domain=False, ticks=False),
            ),
            tooltip=[
                alt.Tooltip("category:N", title="Category"),
                alt.Tooltip("respondents:Q", title="Respondents"),
            ],
        )
    )
    labels = bars.mark_text(align="left", dx=6, color=MUTED_INK, fontSize=11).encode(
        text="respondents:Q"
    )
    st.altair_chart(
        _styled((bars + labels).properties(height=alt.Step(28))),
        width="stretch",
    )
    st.caption(
        "Free-text answers sorted into camps by Snowflake's `AI_CLASSIFY`, run "
        "out of band over the survey responses."
    )
    _render_free_text()


def _render_free_text() -> None:
    """A few answers verbatim. Rendered as plain text, never as markdown.

    These are written by one anonymous respondent and shown to others, so
    nothing here may be interpreted as formatting, links or HTML.
    """
    try:
        text = queries.fetch_survey_free_text()
    except DatabaseError:
        return
    if text.empty:
        return
    with st.expander("In their own words"):
        for row in text.head(12).itertuples():
            definition = getattr(row, "ROUNDNESS_DEFINITION", None)
            factors = getattr(row, "INTUITION_FACTORS", None)
            for value in (definition, factors):
                if isinstance(value, str) and value.strip():
                    st.text(value.strip())
            st.divider()


# --- Coverage ----------------------------------------------------------------


def _render_coverage() -> None:
    st.markdown("### Pair coverage")
    try:
        pairs = queries.fetch_pair_counts()
    except DatabaseError as exc:
        _render_read_failure(exc)
        return
    if pairs.empty:
        return

    covered = int((pairs["N_COMPARISONS"] > 0).sum())
    st.caption(
        f"{covered:,} of {len(pairs):,} possible pairs have been compared at "
        f"least once ({covered / len(pairs):.0%}). Sampling favours the coldest "
        "pairs, so the dark cells spread out rather than deepen."
    )

    frame = pairs.rename(
        columns={
            "PAIR_LOW": "low",
            "PAIR_HIGH": "high",
            "N_COMPARISONS": "comparisons",
        }
    )[["low", "high", "comparisons"]]
    # 4,950 rows, just inside Altair's default 5,000-row guard.
    heatmap = (
        alt.Chart(frame)
        .mark_rect()
        .encode(
            x=alt.X(
                "high:O",
                title=None,
                axis=alt.Axis(
                    values=DECADE_TICKS, labelAngle=0, labelColor=MUTED_INK,
                    domain=False, ticks=False, labelFontSize=10,
                ),
            ),
            y=alt.Y(
                "low:O",
                title=None,
                axis=alt.Axis(
                    values=DECADE_TICKS, labelColor=MUTED_INK, domain=False,
                    ticks=False, labelFontSize=10,
                ),
            ),
            color=alt.Color(
                "comparisons:Q",
                title="Comparisons",
                # One hue, light to dark: this is continuous magnitude, and the
                # lightest step is allowed to recede toward the surface at zero.
                scale=alt.Scale(range=SEQUENTIAL_BLUE, type="linear"),
                legend=alt.Legend(
                    orient="right", labelColor=MUTED_INK, titleColor=MUTED_INK
                ),
            ),
            tooltip=[
                alt.Tooltip("low:O", title="Pair"),
                alt.Tooltip("high:O", title="with"),
                alt.Tooltip("comparisons:Q", title="Comparisons"),
            ],
        )
        .properties(height=420)
    )
    st.altair_chart(_styled(heatmap), width="stretch")


# --- Footer ------------------------------------------------------------------


def _render_footer(scores: pd.DataFrame) -> None:
    st.markdown("### How much to trust this")
    try:
        totals = queries.fetch_collection_totals()
    except DatabaseError as exc:
        _render_read_failure(exc)
        return
    if totals.empty:
        return
    row = totals.iloc[0]

    widths = (scores["ci_high"] - scores["ci_low"]).dropna()
    median_width = float(np.median(widths)) if len(widths) else float("nan")

    first, second, third, fourth = st.columns(4)
    first.metric("Comparisons", f"{int(row['N_COMPARISONS']):,}")
    second.metric("Respondents", f"{int(row['N_RESPONDENTS']):,}")
    third.metric(
        "Pairs covered",
        f"{int(row['N_PAIRS_COVERED']):,}",
        help=f"of {int(row['N_PAIRS_TOTAL']):,} possible pairs",
    )
    fourth.metric(
        "Median interval",
        "—" if np.isnan(median_width) else f"{median_width:.0f}",
        help=(
            "Median width of the 95% bootstrap interval, in Elo points. It "
            "narrows as comparisons accumulate."
        ),
    )

    _render_position_bias()


def _render_position_bias() -> None:
    """The instrument's own health check, one expander down.

    Display order is randomised per pair and recorded separately from the
    outcome precisely so this is measurable. Near 50% is healthy; a strong skew
    means position bias is contaminating the results rather than revealing
    anything about numbers.
    """
    try:
        bias = queries.fetch_position_bias()
    except DatabaseError:
        return
    if bias.empty:
        return

    total = float(bias["N_COMPARISONS"].sum())
    left_wins = float(bias["N_LEFT_WINS"].sum())
    overall = left_wins / total if total else float("nan")

    with st.expander("Position bias check"):
        st.markdown(
            f"The number shown on the **left** won **{overall:.1%}** of "
            "comparisons. Which number goes left is a coin flip, so this should "
            "sit near 50%."
        )
        st.dataframe(
            bias.rename(
                columns={
                    "POSITION_IN_PAGE": "Row on page",
                    "N_COMPARISONS": "Comparisons",
                    "N_LEFT_WINS": "Left won",
                    "LEFT_WIN_PCT": "Left win rate",
                    "MEDIAN_RESPONSE_MS": "Median response (ms)",
                }
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "By row, too: response times drifting upward down the page would "
            "mean people are deliberating rather than reacting."
        )


# --- Shared chrome -----------------------------------------------------------


def _styled(chart: alt.Chart) -> alt.Chart:
    """Recessive chrome, transparent surface, no view border."""
    return (
        chart.configure_view(strokeWidth=0, fill=None)
        .configure_axis(labelFontSize=11, titleFontSize=11, titlePadding=8)
        .configure_legend(labelFontSize=11, titleFontSize=11)
        .configure_facet(spacing=18)
    )


def _render_empty() -> None:
    st.info("No comparisons have been recorded yet — yours will be the first.")
    st.caption(
        "Reload the page in a moment if you have just submitted; the results "
        "are cached briefly."
    )
    if st.button("Reload results"):
        queries.clear_read_caches()
        _score_frame.clear()
        st.rerun()


def _render_read_failure(exc: DatabaseError) -> None:
    st.error("Couldn't load the results — the database didn't answer.")
    st.caption(str(exc))
