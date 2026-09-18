"""The dual-target database abstraction. The keystone of this project.

The same code runs in two places:

* **Streamlit Community Cloud** — the public collection app, which reaches
  Snowflake through ``snowflake.connector`` using key-pair auth.
* **Streamlit in Snowflake** — admin and analysis, where a Snowpark session is
  already active and no credentials exist at all.

This is the *only* module allowed to ``import snowflake.*``. Everything else in
the project uses exactly three functions::

    from app.db import query_df, execute_many, get_backend

Adding a Snowpark or connector import anywhere else breaks one of the two
targets silently: Community Cloud has no active session, and SiS has no
secrets. Both imports live inside functions here, so importing this module
never requires either package to be installed.

Two conventions the rest of the project depends on:

* **Placeholders are ``?``** (qmark), with parameters passed as a positional
  sequence. This is the one style both backends bind natively -- Snowpark's
  ``session.sql(sql, params)`` speaks qmark, and the connector is opened with
  ``paramstyle="qmark"`` to match. Never build a statement with an f-string.
* **Column names come back UPPERCASE** from both backends, because that is how
  Snowflake stores the unquoted identifiers in ``sql/``.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Literal, Mapping, Sequence

import pandas as pd

from app import config

Backend = Literal["sis", "external"]

__all__ = [
    "Backend",
    "DatabaseError",
    "DbConfigError",
    "execute_many",
    "get_backend",
    "query_df",
]


class DatabaseError(RuntimeError):
    """Anything that went wrong talking to Snowflake."""


class DbConfigError(DatabaseError):
    """The external backend is missing or misreading its credentials."""


# Rows per INSERT statement. A comparison page submits PAIRS_PER_PAGE rows, so
# this only bites on a bulk backfill; it keeps the bind count per statement well
# clear of Snowflake's limit.
MAX_ROWS_PER_STATEMENT = 500

# Guards backend detection and connection setup. Re-entrant because the setup
# helpers call get_backend().
_init_lock = threading.RLock()

# Serializes statement execution. A connector connection is not safe to use
# from two threads at once, and on Community Cloud every respondent's rerun
# gets its own script-runner thread against this one shared connection.
# Serializing costs nothing at this traffic level and keeps warehouse
# wake-ups down.
_execute_lock = threading.Lock()

_backend: Backend | None = None
_sis_session: Any = None
_connection: Any = None


# --- Backend detection -------------------------------------------------------


def get_backend() -> Backend:
    """Return ``"sis"`` or ``"external"``, detected once per process.

    Detection is by capability, not configuration: if a Snowpark session is
    already active we are running inside Snowflake.
    """
    global _backend
    if _backend is not None:
        return _backend
    with _init_lock:
        if _backend is None:
            _backend = "sis" if _active_sis_session() is not None else "external"
    return _backend


def _active_sis_session() -> Any:
    """The active Snowpark session, or None if there isn't one.

    Both failure modes mean the same thing -- not SiS. ImportError is
    Community Cloud, where Snowpark isn't installed; the exception from
    ``get_active_session()`` is any environment where Snowpark is installed but
    nothing has opened a session.
    """
    global _sis_session
    if _sis_session is not None:
        return _sis_session
    try:
        from snowflake.snowpark.context import get_active_session

        _sis_session = get_active_session()
    except Exception:
        return None
    return _sis_session


# --- External connection -----------------------------------------------------


def _secrets() -> Mapping[str, Any]:
    """The ``[snowflake]`` block from Streamlit secrets."""
    import streamlit as st

    try:
        section = st.secrets["snowflake"]
    except Exception as exc:  # missing file, missing section, parse error
        raise DbConfigError(
            "No [snowflake] section in Streamlit secrets. Copy "
            ".streamlit/secrets.toml.example to .streamlit/secrets.toml and "
            "fill it in for a local run, or paste the same block into "
            "Streamlit Community Cloud's secrets manager."
        ) from exc
    return section


def _private_key_der(pem: str, passphrase: str | None) -> bytes:
    """Convert a PEM private key to the DER bytes the connector wants.

    ``cryptography`` is a hard dependency of snowflake-connector-python, so
    this adds nothing to the deployed environment.
    """
    from cryptography.hazmat.primitives import serialization

    password = passphrase.encode() if passphrase else None
    try:
        key = serialization.load_pem_private_key(pem.strip().encode(), password=password)
    except Exception as exc:
        raise DbConfigError(
            "Could not read private_key from secrets. It must be the full PEM "
            "contents of the PKCS#8 key, BEGIN/END lines included, and "
            "private_key_passphrase must be set if the key has one."
        ) from exc
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _connect_kwargs() -> dict[str, Any]:
    secrets = _secrets()

    missing = [k for k in ("account", "user") if not secrets.get(k)]
    if missing:
        raise DbConfigError(f"Missing required secret(s): {', '.join(missing)}.")

    kwargs: dict[str, Any] = {
        "account": secrets["account"],
        "user": secrets["user"],
        "role": secrets.get("role"),
        "warehouse": secrets.get("warehouse", config.WAREHOUSE),
        "database": secrets.get("database", config.DATABASE),
        "schema": secrets.get("schema", config.SCHEMA),
        # One placeholder style across both backends. See the module docstring.
        "paramstyle": "qmark",
        # Keeps the Snowflake session token renewed through idle spells, so a
        # respondent who leaves a tab open still submits successfully. The
        # heartbeat is a metadata call and does not wake the warehouse.
        "client_session_keep_alive": True,
        "session_parameters": {"QUERY_TAG": "roundness_lab_app"},
    }

    if secrets.get("private_key"):
        kwargs["private_key"] = _private_key_der(
            secrets["private_key"], secrets.get("private_key_passphrase")
        )
    elif secrets.get("pat"):
        # Fallback only. Snowflake has deprecated single-factor password auth
        # for programmatic access, so a PAT is the one alternative to key-pair.
        kwargs["password"] = secrets["pat"]
        kwargs["authenticator"] = "PROGRAMMATIC_ACCESS_TOKEN"
    else:
        raise DbConfigError(
            "Secrets contain neither private_key nor pat. Key-pair auth is "
            "expected; see .streamlit/secrets.toml.example."
        )

    return {k: v for k, v in kwargs.items() if v is not None}


def _open_connection() -> Any:
    import snowflake.connector

    try:
        return snowflake.connector.connect(**_connect_kwargs())
    except DbConfigError:
        raise
    except Exception as exc:
        raise DatabaseError(f"Could not connect to Snowflake: {exc}") from exc


def _connection_and_freshness() -> tuple[Any, bool]:
    """The shared connection, plus whether this call just opened it.

    Callers use the flag to decide whether a failure is worth one retry: a
    connection we just opened failed for a real reason, an older one may
    simply have gone stale.
    """
    global _connection
    with _init_lock:
        if _connection is not None and not _connection.is_closed():
            return _connection, False
        _connection = _open_connection()
        return _connection, True


def _discard_connection() -> None:
    global _connection
    with _init_lock:
        stale, _connection = _connection, None
    if stale is not None:
        try:
            stale.close()
        except Exception:
            pass


def _cursor_to_df(cursor: Any) -> pd.DataFrame:
    try:
        return cursor.fetch_pandas_all()
    except Exception:
        # Not every result set arrives in Arrow format. Rebuild by hand rather
        # than fail, keeping the same uppercase column names.
        rows = cursor.fetchall()
        columns = [d[0] for d in (cursor.description or [])]
        return pd.DataFrame(rows, columns=columns)


# --- The public three --------------------------------------------------------


def query_df(sql: str, params: Sequence[Any] | None = None) -> pd.DataFrame:
    """Run a read query and return a DataFrame with UPPERCASE column names.

    ``sql`` uses ``?`` placeholders; ``params`` is a positional sequence.
    """
    bindings = list(params) if params is not None else None

    if get_backend() == "sis":
        session = _require_sis_session()
        with _execute_lock:
            try:
                return session.sql(sql, params=bindings).to_pandas()
            except Exception as exc:
                raise DatabaseError(f"Query failed: {exc}") from exc

    for attempt in (1, 2):
        connection, is_fresh = _connection_and_freshness()
        try:
            with _execute_lock:
                with connection.cursor() as cursor:
                    cursor.execute(sql, bindings)
                    return _cursor_to_df(cursor)
        except Exception as exc:
            # A stale connection is the common failure on a long-idle app, and
            # a read is safe to repeat. Writes deliberately do not retry.
            if is_fresh or attempt == 2:
                raise DatabaseError(f"Query failed: {exc}") from exc
            _discard_connection()

    raise AssertionError("unreachable")  # pragma: no cover


def execute_many(sql: str, rows: Sequence[Sequence[Any]]) -> int:
    """Write many rows in as few statements as possible; returns rows written.

    ``sql`` is a single-row INSERT whose ``VALUES`` clause is one tuple of
    ``?`` placeholders. That tuple is repeated once per row so a submitted page
    costs one statement -- the warehouse is XS with a 60s auto-suspend, and
    minimising wake-ups keeps both latency and credits down.

    Deliberately never retried. A failed INSERT may or may not have been
    applied server-side, and Snowflake does not enforce the primary key, so a
    retry could silently duplicate a respondent's answers. Callers surface the
    error and let the respondent submit again.
    """
    materialized = [tuple(row) for row in rows]
    if not materialized:
        return 0

    width = len(materialized[0])
    if any(len(row) != width for row in materialized):
        raise ValueError("every row passed to execute_many must be the same length")

    written = 0
    for start in range(0, len(materialized), MAX_ROWS_PER_STATEMENT):
        chunk = materialized[start : start + MAX_ROWS_PER_STATEMENT]
        statement = expand_values_clause(sql, len(chunk), row_width=width)
        bindings = [value for row in chunk for value in row]
        _execute_write(statement, bindings)
        written += len(chunk)
    return written


def _execute_write(sql: str, bindings: list[Any]) -> None:
    if get_backend() == "sis":
        session = _require_sis_session()
        with _execute_lock:
            try:
                session.sql(sql, params=bindings).collect()
            except Exception as exc:
                raise DatabaseError(f"Write failed: {exc}") from exc
        return

    connection, _ = _connection_and_freshness()
    try:
        with _execute_lock:
            with connection.cursor() as cursor:
                cursor.execute(sql, bindings)
    except Exception as exc:
        raise DatabaseError(f"Write failed: {exc}") from exc


def _require_sis_session() -> Any:
    session = _active_sis_session()
    if session is None:  # pragma: no cover - only if a session dies mid-run
        raise DatabaseError("Snowpark session is no longer available.")
    return session


# --- Multi-row VALUES expansion ---------------------------------------------

# Anchored at the end of the statement so a VALUES appearing earlier (in a
# comment, say) cannot be mistaken for the real one.
_VALUES_RE = re.compile(r"\bVALUES\b\s*(\(\s*\?[^()]*\))\s*;?\s*$", re.IGNORECASE | re.DOTALL)


def expand_values_clause(sql: str, n_rows: int, row_width: int | None = None) -> str:
    """Repeat a single-row ``VALUES (?, ?)`` tuple ``n_rows`` times.

    Pure string work, kept here rather than in a backend branch so that one
    unit test covers what both backends execute.
    """
    if n_rows < 1:
        raise ValueError("n_rows must be at least 1")

    statement = sql.strip()
    match = _VALUES_RE.search(statement)
    if match is None:
        raise ValueError(
            "execute_many needs a statement ending in a single VALUES tuple of "
            "? placeholders, e.g. 'INSERT INTO T (A, B) VALUES (?, ?)'."
        )

    tuple_sql = match.group(1)
    placeholders = tuple_sql.count("?")
    if row_width is not None and placeholders != row_width:
        raise ValueError(
            f"statement has {placeholders} placeholders but rows have {row_width} values"
        )

    head = statement[: match.start(1)]
    return head + ", ".join([tuple_sql] * n_rows)


# --- Testing hook ------------------------------------------------------------


def _reset_for_tests() -> None:
    """Drop all cached backend state. Used by tests only."""
    global _backend, _sis_session
    _discard_connection()
    with _init_lock:
        _backend = None
        _sis_session = None
