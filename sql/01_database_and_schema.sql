-- 01_database_and_schema.sql
-- Creates the database, schema and warehouse the experiment lives in.
-- Run as a role that can create databases and warehouses (e.g. SYSADMIN).
-- Safely re-runnable: every statement uses IF NOT EXISTS.

CREATE DATABASE IF NOT EXISTS ROUNDNESS_LAB
    COMMENT = 'Number roundness experiment: pairwise comparison survey.';

CREATE SCHEMA IF NOT EXISTS ROUNDNESS_LAB.APP
    COMMENT = 'Fact tables and views backing the Streamlit app.';

-- XS is ample: the app issues one small read per minute and one batched insert
-- per page. AUTO_SUSPEND is deliberately short so an idle public app costs
-- nothing; AUTO_RESUME covers the cold start when a respondent arrives.
CREATE WAREHOUSE IF NOT EXISTS ROUNDNESS_WH
    WAREHOUSE_SIZE = 'XSMALL'
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    INITIALLY_SUSPENDED = TRUE
    COMMENT = 'Warehouse for the roundness experiment app.';

USE DATABASE ROUNDNESS_LAB;
USE SCHEMA APP;
USE WAREHOUSE ROUNDNESS_WH;
