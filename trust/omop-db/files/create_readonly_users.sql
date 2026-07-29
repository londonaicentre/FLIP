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
    PASSWORD :'data_analyst_password'
    CONNECTION LIMIT 5;
GRANT omop_readonly_base TO data_analyst_reader;
ALTER ROLE data_analyst_reader SET statement_timeout = '300s';
\echo 'Created data analyst read-only user: data_analyst_reader'
\endif

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
-- 1. Change all default passwords immediately after running this script
-- 2. Consider using certificate-based authentication for production
-- 3. Set up connection pooling to limit resource usage
-- 4. Monitor query performance and adjust timeouts as needed
-- 5. Regularly audit user permissions and access patterns
-- 6. Consider row-level security (RLS) for additional data protection
-- =============================================================================
