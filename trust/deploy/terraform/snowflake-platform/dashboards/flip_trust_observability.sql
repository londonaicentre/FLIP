-- Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
--     http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.
--
-- FLIP trust node on Snowflake: the observability dashboard (FLIP#1584).
--
-- Snowsight dashboards have no API and no Terraform resource, so the dashboard is kept here as
-- the SQL of its tiles. Each tile is one statement between a "-- TILE:" marker and the next;
-- README.md in this directory says how to paste them in, and how to run the whole file as a
-- worksheet instead if you would rather not build the dashboard at all.
--
-- Run as FLIP_<CODE>_OBSERVER with warehouse FLIP_<CODE>_WH. Every tile reads a view in
-- FLIP_PLATFORM.OBS and nothing else: no tile touches the event table or SNOWFLAKE.ACCOUNT_USAGE
-- directly, which is why the observer role needs no privilege on either.

USE ROLE FLIP_<CODE>_OBSERVER;
USE WAREHOUSE FLIP_<CODE>_WH;
USE SCHEMA FLIP_PLATFORM.OBS;

-- TILE: Service status (table)
-- Is every service alive, and how often has it come back? SILENT with a recent FIRST_SEEN is a
-- container that died; RESTARTS climbing between refreshes is a crash loop.
SELECT
    SERVICE_NAME,
    CONTAINER_NAME,
    STATUS,
    LAST_SEEN,
    SECONDS_SINCE_LAST_EVENT,
    RESTARTS,
    ERROR_RECORDS,
    COMPUTE_POOL_NAME
FROM FLIP_PLATFORM.OBS.V_SERVICE_STATUS
ORDER BY STATUS, SERVICE_NAME, CONTAINER_NAME;

-- TILE: Raw log tail, any service (table)
-- The base view, for the question no tile anticipated. It is the only place the event table's
-- shape is written down, so an ad-hoc query starts here rather than at FLIP_PLATFORM.OBS.
SELECT
    EVENT_TIME,
    SERVICE_NAME,
    CONTAINER_NAME,
    SEVERITY,
    MESSAGE
FROM FLIP_PLATFORM.OBS.V_EVENTS
WHERE RECORD_TYPE = 'LOG'
ORDER BY EVENT_TIME DESC
LIMIT 200;

-- TILE: Restart history (table)
-- One row per container run, newest first. A run of a few seconds that repeats is a failing
-- start-up, not a restart policy doing its job.
SELECT
    SERVICE_NAME,
    CONTAINER_NAME,
    CONTAINER_RUN_ID,
    RUN_STARTED,
    RUN_SECONDS,
    ERROR_RECORDS
FROM FLIP_PLATFORM.OBS.V_SERVICE_RESTARTS
ORDER BY RUN_STARTED DESC
LIMIT 200;

-- TILE: Errors in the last 24 hours, by service (bar chart)
SELECT
    SERVICE_NAME,
    COUNT(*) AS ERRORS
FROM FLIP_PLATFORM.OBS.V_RECENT_ERRORS
GROUP BY ALL
ORDER BY ERRORS DESC;

-- TILE: Recent errors (table)
SELECT
    EVENT_TIME,
    SERVICE_NAME,
    CONTAINER_NAME,
    SEVERITY,
    MESSAGE
FROM FLIP_PLATFORM.OBS.V_RECENT_ERRORS
ORDER BY EVENT_TIME DESC
LIMIT 200;

-- TILE: fl-client jobs (table)
-- One row per job the FL client joined, with its log volume and whether it logged an error.
SELECT
    JOB_ID,
    SERVICE_NAME,
    MIN(EVENT_TIME)                          AS JOB_STARTED,
    MAX(EVENT_TIME)                          AS JOB_LAST_LOG,
    COUNT(*)                                 AS LOG_RECORDS,
    COUNT_IF(SEVERITY IN ('ERROR', 'FATAL')) AS ERROR_RECORDS
FROM FLIP_PLATFORM.OBS.V_FL_CLIENT_LOGS
GROUP BY ALL
ORDER BY JOB_STARTED DESC
LIMIT 100;

-- TILE: fl-client log tail (table)
-- The log of the most recent fl-client job. Change the JOB_ID filter to read an older one.
SELECT
    EVENT_TIME,
    SEVERITY,
    MESSAGE
FROM FLIP_PLATFORM.OBS.V_FL_CLIENT_LOGS
WHERE JOB_ID = (
    SELECT JOB_ID
    FROM FLIP_PLATFORM.OBS.V_FL_CLIENT_LOGS
    ORDER BY EVENT_TIME DESC
    LIMIT 1
)
ORDER BY EVENT_TIME
LIMIT 500;

-- TILE: Credits per compute pool, per day (bar chart)
-- SPCS is billed per node-second while a pool runs and resource monitors do not cover it, so this
-- is the tile that catches a pool the nightly suspend task did not reach.
SELECT
    USAGE_DATE,
    COMPUTE_POOL_NAME,
    CREDITS
FROM FLIP_PLATFORM.OBS.V_COMPUTE_POOL_COST
ORDER BY USAGE_DATE DESC, CREDITS DESC;

-- TILE: Credits per warehouse, per day (bar chart)
SELECT
    USAGE_DATE,
    WAREHOUSE_NAME,
    CREDITS
FROM FLIP_PLATFORM.OBS.V_WAREHOUSE_COST
ORDER BY USAGE_DATE DESC, CREDITS DESC;

-- TILE: Credits this month, pools and warehouses together (scorecard)
-- ACCOUNT_USAGE views lag by up to three hours, so this is a running total, not a live meter.
SELECT
    SUM(CREDITS) AS CREDITS_THIS_MONTH
FROM (
    SELECT USAGE_DATE, CREDITS FROM FLIP_PLATFORM.OBS.V_COMPUTE_POOL_COST
    UNION ALL
    SELECT USAGE_DATE, CREDITS FROM FLIP_PLATFORM.OBS.V_WAREHOUSE_COST
)
WHERE USAGE_DATE >= DATE_TRUNC('month', CURRENT_DATE());
