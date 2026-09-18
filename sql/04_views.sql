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

-- The remaining views exist so the app never needs SELECT on a write table.
-- Snowflake views run with the definer's rights, so granting SELECT on these
-- to the service role exposes exactly these columns and nothing else. Every
-- read in app/queries.py goes through a view or through NUMBERS.

-- One row, for the results-page confidence footer.
CREATE OR REPLACE VIEW V_COLLECTION_TOTALS
COMMENT = 'Single-row collection progress summary for the results footer.'
AS
SELECT
    (SELECT COUNT(*) FROM COMPARISONS)                        AS N_COMPARISONS,
    (SELECT COUNT(*) FROM RESPONDENTS)                        AS N_RESPONDENTS,
    (SELECT COUNT(DISTINCT RESPONDENT_ID) FROM COMPARISONS)   AS N_RESPONDENTS_COMPARING,
    (SELECT COUNT(*) FROM SURVEY_RESPONSES)                   AS N_SURVEYS_SUBMITTED,
    (SELECT COUNT(*) FROM V_PAIR_COUNTS WHERE N_COMPARISONS > 0) AS N_PAIRS_COVERED,
    (SELECT COUNT(*) FROM V_PAIR_COUNTS)                      AS N_PAIRS_TOTAL;

-- Feeds the random-number analysis: are "random" picks drawn toward round
-- numbers, and did a respondent's pick shift after taking the test? LEFT JOIN
-- because the intro row is written on intro submit and the survey row may
-- never arrive.
CREATE OR REPLACE VIEW V_RANDOM_NUMBER_PICKS
COMMENT = 'Intro and closing number questions, one row per respondent.'
AS
SELECT
    i.RESPONDENT_ID,
    i.RANDOM_NUMBER,
    i.UNIQUE_GUESS_NUMBER,
    s.POST_RANDOM_NUMBER,
    s.LEAST_ROUND_NUMBER,
    i.SUBMITTED_AT AS INTRO_SUBMITTED_AT
FROM INTRO_RESPONSES i
LEFT JOIN SURVEY_RESPONSES s
       ON s.RESPONDENT_ID = i.RESPONDENT_ID;

-- Free text only. The results page renders these as text, never as markdown or
-- HTML: they are written by one anonymous respondent and shown to others.
CREATE OR REPLACE VIEW V_SURVEY_FREE_TEXT
COMMENT = 'Survey free-text answers. Render as text, never as markdown/HTML.'
AS
SELECT
    RESPONDENT_ID,
    ROUNDNESS_DEFINITION,
    INTUITION_FACTORS,
    SUBMITTED_AT
FROM SURVEY_RESPONSES
WHERE ROUNDNESS_DEFINITION IS NOT NULL
   OR INTUITION_FACTORS IS NOT NULL;

-- Empty until 06_ai_classify_survey.sql has been hand-run, which is the signal
-- for the results page to hide the classification panel entirely.
CREATE OR REPLACE VIEW V_SURVEY_CLASSIFICATION_COUNTS
COMMENT = 'Respondent counts per AI_CLASSIFY category.'
AS
SELECT
    CLASSIFICATION,
    COUNT(*) AS N_RESPONDENTS
FROM SURVEY_CLASSIFICATIONS
WHERE CLASSIFICATION IS NOT NULL
GROUP BY CLASSIFICATION;
