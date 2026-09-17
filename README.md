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

## Layout

```
streamlit_app.py     entry point: page config, global CSS, stage router
app/                 config, db abstraction, queries, sampling, state, pages
analysis/            bradley_terry.py, metrics.py  (pure numpy, no Snowflake)
scripts/             generate_number_features.py, power_simulation.py
sql/                 hand-run setup scripts, 01 through 06
tests/               pytest: sampling, bradley_terry, metrics
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
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then fill it in
streamlit run streamlit_app.py
```

Deployment steps for both targets live in `DEPLOYMENT.md`.

## Security

This repo is public and the app is internet-facing.

- Credentials live **only** in Community Cloud's secrets manager.
  `.streamlit/secrets.toml` is gitignored; only the `.example` is committed.
- The service user authenticates with a **key pair** and holds a minimal role:
  `INSERT` on the four write tables, `SELECT` on `NUMBERS` and the views, and
  `USAGE` on the database, schema and warehouse. No `DELETE`, no `UPDATE`, no
  DDL. The worst case for a leaked key is junk rows, not data loss.
- Every write is parameterized. Numbers are validated server-side as integers
  in 1–100, with `winner_number` checked against the pair actually presented.
- Free-text survey fields are length-capped and rendered as text, never as
  markdown or HTML.

## Tests

```
pytest
```

No Snowflake connection required — the sampling, Bradley-Terry and metrics
logic is pure and tested in isolation.
