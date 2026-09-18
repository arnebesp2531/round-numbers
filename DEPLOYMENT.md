# Deployment

Two targets, one codebase. Which one is running is detected at runtime by
`app/db.py`; nothing else in the project knows the difference.

| Target | Who it is for | Auth |
|---|---|---|
| **Streamlit Community Cloud** | the public — anyone with the link | key-pair, as a least-privilege service user |
| **Streamlit in Snowflake** | you, for admin and analysis | whoever opens the app |

Community Cloud hosts the public app because Streamlit in Snowflake has no
anonymous access mode: every viewer would need a Snowflake login, which kills
"send a link to my friends."

---

## 1. Snowflake objects

Run these by hand in a Snowsight worksheet, in order. All are re-runnable.

| Script | Run as | What it does |
|---|---|---|
| `sql/01_database_and_schema.sql` | SYSADMIN | `ROUNDNESS_LAB.APP`, warehouse `ROUNDNESS_WH` |
| `sql/02_tables.sql` | SYSADMIN | Fact tables and `NUMBERS` |
| `sql/03_seed_numbers.sql` | SYSADMIN | Seeds 1–100 with the per-hypothesis features |
| `sql/04_views.sql` | SYSADMIN | Everything derived |
| `sql/05_service_role_and_user.sql` | SECURITYADMIN, then SYSADMIN | The service role and user for the public app |

`03_seed_numbers.sql` is generated. If a feature definition changes, run
`python scripts/generate_number_features.py` and re-run the emitted SQL.

---

## 2. The key pair

Generate it locally. **The private half never goes into this repo, into
Snowflake, or into a chat window.**

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

Register the **public** half on the service user. Paste the body of
`rsa_key.pub` — everything between the BEGIN and END lines, newlines stripped:

```sql
ALTER USER ROUNDNESS_APP_SVC SET RSA_PUBLIC_KEY = 'MIIBIjANBgkqh...';
```

Then confirm the role is as locked down as it is supposed to be, before the key
goes anywhere. As a role that holds `ROUNDNESS_APP_ROLE`:

```sql
USE ROLE ROUNDNESS_APP_ROLE;
USE WAREHOUSE ROUNDNESS_WH;
USE DATABASE ROUNDNESS_LAB;
USE SCHEMA APP;

SELECT COUNT(*) FROM V_PAIR_COUNTS;   -- expect 4950
SELECT COUNT(*) FROM COMPARISONS;     -- expect: insufficient privileges
DELETE FROM COMPARISONS WHERE 1 = 0;  -- expect: insufficient privileges
```

The first must succeed and the other two must fail. If `DELETE` succeeds, stop:
`sql/05_service_role_and_user.sql` has not done its job and a leaked key would
be able to destroy the data rather than merely add to it.

---

## 3. Local run

```bash
pip install -r requirements-dev.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Fill in `account`, `user` and the **full PEM contents** of `rsa_key.p8`,
BEGIN/END lines included, as `private_key`. Then:

```bash
python scripts/smoke_test_connection.py --write
streamlit run streamlit_app.py
```

The smoke test is the only thing that exercises a real connection: it reports
the detected backend, checks parameter binding, reads every view the app uses,
and with `--write` exercises the batched INSERT. It leaves rows behind on
purpose — the service role cannot delete them — and prints the cleanup SQL to
run as the owner.

Walk the app end to end: intro → two comparison pages → survey → results. Then
confirm in Snowsight that the rows landed under one `respondent_id`:

```sql
SELECT 'respondents' AS T, COUNT(*) FROM RESPONDENTS      WHERE RESPONDENT_ID = '<id>'
UNION ALL SELECT 'intro',       COUNT(*) FROM INTRO_RESPONSES  WHERE RESPONDENT_ID = '<id>'
UNION ALL SELECT 'comparisons', COUNT(*) FROM COMPARISONS      WHERE RESPONDENT_ID = '<id>'
UNION ALL SELECT 'survey',      COUNT(*) FROM SURVEY_RESPONSES WHERE RESPONDENT_ID = '<id>';
```

`.streamlit/secrets.toml` is gitignored. Confirm it stayed that way:

```bash
git status --porcelain            # secrets.toml must not appear
git log -p | grep -i "BEGIN.*PRIVATE KEY"   # must find nothing
```

---

## 4. Streamlit Community Cloud (the public app)

1. Push the repo to GitHub. It is public, so re-read the check above first.
2. At [share.streamlit.io](https://share.streamlit.io), **Create app** → from
   this repo, branch `main`, main file `streamlit_app.py`.
3. Under **Advanced settings**, set the Python version to 3.11 or later and
   paste the entire `[snowflake]` block from your local `secrets.toml` into
   **Secrets**. This is the only place the private key lives besides your own
   machine.
4. Deploy. The first boot installs `requirements.txt`, which takes a few
   minutes.

Then, in a **logged-out incognito window**, open the public URL and confirm:

- the intro renders and accepts both numbers;
- the first comparison page appears within a few seconds of a cold start — the
  warehouse auto-suspends after 60s, so the first respondent of the day pays a
  resume;
- a submitted page increments `V_PAIR_COUNTS` for exactly the pairs shown;
- the results page is unreachable until the survey is submitted.

Community Cloud sleeps an app after a week of no traffic and wakes it on the
next visit. That is fine here; the cost is one slow first load.

---

## 5. Streamlit in Snowflake (admin and analysis)

Same code, no secrets, and the app runs as whoever opens it.

1. In Snowsight: **Projects → Streamlit → + Streamlit App**. Put it in
   `ROUNDNESS_LAB.APP` on warehouse `ROUNDNESS_WH`.
2. Pick Streamlit **1.49 or later** (or the newest on offer) — see the note in
   `environment.yml` for why.
3. Upload the app files, preserving the directory structure:
   `streamlit_app.py`, `app/`, `analysis/`. `scripts/`, `sql/` and `tests/` are
   not needed at runtime.
4. Add `altair` in the app's **Packages** panel, matching `environment.yml`.
5. Run it. If `app/db.py` has detected SiS correctly there is no secrets error
   at all — that is the whole test of the abstraction.

Use SiS for reading results, not for collecting them: it has no anonymous
access, and rows written there are tagged `APP_SOURCE = 'SIS'` so you can tell
your own walkthroughs from real respondents.

```sql
SELECT APP_SOURCE, COUNT(*) FROM RESPONDENTS GROUP BY APP_SOURCE;
```

---

## 6. After the data starts arriving

**Sanity checks.** 10 and 50 should rank high and primes near 90 should rank
low. If not, suspect the winner/loser column mapping before suspecting human
nature. The results page's position-bias expander should show the left-hand
number winning near 50% of comparisons; a strong skew means position bias is
contaminating the results rather than anything about numbers.

**Survey classification.** Hand-run `sql/06_ai_classify_survey.sql` in Snowsight
whenever you want the "How people explained it" panel refreshed. It is
re-runnable and only classifies respondents who do not already have a row. The
app never calls Cortex itself, and the panel stays hidden until this has been
run at least once.

**Key rotation.** Set `RSA_PUBLIC_KEY_2` to the new public key, swap the private
key in Community Cloud's secrets manager, confirm the app still works, then
`UNSET RSA_PUBLIC_KEY` and promote the new one into the first slot.

**If a key leaks.** `ALTER USER ROUNDNESS_APP_SVC UNSET RSA_PUBLIC_KEY;` cuts
access immediately. The damage ceiling is junk rows, which you can find and
delete by `SUBMITTED_AT` and `RESPONDENT_ID` — the role holds no `DELETE` and
no `UPDATE`, so nothing already collected can have been altered.
