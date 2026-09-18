"""Tests for the parts of the database layer that need no connection.

The interesting, testable-without-Snowflake piece is the multi-row VALUES
expansion: one batched write per submitted page is the whole reason it exists,
and both backends execute the statement it produces.

These also pin the contract the rest of the project is built on -- that
importing `app.db` never requires either Snowflake package to be installed,
and that no other module imports one.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.db import MAX_ROWS_PER_STATEMENT, expand_values_clause  # noqa: E402

SINGLE_ROW_INSERT = "INSERT INTO DB.APP.T (A, B, C) VALUES (?, ?, ?)"


def test_single_row_is_left_alone() -> None:
    assert expand_values_clause(SINGLE_ROW_INSERT, 1) == SINGLE_ROW_INSERT


def test_tuple_is_repeated_once_per_row() -> None:
    statement = expand_values_clause(SINGLE_ROW_INSERT, 3)
    assert statement == (
        "INSERT INTO DB.APP.T (A, B, C) VALUES (?, ?, ?), (?, ?, ?), (?, ?, ?)"
    )
    # One statement per page, and a placeholder for every bound value.
    assert statement.count("VALUES") == 1
    assert statement.count("?") == 9


def test_trailing_semicolon_and_whitespace_are_handled() -> None:
    sql = """
        INSERT INTO DB.APP.T (A, B)
        VALUES (?, ?);
    """
    statement = expand_values_clause(sql, 2)
    assert statement.endswith("VALUES (?, ?), (?, ?)")
    assert ";" not in statement


def test_multiline_values_tuple() -> None:
    sql = "INSERT INTO DB.APP.T (A, B)\nVALUES (\n  ?,\n  ?\n)"
    statement = expand_values_clause(sql, 2)
    assert statement.count("?") == 4


def test_row_width_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="placeholders"):
        expand_values_clause(SINGLE_ROW_INSERT, 2, row_width=4)


def test_row_width_match_is_accepted() -> None:
    expand_values_clause(SINGLE_ROW_INSERT, 2, row_width=3)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE DB.APP.T SET A = ?",  # no VALUES clause at all
        "INSERT INTO DB.APP.T (A) SELECT A FROM DB.APP.U",  # insert-select
        "INSERT INTO DB.APP.T (A) VALUES (CURRENT_TIMESTAMP())",  # nothing bound
        "INSERT INTO DB.APP.T (A) VALUES (?), (?)",  # already expanded
    ],
)
def test_statements_that_cannot_be_expanded_are_rejected(sql: str) -> None:
    with pytest.raises(ValueError):
        expand_values_clause(sql, 2)


def test_zero_rows_is_rejected() -> None:
    # execute_many short-circuits on an empty batch before reaching here.
    with pytest.raises(ValueError):
        expand_values_clause(SINGLE_ROW_INSERT, 0)


def test_chunk_size_leaves_headroom_under_the_bind_limit() -> None:
    # Snowflake caps bindings per statement; a full chunk of the widest row the
    # app writes (COMPARISONS, 9 columns) must stay well clear of it.
    assert MAX_ROWS_PER_STATEMENT * 9 < 16_000


def test_importing_db_does_not_require_snowflake() -> None:
    # Community Cloud has no Snowpark and SiS has no connector, so both imports
    # must stay inside functions.
    import app.db  # noqa: F401

    assert "snowflake.connector" not in sys.modules
    assert "snowflake.snowpark" not in sys.modules


def test_no_other_module_imports_snowflake() -> None:
    """app/db.py is the only module allowed to touch Snowflake directly.

    An import anywhere else breaks one of the two deployment targets silently,
    which is exactly the kind of thing nobody notices until a respondent hits
    the public URL.
    """
    import_re = re.compile(r"^\s*(?:from|import)\s+snowflake\b", re.MULTILINE)
    skip_dirs = {".venv", "venv", "site-packages", "tests"}

    offenders = []
    for path in sorted(REPO_ROOT.glob("**/*.py")):
        if path.name == "db.py" or skip_dirs & set(path.parts):
            continue
        if import_re.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"snowflake imported outside app/db.py: {offenders}"
