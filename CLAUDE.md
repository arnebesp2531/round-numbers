# Number Roundness Experiment

A pairwise-comparison survey that measures what makes a number "round." Respondents are shown two numbers from 1–100 and pick the rounder one on gut instinct. Aggregated comparisons yield a roundness score R(x) for every number, and a quantitative verdict on four competing hypotheses (additive coins/bills, geometric factor count, "roundness gravity" from landmark numbers, Roman-numeral length).

- **Experiment design & hypotheses:** [Number Roundness.md](Number%20Roundness.md)
- **UI reference for the comparison page:** [wireframe.png](wireframe.png)
- **Full implementation plan (read this before starting a feature):** `C:\Users\sarneberg\.claude\plans\logical-snuggling-zephyr.md`

Work is split into 10 numbered features in the plan. The user runs one feature per session to control context cost — build only the feature named, and don't drift into adjacent ones.

## Hard constraints

**The user hand-runs all SQL.** Never execute DDL, DML, or any statement against Snowflake. Write `.sql` files to `sql/` for the user to run in Snowsight. This includes setup, seeds, views, grants, and the `AI_CLASSIFY` batch.

**Dual-target: never import Snowflake outside `app/db.py`.** The same code runs on Streamlit Community Cloud (public respondents) and in Streamlit in Snowflake (admin). `app/db.py` detects which and is the only module allowed to `import snowflake.*`. Everything else uses exactly three functions:

```python
from app.db import query_df, execute_many, get_backend
```

Adding a Snowpark or connector import anywhere else breaks one of the two targets silently.

**The repo is public and the app is internet-facing.** No credentials, keys, account identifiers, or connection strings in committed files — ever. `.streamlit/secrets.toml` is gitignored; only `secrets.toml.example` is committed. Auth is key-pair (Snowflake has deprecated single-factor password auth for programmatic access); a PAT is the only fallback.

**All SQL is parameterized.** No f-strings or `.format()` building statements. Validate server-side that numbers are integers in 1–100 and that `winner_number` is one of the two presented.

## Conventions

- **Fact-first data model.** `COMPARISONS` is the source of truth; every aggregate is a view. Never write a denormalized counts table.
- **Display order is stored separately from outcome.** `left_number`/`right_number` alongside `winner_number`/`loser_number`, so left-side position bias is measurable. Don't collapse these.
- **DDL is idempotent** — `CREATE ... IF NOT EXISTS`, safe to re-run.
- **Pure logic stays pure.** `app/sampling.py`, `analysis/bradley_terry.py`, and `analysis/metrics.py` import no Streamlit and no Snowflake, and are unit-tested without a connection.
- **Charts use Altair** (ships with Streamlit, works in SiS). Invoke the `dataviz` skill before writing chart code.
- **Config lives in `app/config.py`** — database/schema/warehouse names, `PAIRS_PER_PAGE`, sampling `alpha`. No magic numbers scattered in pages.

## Gotchas

- **Bradley-Terry must be regularized.** Add `epsilon ≈ 0.01` virtual wins/losses per pair. Without it the MLE diverges on a disconnected comparison graph or an undefeated number — guaranteed early in data collection.
- **Fetch `V_PAIR_COUNTS` whole and cache it** (`st.cache_data(ttl=60)`, only 4,950 rows). Also keep a local count overlay in `session_state` so one long session keeps spreading across cold pairs before the cache refreshes.
- **One batched write per submitted page**, not per click. The warehouse is XS with 60s auto-suspend; minimizing wake-ups keeps both latency and credits down.
- **Write the `RESPONDENTS` and `INTRO_RESPONSES` rows on intro submit**, so respondents who bail mid-session still contribute data.
- **Results are gated** on `st.session_state.results_unlocked`, set only after survey submission — otherwise early viewers get anchored by the current leaderboard.
- **`AI_CLASSIFY` runs out-of-band only.** The public app must never call Cortex: it would add latency, require Cortex grants on the service role, and break where the function isn't enabled. The results page reads `SURVEY_CLASSIFICATIONS` and hides the panel when empty.

## Commands

```bash
pytest                                # unit tests, no Snowflake needed
streamlit run streamlit_app.py        # local dev, reads .streamlit/secrets.toml
python scripts/generate_number_features.py   # regenerates sql/03_seed_numbers.sql
```

## Sanity checks on real data

10 and 50 should score high; primes near 90 should score low. If not, suspect the winner/loser column mapping before suspecting human nature. `left_won` in `V_COMPARISONS_ENRICHED` should sit near 50% — a strong skew means position bias is contaminating the results.
