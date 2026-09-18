-- 06_ai_classify_survey.sql
-- Sorts the survey free text into the camps the experiment is adjudicating
-- between, using Snowflake Cortex AI_CLASSIFY.
--
-- Run this by hand in Snowsight, as the schema owner, whenever new surveys have
-- come in. It is deliberately out-of-band: the public app never calls Cortex.
-- Doing it in-app would add seconds of latency to a page submit, require Cortex
-- grants on the service role, and break outright in a region where AI_CLASSIFY
-- is not enabled. The results page reads SURVEY_CLASSIFICATIONS and hides the
-- panel entirely while it is empty, so not running this degrades gracefully.
--
-- Re-runnable: the anti-join classifies only respondents who do not already
-- have a row, so a second run costs nothing and never duplicates.

USE DATABASE ROUNDNESS_LAB;
USE SCHEMA APP;
USE WAREHOUSE ROUNDNESS_WH;

-- The categories mirror the four hypotheses, plus the two things respondents
-- actually say that none of the hypotheses predicted (time/clock, and digits as
-- shapes on the page). Descriptions matter more than labels here -- AI_CLASSIFY
-- reads them -- but the labels are what lands in the table, so they stay short
-- enough for SURVEY_CLASSIFICATIONS.CLASSIFICATION (VARCHAR(64)) and stable
-- enough to group on.
INSERT INTO SURVEY_CLASSIFICATIONS (RESPONDENT_ID, CLASSIFICATION)
WITH unclassified AS (
    SELECT
        s.RESPONDENT_ID,
        -- Both free-text answers together: a respondent often defines roundness
        -- in one box and explains their reaction in the other.
        TRIM(
            COALESCE(s.ROUNDNESS_DEFINITION, '') || '\n\n' ||
            COALESCE(s.INTUITION_FACTORS, '')
        ) AS FREE_TEXT
    FROM SURVEY_RESPONSES s
    LEFT JOIN SURVEY_CLASSIFICATIONS c
           ON c.RESPONDENT_ID = s.RESPONDENT_ID
    WHERE c.RESPONDENT_ID IS NULL
      AND (s.ROUNDNESS_DEFINITION IS NOT NULL OR s.INTUITION_FACTORS IS NOT NULL)
),
classified AS (
    SELECT
        RESPONDENT_ID,
        AI_CLASSIFY(
            FREE_TEXT,
            [
                {
                    'label': 'money',
                    'description': 'Roundness explained through money: coins, bills, prices, change, paying for something, or amounts that are easy to hand over.'
                },
                {
                    'label': 'time',
                    'description': 'Roundness explained through time: clocks, minutes, quarter and half hours, schedules, or counting in fifteens and thirties.'
                },
                {
                    'label': 'factors',
                    'description': 'Roundness explained through arithmetic structure: divisibility, factors, multiples, primes, squares, or how evenly a number splits.'
                },
                {
                    'label': 'landmark',
                    'description': 'Roundness explained by nearness to a big landmark number such as 10, 25, 50 or 100, or by a number being pulled toward or overshadowed by one.'
                },
                {
                    'label': 'roman',
                    'description': 'Roundness explained through Roman numerals, or through how short or simple the number is to write down in any notation.'
                },
                {
                    'label': 'digits',
                    'description': 'Roundness explained by the look of the written digits themselves: trailing zeros, curved shapes, how the number reads aloud, or how tidy it appears.'
                },
                {
                    'label': 'other',
                    'description': 'No explanation given, pure gut feeling with no reason offered, or a reason that fits none of the other categories.'
                }
            ]
        ) AS RESULT
    FROM unclassified
    WHERE LENGTH(FREE_TEXT) > 0
)
SELECT
    RESPONDENT_ID,
    -- AI_CLASSIFY returns an OBJECT like { "labels": ["money"] }. Taking the
    -- first label keeps one row per respondent, which is what the results
    -- panel's counts assume.
    COALESCE(RESULT:labels[0]::VARCHAR, 'other') AS CLASSIFICATION
FROM classified;


-- Check what landed:
--
--   SELECT CLASSIFICATION, COUNT(*) AS N_RESPONDENTS
--   FROM SURVEY_CLASSIFICATIONS
--   GROUP BY CLASSIFICATION
--   ORDER BY N_RESPONDENTS DESC;
--
-- Or read the classifications next to the text they came from, which is the
-- honest way to judge whether the categories are doing their job:
--
--   SELECT c.CLASSIFICATION, s.ROUNDNESS_DEFINITION, s.INTUITION_FACTORS
--   FROM SURVEY_CLASSIFICATIONS c
--   JOIN SURVEY_RESPONSES s ON s.RESPONDENT_ID = c.RESPONDENT_ID
--   ORDER BY c.CLASSIFIED_AT DESC
--   LIMIT 50;
--
-- To reclassify everything after editing the categories above, clear the table
-- first and re-run this script. The app tolerates an empty table, so the gap
-- between the two statements is harmless:
--
--   DELETE FROM SURVEY_CLASSIFICATIONS;
--
-- To run it on a schedule instead of by hand, wrap the INSERT in a task owned
-- by a role with Cortex access. Left commented out because a task on an XS
-- warehouse that wakes for nothing most days is a worse trade than running
-- this when you actually want to look at the results:
--
--   CREATE OR REPLACE TASK CLASSIFY_SURVEYS
--       WAREHOUSE = ROUNDNESS_WH
--       SCHEDULE = 'USING CRON 0 6 * * * UTC'
--   AS
--   <the INSERT above>;
--   ALTER TASK CLASSIFY_SURVEYS RESUME;
