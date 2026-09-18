"""Single source of truth for names, sizes and tuning constants.

Every other module imports from here rather than hardcoding a database name or
a magic number. Nothing in this module touches Snowflake or Streamlit, so it is
importable from scripts and tests.
"""

from __future__ import annotations

# --- Snowflake object names --------------------------------------------------
# These must match sql/01_database_and_schema.sql. Change both together.
DATABASE = "ROUNDNESS_LAB"
SCHEMA = "APP"
WAREHOUSE = "ROUNDNESS_WH"

TABLE_RESPONDENTS = "RESPONDENTS"
TABLE_INTRO_RESPONSES = "INTRO_RESPONSES"
TABLE_COMPARISONS = "COMPARISONS"
TABLE_SURVEY_RESPONSES = "SURVEY_RESPONSES"
TABLE_SURVEY_CLASSIFICATIONS = "SURVEY_CLASSIFICATIONS"
TABLE_NUMBERS = "NUMBERS"

VIEW_PAIR_COUNTS = "V_PAIR_COUNTS"
VIEW_NUMBER_STATS = "V_NUMBER_STATS"
VIEW_RESPONDENT_WEIGHTS = "V_RESPONDENT_WEIGHTS"
VIEW_COMPARISONS_ENRICHED = "V_COMPARISONS_ENRICHED"
VIEW_COLLECTION_TOTALS = "V_COLLECTION_TOTALS"
VIEW_RANDOM_NUMBER_PICKS = "V_RANDOM_NUMBER_PICKS"
VIEW_SURVEY_FREE_TEXT = "V_SURVEY_FREE_TEXT"
VIEW_SURVEY_CLASSIFICATION_COUNTS = "V_SURVEY_CLASSIFICATION_COUNTS"

# The app reads views and NUMBERS, and only ever INSERTs into the fact tables.
# Keeping every read behind a view is what lets the service role hold no SELECT
# on the write tables at all (Snowflake views run with the definer's rights).
READ_VIEWS = (
    VIEW_PAIR_COUNTS,
    VIEW_NUMBER_STATS,
    VIEW_RESPONDENT_WEIGHTS,
    VIEW_COMPARISONS_ENRICHED,
    VIEW_COLLECTION_TOTALS,
    VIEW_RANDOM_NUMBER_PICKS,
    VIEW_SURVEY_FREE_TEXT,
    VIEW_SURVEY_CLASSIFICATION_COUNTS,
)


def qualified(name: str) -> str:
    """Fully qualify an object name, e.g. ROUNDNESS_LAB.APP.COMPARISONS.

    The external backend sets a default schema on connect and SiS inherits the
    app's, but qualifying every reference removes any doubt about which schema a
    statement hit.
    """
    return f"{DATABASE}.{SCHEMA}.{name}"


# --- Experiment domain -------------------------------------------------------
MIN_NUMBER = 1
MAX_NUMBER = 100
TOTAL_PAIRS = (MAX_NUMBER - MIN_NUMBER + 1) * (MAX_NUMBER - MIN_NUMBER) // 2  # 4,950

# --- Survey flow -------------------------------------------------------------
PAIRS_PER_PAGE = 10

# Cumulative comparisons at which a respondent gets a warm "you've done plenty"
# nudge. It is a nudge only — they may keep going.
THANK_YOU_THRESHOLD = 100

# Free-text fields are length-capped before they reach the database.
MAX_FREE_TEXT_CHARS = 2000

# Per-comparison response times above this are recorded as unknown rather than
# stored: a ten-minute gap means the respondent walked away from the page, not
# that they deliberated for ten minutes, and keeping it would wreck the median.
MAX_RESPONSE_MS = 10 * 60 * 1000

# --- Pair sampling -----------------------------------------------------------
# Candidate pair weight is 1 / (n_comparisons + 1) ** SAMPLING_ALPHA.
# Higher alpha pushes harder toward the least-compared pairs; 0.0 is uniform.
SAMPLING_ALPHA = 2.0

# V_PAIR_COUNTS is only ~4,950 rows, so it is fetched whole and cached rather
# than queried per page.
PAIR_COUNTS_CACHE_TTL_SECONDS = 60

# --- Results page ------------------------------------------------------------
# The Bradley-Terry fit plus its bootstrap is the one genuinely expensive thing
# the app does, and the standings do not visibly move minute to minute. Cached
# for longer than the reads it is built from.
RESULTS_CACHE_TTL_SECONDS = 300

# --- Bradley-Terry -----------------------------------------------------------
# Virtual wins and losses added to every pair. Without this the MLE diverges on
# a disconnected comparison graph or an undefeated number. Do not set to 0.
BT_EPSILON = 0.01
BT_MAX_ITERATIONS = 1000
BT_TOLERANCE = 1e-9
BT_BOOTSTRAP_SAMPLES = 200

# --- App source tags ---------------------------------------------------------
APP_SOURCE_COMMUNITY_CLOUD = "COMMUNITY_CLOUD"
APP_SOURCE_SIS = "SIS"

# Present now so the future priming experiment needs no migration.
DEFAULT_EXPERIMENT_VARIANT = "CONTROL"
