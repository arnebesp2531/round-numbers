"""Prove the database layer works against a real Snowflake account.

The unit tests deliberately need no connection, so this is the one thing that
actually exercises `app/db.py` end to end: credentials, key-pair auth, qmark
binding, every read in `app/queries.py`, and -- if you ask for it -- a batched
write.

    python scripts/smoke_test_connection.py            # read-only
    python scripts/smoke_test_connection.py --write     # also writes test rows

Run it from the repo root: Streamlit looks for secrets in
`./.streamlit/secrets.toml`. Run it before wiring up any UI, and again after
`sql/05_service_role_and_user.sql` to confirm the least-privilege role can
still do everything the app needs.

`--write` inserts real rows that the service role cannot delete. They are
tagged `EXPERIMENT_VARIANT = 'SMOKE_TEST'`, and the script prints the cleanup
SQL to hand-run as the schema owner.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# app.queries decorates its reads with st.cache_data, which warns once per
# function when there is no Streamlit runtime. Harmless here, but it buries the
# output. Streamlit sets an explicit level on each of its own loggers when it is
# imported, so every one of them has to be quietened, after importing Streamlit
# and before the import that triggers the warnings.
import streamlit  # noqa: E402, F401

for _logger_name in list(logging.root.manager.loggerDict):
    if _logger_name.startswith("streamlit"):
        logging.getLogger(_logger_name).setLevel(logging.ERROR)

from app import config, queries  # noqa: E402
from app.db import DatabaseError, DbConfigError, get_backend, query_df  # noqa: E402

SMOKE_TEST_VARIANT = "SMOKE_TEST"

# One page of comparisons, chosen so a leftover row is obvious as test data
# rather than a plausible human answer.
SMOKE_TEST_PAIRS = [(1, 2), (3, 4)]

_failures: list[str] = []


def check(label: str, fn) -> object:
    """Run one check, print a one-line verdict, and keep going on failure.

    A credentials problem is the exception: it would fail every remaining check
    with the same message, so it stops the run instead.
    """
    try:
        result = fn()
    except DbConfigError:
        raise
    except Exception as exc:
        _failures.append(label)
        print(f"  FAIL  {label}")
        print(f"        {type(exc).__name__}: {exc}")
        return None
    detail = ""
    if isinstance(result, pd.DataFrame):
        detail = f"{len(result)} row(s)"
    elif result is not None:
        detail = str(result)
    print(f"  ok    {label}{'  -> ' + detail if detail else ''}")
    return result


def expect(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}{'  -> ' + detail if detail else ''}")
    else:
        _failures.append(label)
        print(f"  FAIL  {label}{'  -> ' + detail if detail else ''}")


def report_backend() -> None:
    print("Backend")
    backend = get_backend()
    print(f"  ok    detected backend  -> {backend}")
    print(f"  ok    rows will be tagged APP_SOURCE  -> {queries.app_source()}")


def report_session() -> None:
    print("\nSession")
    frame = check(
        "session identity",
        lambda: query_df(
            """
            SELECT CURRENT_VERSION()   AS SNOWFLAKE_VERSION,
                   CURRENT_ACCOUNT()   AS ACCOUNT,
                   CURRENT_USER()      AS USER_NAME,
                   CURRENT_ROLE()      AS ROLE_NAME,
                   CURRENT_WAREHOUSE() AS WAREHOUSE_NAME,
                   CURRENT_DATABASE()  AS DATABASE_NAME,
                   CURRENT_SCHEMA()    AS SCHEMA_NAME
            """
        ),
    )
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        for column, value in frame.iloc[0].items():
            print(f"        {column:<18} {value}")


def check_parameter_binding() -> None:
    print("\nParameter binding")
    frame = check(
        "qmark placeholders bind positionally",
        lambda: query_df(
            "SELECT ? AS FIRST_VALUE, ? AS SECOND_VALUE, ? AS THIRD_VALUE",
            [42, "roundness", None],
        ),
    )
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        row = frame.iloc[0]
        expect(
            "bound values round-trip",
            int(row["FIRST_VALUE"]) == 42
            and str(row["SECOND_VALUE"]) == "roundness"
            and pd.isna(row["THIRD_VALUE"]),
            f"{row['FIRST_VALUE']!r}, {row['SECOND_VALUE']!r}, {row['THIRD_VALUE']!r}",
        )


def check_reads() -> None:
    print("\nReference data")
    numbers = check("NUMBERS readable", queries.fetch_numbers)
    if isinstance(numbers, pd.DataFrame):
        expected = config.MAX_NUMBER - config.MIN_NUMBER + 1
        expect(
            f"NUMBERS seeded with {expected} rows",
            len(numbers) == expected,
            f"{len(numbers)} rows -- run sql/03_seed_numbers.sql" if len(numbers) != expected else "",
        )

    pairs = check("V_PAIR_COUNTS readable", queries.fetch_pair_counts)
    if isinstance(pairs, pd.DataFrame):
        expect(
            f"all {config.TOTAL_PAIRS} pairs present, zero-count included",
            len(pairs) == config.TOTAL_PAIRS,
            f"{len(pairs)} rows" if len(pairs) != config.TOTAL_PAIRS else "",
        )

    print("\nDerived views")
    for label, fn in (
        ("V_NUMBER_STATS", queries.fetch_number_stats),
        ("V_COMPARISONS_ENRICHED + weights (BT input)", queries.fetch_comparisons_for_scoring),
        ("V_RESPONDENT_WEIGHTS", queries.fetch_respondent_weights),
        ("V_COLLECTION_TOTALS", queries.fetch_collection_totals),
        ("position bias", queries.fetch_position_bias),
        ("V_RANDOM_NUMBER_PICKS", queries.fetch_random_number_picks),
        ("V_SURVEY_FREE_TEXT", queries.fetch_survey_free_text),
        ("V_SURVEY_CLASSIFICATION_COUNTS", queries.fetch_survey_classification_counts),
    ):
        check(f"{label} readable", fn)


def check_validation() -> None:
    """Guard the checks that keep bad rows out, without touching Snowflake."""
    print("\nServer-side validation")
    rejects = [
        ("number 0 rejected", lambda: queries.validate_number(0)),
        ("number 101 rejected", lambda: queries.validate_number(101)),
        ("non-integer rejected", lambda: queries.validate_number("50")),
        (
            "winner outside the presented pair rejected",
            lambda: queries.validate_comparison(10, 20, 30),
        ),
        ("a pair of one number rejected", lambda: queries.validate_comparison(7, 7, 7)),
    ]
    for label, fn in rejects:
        try:
            fn()
        except ValueError:
            print(f"  ok    {label}")
        else:
            _failures.append(label)
            print(f"  FAIL  {label}  -> no error raised")


def check_write() -> None:
    print("\nWrite round-trip")
    respondent_id = str(uuid.uuid4())
    print(f"        respondent_id {respondent_id}")

    before = queries.fetch_pair_counts()
    before_counts = {
        (int(row.PAIR_LOW), int(row.PAIR_HIGH)): int(row.N_COMPARISONS)
        for row in before.itertuples()
    }

    check(
        "RESPONDENTS insert",
        lambda: queries.insert_respondent(respondent_id, experiment_variant=SMOKE_TEST_VARIANT),
    )
    check(
        "INTRO_RESPONSES insert",
        lambda: queries.insert_intro_response(respondent_id, 7, 73),
    )
    written = check(
        f"COMPARISONS batched insert ({len(SMOKE_TEST_PAIRS)} rows, 1 statement)",
        lambda: queries.insert_comparisons(
            respondent_id,
            page_number=0,
            answers=[
                {
                    "left_number": low,
                    "right_number": high,
                    "winner_number": low,
                    "position_in_page": index,
                    "response_ms": 1234,
                }
                for index, (low, high) in enumerate(SMOKE_TEST_PAIRS)
            ],
        ),
    )
    expect(
        "all rows reported written",
        written == len(SMOKE_TEST_PAIRS),
        f"{written} of {len(SMOKE_TEST_PAIRS)}",
    )
    check(
        "SURVEY_RESPONSES insert",
        lambda: queries.insert_survey_response(
            respondent_id,
            roundness_definition="smoke test row -- safe to delete",
            intuition_factors="smoke test row -- safe to delete",
            least_round_number=97,
            post_random_number=8,
        ),
    )

    queries.clear_read_caches()
    after = queries.fetch_pair_counts()
    after_counts = {
        (int(row.PAIR_LOW), int(row.PAIR_HIGH)): int(row.N_COMPARISONS)
        for row in after.itertuples()
    }
    incremented = [
        pair for pair, count in after_counts.items() if count > before_counts.get(pair, 0)
    ]
    expect(
        "V_PAIR_COUNTS incremented for exactly the pairs written",
        sorted(incremented) == sorted(SMOKE_TEST_PAIRS),
        f"incremented {sorted(incremented)}",
    )

    print(
        "\n  The service role cannot DELETE. To remove the test rows, hand-run "
        "this\n  in Snowsight as the schema owner:\n"
    )
    print(f"    USE DATABASE {config.DATABASE};")
    print(f"    USE SCHEMA {config.SCHEMA};")
    print(f"    DELETE FROM {config.TABLE_COMPARISONS}        WHERE RESPONDENT_ID = '{respondent_id}';")
    print(f"    DELETE FROM {config.TABLE_INTRO_RESPONSES}    WHERE RESPONDENT_ID = '{respondent_id}';")
    print(f"    DELETE FROM {config.TABLE_SURVEY_RESPONSES}   WHERE RESPONDENT_ID = '{respondent_id}';")
    print(f"    DELETE FROM {config.TABLE_RESPONDENTS}        WHERE RESPONDENT_ID = '{respondent_id}';")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="also insert test rows; they are permanent and must be deleted by hand",
    )
    args = parser.parse_args()

    print(f"Smoke-testing {config.DATABASE}.{config.SCHEMA}\n")

    try:
        report_backend()
        report_session()
        check_parameter_binding()
        check_reads()
        check_validation()
        if args.write:
            check_write()
        else:
            print("\nWrite round-trip\n  skipped  (pass --write to exercise the INSERT path)")
    except DbConfigError as exc:
        print(f"\nStopped: the connection is not configured.\n  {exc}")
        return 1
    except DatabaseError as exc:
        print(f"\nStopped: {exc}")
        return 1

    print()
    if _failures:
        print(f"{len(_failures)} check(s) failed:")
        for label in _failures:
            print(f"  - {label}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
