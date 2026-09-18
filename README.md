# Number Roundness

What makes a number "round"? [Number Roundness.md](Number%20Roundness.md) sets out
four competing hypotheses — additive coin/bill decomposition, geometric factor
count, "roundness gravity" from heavy neighbours like 50 and 100, and Roman
numeral length. None can be settled by argument, so this is the instrument that
settles them with data.

A respondent is shown two numbers from 1–100 and picks the one that feels more
round, on gut instinct. Enough pairwise comparisons yield a full relative
ranking without ever asking anyone to rate a number in isolation. Complete
coverage is 100C2 = **4,950 pairs**.

The output is a roundness score `R(x)` for every number 1–100, plus a
quantitative verdict on which hypothesis actually predicts human intuition.

## How it is built

One codebase, two deployment targets, sharing a thin database abstraction:

- **Streamlit Community Cloud** — the public collection app. Free, a real
  public URL, deploys from this repo.
- **Streamlit in Snowflake** — the identical code, for admin and analysis.
  SiS has no anonymous access mode, which is why it cannot host the public app.

Snowflake is the single source of truth either way.

Scoring headline is a **Bradley-Terry** strength model with bootstrap
confidence intervals. Raw win% is shown alongside as a sanity check, but it is
biased by which opponents a number happened to face.

The flow is linear — intro → comparisons → survey → results — and the results
are locked until a respondent submits their own survey, so nobody is anchored
by the current standings before contributing to them.

## Layout

```
streamlit_app.py     entry point: page config, global CSS, stage router
app/                 config, db abstraction, queries, sampling, state
app/pages/           intro, comparisons, survey, results
analysis/            bradley_terry.py, metrics.py  (pure numpy, no Snowflake)
scripts/             generate_number_features.py, power_simulation.py,
                     smoke_test_connection.py
sql/                 hand-run setup scripts, 01 through 06
tests/               pytest: the pure logic, and every page through AppTest
```

## Setup

### 1. Snowflake objects

Run these by hand in a Snowsight worksheet, in order. All are re-runnable.

| Script | What it does |
|---|---|
| `sql/01_database_and_schema.sql` | `ROUNDNESS_LAB.APP` and the `ROUNDNESS_WH` warehouse |
| `sql/02_tables.sql` | Fact tables and the `NUMBERS` reference table |
| `sql/03_seed_numbers.sql` | Seeds 1–100 with per-hypothesis features (generated) |
| `sql/04_views.sql` | `V_PAIR_COUNTS`, `V_NUMBER_STATS`, and the rest |
| `sql/05_service_role_and_user.sql` | Least-privilege service user for the public app |
| `sql/06_ai_classify_survey.sql` | Out-of-band `AI_CLASSIFY` over the survey free text |

`03_seed_numbers.sql` is generated — do not edit it by hand:

```
python scripts/generate_number_features.py
```

### 2. Local run

```
pip install -r requirements-dev.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then fill it in
python scripts/smoke_test_connection.py
streamlit run streamlit_app.py
```

The smoke test is the only thing that exercises a real connection — it reports
the detected backend, checks parameter binding, and reads every view the app
uses. Run it after the SQL scripts and again after
`sql/05_service_role_and_user.sql`, to confirm the least-privilege role can
still do everything the app needs. `--write` additionally exercises the batched
INSERT path and prints the cleanup SQL for the rows it leaves behind.

Deployment steps for both targets live in `DEPLOYMENT.md`.

## Security

This repo is public and the app is internet-facing.

- Credentials live **only** in Community Cloud's secrets manager.
  `.streamlit/secrets.toml` is gitignored; only the `.example` is committed.
- The service user authenticates with a **key pair** and holds a minimal role:
  `INSERT` on the four write tables, `SELECT` on `NUMBERS` and the views, and
  `USAGE` on the database, schema and warehouse. No `DELETE`, no `UPDATE`, no
  DDL. The worst case for a leaked key is junk rows, not data loss.
- Every read in `app/queries.py` goes through a view, never a fact table. Views
  run with the definer's rights, so the role needs no `SELECT` on the tables it
  writes to — a leaked key cannot read back the raw comparisons.
- Every write is parameterized. Numbers are validated server-side as integers
  in 1–100, with `winner_number` checked against the pair actually presented.
- Free-text survey fields are length-capped and rendered as text, never as
  markdown or HTML.

## How many comparisons do we need?

```
python scripts/power_simulation.py
```

Runs simulated respondents through the real sampler against a population whose
roundness is known, and reports where the recovered ranking stops moving. At
the default settings that is around **10,000 comparisons — roughly 300
respondents** doing three pages each. Use that as the recruiting target rather
than a guess. On real data the split-half correlation it reports is the figure
to watch, since it needs no ground truth.

## Tests

```
pytest
```

No Snowflake connection required. The sampling, Bradley-Terry and metrics logic
is pure and tested in isolation; the four pages are driven through Streamlit's
headless `AppTest` with the database reads and writes stubbed, so the gate, the
batched write and the results panels are all covered without a connection.
