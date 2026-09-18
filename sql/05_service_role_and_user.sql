-- 05_service_role_and_user.sql
-- The least-privilege identity the public app connects as.
--
-- Run by hand in Snowsight as a role that can create users and roles
-- (SECURITYADMIN or ACCOUNTADMIN), after 01-04. Safely re-runnable.
--
-- The threat being designed against is specific: the repo is public and the
-- app is internet-facing, so the realistic worst case is the private key
-- leaking. This role is shaped so that the worst that buys an attacker is junk
-- rows in a survey nobody is being paid for:
--
--   * INSERT only, on the four write tables. No UPDATE, no DELETE, no TRUNCATE,
--     no DDL. Data can be added but never altered or destroyed.
--   * No SELECT on any table it writes to. Every read the app makes goes
--     through a view, and Snowflake views run with the definer's rights, so
--     the role reads exactly the columns the views expose -- not raw
--     COMPARISONS, and not SURVEY_RESPONSES.
--   * One XS warehouse, nothing else in the account.
--
-- Streamlit in Snowflake does not use this identity at all: there the app runs
-- as whoever opened it, which is you.

USE ROLE SECURITYADMIN;

CREATE ROLE IF NOT EXISTS ROUNDNESS_APP_ROLE
    COMMENT = 'Least-privilege role for the public roundness survey app.';

-- TYPE = SERVICE is what makes this a machine identity: it cannot be given a
-- password, cannot log in to Snowsight, and is exempt from the MFA policies
-- that apply to people. Key-pair (or a PAT) is the only way in, which matches
-- Snowflake's deprecation of single-factor password auth for programmatic use.
CREATE USER IF NOT EXISTS ROUNDNESS_APP_SVC
    TYPE = SERVICE
    DEFAULT_ROLE = ROUNDNESS_APP_ROLE
    DEFAULT_WAREHOUSE = ROUNDNESS_WH
    COMMENT = 'Service user for the Streamlit Community Cloud survey app.';

GRANT ROLE ROUNDNESS_APP_ROLE TO USER ROUNDNESS_APP_SVC;

-- Register the public key. Generate the pair locally -- the private half never
-- goes anywhere near Snowflake or this repo:
--
--   openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
--   openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
--
-- Paste the BODY of rsa_key.pub below: everything between the BEGIN and END
-- lines, with the newlines stripped out. The private key (the whole PEM file,
-- BEGIN and END lines included) goes into Community Cloud's secrets manager as
-- `private_key`, and nowhere else.
--
-- ALTER USER ROUNDNESS_APP_SVC SET RSA_PUBLIC_KEY = 'MIIBIjANBgkqh...';
--
-- To rotate without downtime: set RSA_PUBLIC_KEY_2 to the new key, swap the
-- secret in Community Cloud, confirm the app still works, then UNSET
-- RSA_PUBLIC_KEY and promote the new one back into the first slot.


-- The grants themselves are the owner's to make.
USE ROLE SYSADMIN;
USE DATABASE ROUNDNESS_LAB;
USE SCHEMA APP;

GRANT USAGE ON WAREHOUSE ROUNDNESS_WH      TO ROLE ROUNDNESS_APP_ROLE;
GRANT USAGE ON DATABASE ROUNDNESS_LAB      TO ROLE ROUNDNESS_APP_ROLE;
GRANT USAGE ON SCHEMA ROUNDNESS_LAB.APP    TO ROLE ROUNDNESS_APP_ROLE;

-- Writes: the four tables the app inserts into, and nothing more.
GRANT INSERT ON TABLE RESPONDENTS        TO ROLE ROUNDNESS_APP_ROLE;
GRANT INSERT ON TABLE INTRO_RESPONSES    TO ROLE ROUNDNESS_APP_ROLE;
GRANT INSERT ON TABLE COMPARISONS        TO ROLE ROUNDNESS_APP_ROLE;
GRANT INSERT ON TABLE SURVEY_RESPONSES   TO ROLE ROUNDNESS_APP_ROLE;

-- Reads: the reference table, plus every view in app/queries.py. Note what is
-- absent -- no SELECT on the four tables above, and none on
-- SURVEY_CLASSIFICATIONS, which the app only ever reads through its view.
GRANT SELECT ON TABLE NUMBERS                        TO ROLE ROUNDNESS_APP_ROLE;
GRANT SELECT ON VIEW  V_PAIR_COUNTS                  TO ROLE ROUNDNESS_APP_ROLE;
GRANT SELECT ON VIEW  V_NUMBER_STATS                 TO ROLE ROUNDNESS_APP_ROLE;
GRANT SELECT ON VIEW  V_RESPONDENT_WEIGHTS           TO ROLE ROUNDNESS_APP_ROLE;
GRANT SELECT ON VIEW  V_COMPARISONS_ENRICHED         TO ROLE ROUNDNESS_APP_ROLE;
GRANT SELECT ON VIEW  V_COLLECTION_TOTALS            TO ROLE ROUNDNESS_APP_ROLE;
GRANT SELECT ON VIEW  V_RANDOM_NUMBER_PICKS          TO ROLE ROUNDNESS_APP_ROLE;
GRANT SELECT ON VIEW  V_SURVEY_FREE_TEXT             TO ROLE ROUNDNESS_APP_ROLE;
GRANT SELECT ON VIEW  V_SURVEY_CLASSIFICATION_COUNTS TO ROLE ROUNDNESS_APP_ROLE;


-- --- Verification ------------------------------------------------------------
-- The pre-deploy gate is "confirm the service role genuinely cannot DELETE".
-- Confirm it, do not assume it.
--
-- ACCOUNTADMIN cannot USE ROLE a custom role it has not been granted -- it
-- inherits SYSADMIN and SECURITYADMIN, not roles you create. Grant it to
-- yourself first, or the block below fails with "Requested role
-- 'ROUNDNESS_APP_ROLE' is not assigned to the executing user":
--
--   USE ROLE SECURITYADMIN;
--   GRANT ROLE ROUNDNESS_APP_ROLE TO USER IDENTIFIER(CURRENT_USER());
--
-- Then run the checks as the service role itself:
--
--   USE ROLE ROUNDNESS_APP_ROLE;
--   USE WAREHOUSE ROUNDNESS_WH;
--   USE DATABASE ROUNDNESS_LAB;
--   USE SCHEMA APP;
--
--   SELECT COUNT(*) FROM V_PAIR_COUNTS;   -- expect 4950
--   SELECT COUNT(*) FROM NUMBERS;         -- expect 100
--   SELECT COUNT(*) FROM COMPARISONS;     -- expect: insufficient privileges
--   DELETE FROM COMPARISONS WHERE 1 = 0;  -- expect: insufficient privileges
--   UPDATE COMPARISONS SET PAGE_NUMBER = PAGE_NUMBER;  -- expect: insufficient privileges
--
-- The two SELECTs must succeed and the last three must fail. A failure on the
-- first two means a missing grant; a success on the last three means this
-- script has not been run as written, and the key must not be deployed.
--
-- The grant to yourself was only so you could impersonate the role. Give it
-- back once the checks pass, so the role stays a machine identity:
--
--   USE ROLE SECURITYADMIN;
--   REVOKE ROLE ROUNDNESS_APP_ROLE FROM USER IDENTIFIER(CURRENT_USER());
--
-- The full end-to-end check, once the key is in .streamlit/secrets.toml, is:
--
--   python scripts/smoke_test_connection.py --write
--
-- which exercises every read and the batched INSERT as the service user, and
-- prints the cleanup SQL for the rows it leaves behind. Those rows have to be
-- deleted as the owner -- which is the point.
--
-- To review what this role actually holds:
--
--   SHOW GRANTS TO ROLE ROUNDNESS_APP_ROLE;
