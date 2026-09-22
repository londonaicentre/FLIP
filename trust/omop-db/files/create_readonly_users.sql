-- Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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
-- =============================================================================
-- PostgreSQL Read-Only User Management Script for FLIP OMOP Database
-- =============================================================================
-- This script creates read-only users with SELECT-only permissions for the
-- OMOP database following security best practices.
--
-- This script is invoked by create_readonly_users.sh, which passes the
-- data analyst password as a psql variable (-v data_analyst_password=...).
-- Running it directly with psql requires the same -v flag.
--
-- It is the ONE definition of the data_analyst_reader grants for every
-- deployment path. The Compose trust runs it once, from
-- /docker-entrypoint-initdb.d at first initdb; the Kubernetes trust chart runs
-- the copy the image ships at /flip/omop/create_readonly_users.sql from its
-- omop-db postStart hook on EVERY pod start, because a PVC restored from a
-- pgdata snapshot skips /docker-entrypoint-initdb.d altogether (FLIP#904). So
-- everything below must be safe to re-run against a cluster that already holds
-- the roles, and must converge an existing role on the same grants rather than
-- skip it — the chart's former inline copy left a role holding pg_read_all_data
-- and no base-role membership, and an upgrade has to narrow that, not merely
-- stop widening it.
-- =============================================================================
-- Create read-only role template (reusable for multiple users)
DO $$
BEGIN
    -- Create base read-only role if it doesn't exist
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'omop_readonly_base') THEN
        CREATE ROLE omop_readonly_base;
        
        -- Grant basic database connection using current database
        EXECUTE format('GRANT CONNECT ON DATABASE %I TO omop_readonly_base', current_database());
        
        -- Grant schema usage
        GRANT USAGE ON SCHEMA omop TO omop_readonly_base;
        
        -- Grant SELECT on all existing tables
        GRANT SELECT ON ALL TABLES IN SCHEMA omop TO omop_readonly_base;
        
        -- Grant SELECT on all existing sequences (for ID columns, pagination)
        GRANT SELECT ON ALL SEQUENCES IN SCHEMA omop TO omop_readonly_base;
        
        -- Ensure future tables are also readable
        ALTER DEFAULT PRIVILEGES IN SCHEMA omop 
            GRANT SELECT ON TABLES TO omop_readonly_base;
            
        -- Ensure future sequences are also readable
        ALTER DEFAULT PRIVILEGES IN SCHEMA omop 
            GRANT SELECT ON SEQUENCES TO omop_readonly_base;
            
        RAISE NOTICE 'Created base read-only role: omop_readonly_base for database: %', current_database();
    ELSE
        RAISE NOTICE 'Base read-only role already exists: omop_readonly_base';
    END IF;
END
$$;

-- =============================================================================
-- Create specific read-only users
-- =============================================================================

-- 1. Data Analysis User
-- The password is supplied via the psql variable :'data_analyst_password'.
-- Substitution must happen outside any $$...$$ block, since psql does not
-- interpolate variables inside dollar-quoted strings.
SELECT EXISTS (
    SELECT FROM pg_catalog.pg_roles WHERE rolname = 'data_analyst_reader'
) AS data_analyst_exists \gset

\if :data_analyst_exists
\echo 'Data analyst user already exists: data_analyst_reader'
\else
CREATE ROLE data_analyst_reader WITH
    LOGIN
    PASSWORD :'data_analyst_password';
\echo 'Created data analyst read-only user: data_analyst_reader'
\endif

-- Applied on every run, not only at creation, so a role that already existed —
-- restored with a snapshot, or created by an older provisioning path without
-- them — converges on the same membership and limits as a fresh one. All three
-- are idempotent: re-granting a membership is a NOTICE, not an error.
GRANT omop_readonly_base TO data_analyst_reader;
ALTER ROLE data_analyst_reader CONNECTION LIMIT 5;
ALTER ROLE data_analyst_reader SET statement_timeout = '300s';

-- =============================================================================
-- Revoke dangerous permissions explicitly (defense in depth)
-- =============================================================================

-- Ensure read-only users cannot perform dangerous operations
DO $$
DECLARE
    readonly_user TEXT;
    current_db TEXT;
BEGIN
    -- Get current database name
    SELECT current_database() INTO current_db;
    
    FOR readonly_user IN 
        SELECT rolname FROM pg_roles 
        WHERE rolname IN ('data_analyst_reader', 'omop_readonly_base')
    LOOP
        -- Revoke schema modification rights
        EXECUTE format('REVOKE CREATE ON SCHEMA omop FROM %I', readonly_user);
        EXECUTE format('REVOKE CREATE ON DATABASE %I FROM %I', current_db, readonly_user);
        
        -- Revoke table modification rights (should not be needed, but explicit is better)
        EXECUTE format('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA omop FROM %I', readonly_user);

        -- Revoke membership of the predefined all-schemas read role (FLIP#904). It
        -- carries SELECT on every table in every schema, present and future — exactly
        -- the scope the omop-only grant above exists to withhold — and was only ever
        -- granted by the Kubernetes chart's former inline copy of this provisioning.
        -- Guarded on direct membership so a role that never held it stays silent
        -- (an unconditional REVOKE would emit a WARNING on every first init).
        IF EXISTS (
            SELECT FROM pg_auth_members am
            JOIN pg_roles granted ON granted.oid = am.roleid
            JOIN pg_roles member ON member.oid = am.member
            WHERE granted.rolname = 'pg_read_all_data' AND member.rolname = readonly_user
        ) THEN
            EXECUTE format('REVOKE pg_read_all_data FROM %I', readonly_user);
            RAISE NOTICE 'Revoked pg_read_all_data from %', readonly_user;
        END IF;

        RAISE NOTICE 'Ensured read-only restrictions for user: %', readonly_user;
    END LOOP;
END
$$;

-- =============================================================================
-- Verification queries
-- =============================================================================

-- List all roles and their permissions
SELECT 
    r.rolname as role_name,
    r.rolcanlogin as can_login,
    r.rolconnlimit as connection_limit,
    array_agg(m.rolname) as member_of
FROM pg_roles r
LEFT JOIN pg_auth_members am ON r.oid = am.member
LEFT JOIN pg_roles m ON am.roleid = m.oid
WHERE r.rolname IN ('omop_readonly_base', 'data_analyst_reader')
GROUP BY r.rolname, r.rolcanlogin, r.rolconnlimit
ORDER BY r.rolname;


-- =============================================================================
-- Security Notes:
-- =============================================================================
-- 1. Passwords are injected from the environment (no defaults in this script);
--    rotate with ALTER ROLE + update the consuming service's env
-- 2. Consider using certificate-based authentication for production
-- 3. Set up connection pooling to limit resource usage
-- 4. Monitor query performance and adjust timeouts as needed
-- 5. Regularly audit user permissions and access patterns
-- 6. Consider row-level security (RLS) for additional data protection
-- =============================================================================
