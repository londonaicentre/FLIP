-- Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--     http://www.apache.org/licenses/LICENSE-2.0
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.
--
-- =====================================================================================
-- FLIP data-access-api: read-only Snowflake role for a trust's OMOP schema (FLIP#13)
-- =====================================================================================
--
-- The Snowflake equivalent of trust/omop-db/files/create_readonly_users.sql, which creates
-- the local Postgres role data_analyst_reader with SELECT only on the omop schema, explicit
-- REVOKEs of every write and DDL privilege, and a 300 s statement timeout.
--
-- FLIP CANNOT CREATE THIS ROLE. The trust owns its Snowflake account, so this script is run
-- by the TRUST'S OWN SNOWFLAKE ADMIN and is an onboarding prerequisite exactly as the
-- Postgres role is today: data-access-api will not read a Snowflake OMOP until it exists.
--
-- What it guarantees, and what data-access-api relies on:
--   * SELECT only. No INSERT / UPDATE / DELETE / TRUNCATE / MERGE, and no CREATE of any
--     object type, is ever granted — so a write that slips past validate_query is refused by
--     Snowflake itself, which is the same defence-in-depth posture as the Postgres path.
--   * The OMOP schema only. USAGE is granted on the database and the OMOP schema and on
--     nothing else, so the service's `omop` schema pin is backed by an actual grant rather
--     than being the only barrier.
--   * A statement timeout, so a pathological cohort query cannot run up unbounded warehouse
--     cost. Set on BOTH the warehouse and the role: the lower of the two applies, and a role
--     reused on another warehouse keeps its own ceiling.
--
-- Object names follow the FLIP Snowflake naming contract (epic #1589):
--   role       FLIP_<CODE>_OMOP_READER
--   database   FLIP_TRUST_<CODE>
--   schema     OMOP
--   warehouse  FLIP_<CODE>_WH
-- Replace <CODE> with the trust code from the FLIP kit (KIT=<CODE>), upper-cased, e.g. GSTT.
-- SET statements below do that in one place.
--
-- Run as a role that can grant on these objects: ACCOUNTADMIN, or SECURITYADMIN for the role
-- itself. This is the standalone path, for an account not managed by FLIP's Terraform roots.
-- Where those roots are used, the break-glass platform root
-- (trust/deploy/terraform/snowflake-platform) already creates FLIP_<CODE>_OMOP_READER and the
-- workload root grants it on the OMOP schema, so none of this is needed. FLIP_<CODE>_ADMIN is
-- retired (two-tier decision, epic #1589).
--
-- Usage:
--   snowsql -f snowflake_readonly_grants.sql -D code=GSTT
-- or paste into a worksheet after editing the SET below.
-- =====================================================================================

SET trust_code = 'GSTT';  -- <<< EDIT: the trust code from the FLIP kit, upper-cased

SET reader_role = 'FLIP_' || $trust_code || '_OMOP_READER';
SET omop_database = 'FLIP_TRUST_' || $trust_code;
SET omop_schema = $omop_database || '.OMOP';
SET omop_warehouse = 'FLIP_' || $trust_code || '_WH';

-- The number of seconds a single cohort query may run. Mirrors the Postgres role's
-- statement_timeout = 300s. Raise it deliberately, never remove it: on a billed warehouse an
-- unbounded statement is unbounded cost, and FLIP re-runs the cohort query live at every
-- stage (the imaging status poll alone re-runs it roughly every 10 s while a project page is
-- open — FLIP#857).
SET statement_timeout_seconds = 300;

USE ROLE SECURITYADMIN;

-- ── 1. The role ──────────────────────────────────────────────────────────────────────
-- A role, not a user: on SPCS the DATA_ACCESS_API service runs AS this role through the
-- mounted OAuth token and holds no credential of its own. Off SPCS the role is granted to
-- the service user that authenticates with key-pair auth (step 5).
CREATE ROLE IF NOT EXISTS IDENTIFIER($reader_role)
  COMMENT = 'FLIP data-access-api: SELECT-only on the OMOP schema. No write, no DDL.';

USE ROLE ACCOUNTADMIN;

-- ── 2. Compute ───────────────────────────────────────────────────────────────────────
-- USAGE only. OPERATE is deliberately NOT granted: the service must not be able to resize,
-- resume-on-demand beyond its own query, or suspend the trust's warehouse.
GRANT USAGE ON WAREHOUSE IDENTIFIER($omop_warehouse) TO ROLE IDENTIFIER($reader_role);

-- Warehouse-level ceiling. Applies to every statement on this warehouse whoever runs it.
ALTER WAREHOUSE IDENTIFIER($omop_warehouse)
  SET STATEMENT_TIMEOUT_IN_SECONDS = $statement_timeout_seconds;

-- ── 3. Read scope: the OMOP schema and nothing else ──────────────────────────────────
GRANT USAGE ON DATABASE IDENTIFIER($omop_database) TO ROLE IDENTIFIER($reader_role);
GRANT USAGE ON SCHEMA IDENTIFIER($omop_schema) TO ROLE IDENTIFIER($reader_role);

GRANT SELECT ON ALL TABLES IN SCHEMA IDENTIFIER($omop_schema) TO ROLE IDENTIFIER($reader_role);
GRANT SELECT ON ALL VIEWS IN SCHEMA IDENTIFIER($omop_schema) TO ROLE IDENTIFIER($reader_role);

-- Future grants, so a table added by a later OMOP load is readable without re-running this
-- script — and, just as importantly, so nobody is tempted to grant something broader in order
-- to avoid re-running it.
GRANT SELECT ON FUTURE TABLES IN SCHEMA IDENTIFIER($omop_schema) TO ROLE IDENTIFIER($reader_role);
GRANT SELECT ON FUTURE VIEWS IN SCHEMA IDENTIFIER($omop_schema) TO ROLE IDENTIFIER($reader_role);

-- Snowflake allows STATEMENT_TIMEOUT_IN_SECONDS on the account, a user, a session and a
-- warehouse, but NOT on a role, so the warehouse grant above is where the ceiling lives for
-- an SPCS deployment (which has no user). Off SPCS, set it on the service user too — see
-- section 5 — so the limit still applies if the role is ever used on another warehouse.

-- ── 4. What is deliberately NOT granted ──────────────────────────────────────────────
-- Listed rather than REVOKEd because Snowflake grants nothing by default: there is no
-- PUBLIC-schema equivalent of the Postgres grants this file's Postgres sibling has to revoke.
-- Keep this list in the script so a future edit has to argue with it:
--   * INSERT / UPDATE / DELETE / TRUNCATE on any table
--   * CREATE TABLE / VIEW / STAGE / FUNCTION / PROCEDURE / TASK in any schema
--   * OWNERSHIP of any object
--   * USAGE on any other schema of the database, including APP (service specs, secrets,
--     network rules) and STAGES (FL kit, seed data, imaging, cohort snapshots)
--   * OPERATE or MODIFY on the warehouse
--   * IMPORTED PRIVILEGES on SNOWFLAKE (the account_usage / information_schema shares)
--   * Any EXTERNAL ACCESS INTEGRATION, which is what an external function would need
--
-- The service's validate_query independently refuses the SQL-side routes to most of the
-- above (SYSTEM$ functions, RESULT_SCAN, stage references, non-OMOP schemas), so a gap in
-- either layer is covered by the other. Neither is weakened on the assumption of the other.

-- ── 5. Off-SPCS only: bind the role to a key-pair service user ────────────────────────
-- Skip this entire section when data-access-api runs ON Snowpark Container Services: the
-- service authenticates with the mounted OAuth token at /snowflake/session/token and runs as
-- the role granted to the service, so no user and no key exist to be stolen.
--
-- For a trust node reading Snowflake over the internet, create a SERVICE user with key-pair
-- auth. TYPE = SERVICE cannot have a password and cannot log into the UI, which is what makes
-- "never password auth" a property of the account rather than of FLIP's configuration.
--
-- USE ROLE USERADMIN;
-- CREATE USER IF NOT EXISTS FLIP_GSTT_DATA_ACCESS_USER
--   TYPE = SERVICE
--   DEFAULT_ROLE = FLIP_GSTT_OMOP_READER
--   DEFAULT_WAREHOUSE = FLIP_GSTT_WH
--   RSA_PUBLIC_KEY = 'MIIBIjANBgkq...'   -- the PUBLIC half only; the private key never leaves the trust node
--   STATEMENT_TIMEOUT_IN_SECONDS = 300
--   COMMENT = 'FLIP data-access-api, key-pair auth, read-only OMOP';
-- GRANT ROLE FLIP_GSTT_OMOP_READER TO USER FLIP_GSTT_DATA_ACCESS_USER;
--
-- Then in the trust kit file (trust/.env.<CODE>.<env>), Trust-local credentials section:
--   OMOP_BACKEND=snowflake
--   SNOWFLAKE_ACCOUNT=<account locator>
--   SNOWFLAKE_USER=FLIP_GSTT_DATA_ACCESS_USER
--   SNOWFLAKE_ROLE=FLIP_GSTT_OMOP_READER
--   SNOWFLAKE_WAREHOUSE=FLIP_GSTT_WH
--   SNOWFLAKE_DATABASE=FLIP_TRUST_GSTT
--   SNOWFLAKE_SCHEMA=OMOP
--   SNOWFLAKE_PRIVATE_KEY_FILE=/run/secrets/snowflake_key.pem

-- ── 6. Verify ────────────────────────────────────────────────────────────────────────
-- SHOW GRANTS TO ROLE FLIP_GSTT_OMOP_READER;
-- Expect: USAGE on the warehouse, USAGE on the database and the OMOP schema, SELECT on the
-- OMOP tables/views. Anything else on that list is a finding.
