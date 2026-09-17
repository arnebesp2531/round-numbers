-- 04_views.sql
-- Everything derived from the COMPARISONS fact table. CREATE OR REPLACE rather
-- than IF NOT EXISTS so re-running actually picks up an edited definition;
-- views hold no data, so replacing one loses nothing.

USE DATABASE ROUNDNESS_LAB;
USE SCHEMA APP;

-- Canonical pair key plus a left-side-won flag. LEFT_WON should hover near 50%
-- overall; a strong skew means position bias is contaminating the results.
CREATE OR REPLACE VIEW V_COMPARISONS_ENRICHED
COMMENT = 'COMPARISONS with a canonical (low, high) pair key and a position-bias flag.'
AS
SELECT
    c.COMPARISON_ID,
    c.RESPONDENT_ID,
    c.LEFT_NUMBER,
    c.RIGHT_NUMBER,
    c.WINNER_NUMBER,
    c.LOSER_NUMBER,
    LEAST(c.LEFT_NUMBER, c.RIGHT_NUMBER)    AS PAIR_LOW,
    GREATEST(c.LEFT_NUMBER, c.RIGHT_NUMBER) AS PAIR_HIGH,
    IFF(c.WINNER_NUMBER = c.LEFT_NUMBER, TRUE, FALSE) AS LEFT_WON,
    c.PAGE_NUMBER,
    c.POSITION_IN_PAGE,
    c.RESPONSE_MS,
    c.SUBMITTED_AT
FROM COMPARISONS c;

-- All 4,950 pairs, including never-shown ones (N_COMPARISONS = 0). Drives both
-- coverage-weighted sampling and the coverage heatmap, so the LEFT JOIN and the
-- COALESCE matter: a pair missing from this view would never be sampled.
CREATE OR REPLACE VIEW V_PAIR_COUNTS
COMMENT = 'Every unordered pair of numbers with its comparison and per-side win counts.'
AS
WITH all_pairs AS (
    SELECT
        lo.NUMBER_VALUE AS PAIR_LOW,
        hi.NUMBER_VALUE AS PAIR_HIGH
    FROM NUMBERS lo
    JOIN NUMBERS hi
      ON lo.NUMBER_VALUE < hi.NUMBER_VALUE
),
tallied AS (
    SELECT
        PAIR_LOW,
        PAIR_HIGH,
        COUNT(*)                                        AS N_COMPARISONS,
        COUNT_IF(WINNER_NUMBER = PAIR_LOW)              AS LOW_WINS,
        COUNT_IF(WINNER_NUMBER = PAIR_HIGH)             AS HIGH_WINS
    FROM V_COMPARISONS_ENRICHED
    GROUP BY PAIR_LOW, PAIR_HIGH
)
SELECT
    p.PAIR_LOW,
    p.PAIR_HIGH,
    COALESCE(t.N_COMPARISONS, 0) AS N_COMPARISONS,
    COALESCE(t.LOW_WINS, 0)      AS LOW_WINS,
    COALESCE(t.HIGH_WINS, 0)     AS HIGH_WINS
FROM all_pairs p
LEFT JOIN tallied t
       ON t.PAIR_LOW = p.PAIR_LOW
      AND t.PAIR_HIGH = p.PAIR_HIGH;

-- Raw win% per number. The sanity-check metric shown alongside Bradley-Terry,
-- not the headline: it is biased by which opponents a number happened to face.
CREATE OR REPLACE VIEW V_NUMBER_STATS
COMMENT = 'Per-number comparison, win and loss counts with raw win percentage.'
AS
WITH wins AS (
    SELECT WINNER_NUMBER AS NUMBER_VALUE, COUNT(*) AS N
    FROM COMPARISONS
    GROUP BY WINNER_NUMBER
),
losses AS (
    SELECT LOSER_NUMBER AS NUMBER_VALUE, COUNT(*) AS N
    FROM COMPARISONS
    GROUP BY LOSER_NUMBER
)
SELECT
    n.NUMBER_VALUE,
    COALESCE(w.N, 0) + COALESCE(l.N, 0) AS N_COMPARISONS,
    COALESCE(w.N, 0)                    AS N_WINS,
    COALESCE(l.N, 0)                    AS N_LOSSES,
    IFF(COALESCE(w.N, 0) + COALESCE(l.N, 0) = 0,
        NULL,
        COALESCE(w.N, 0) / (COALESCE(w.N, 0) + COALESCE(l.N, 0))) AS WIN_PCT
FROM NUMBERS n
LEFT JOIN wins   w ON w.NUMBER_VALUE = n.NUMBER_VALUE
LEFT JOIN losses l ON l.NUMBER_VALUE = n.NUMBER_VALUE;

-- Each respondent's comparisons sum to weight 1.0, so a heavy user's personal
-- definition of roundness cannot outvote everyone else's.
CREATE OR REPLACE VIEW V_RESPONDENT_WEIGHTS
COMMENT = 'Per-respondent comparison weight: 1.0 / their comparison count.'
AS
SELECT
    RESPONDENT_ID,
    COUNT(*)         AS N_COMPARISONS,
    1.0 / COUNT(*)   AS WEIGHT
FROM COMPARISONS
GROUP BY RESPONDENT_ID;
