# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# The observability views (#1584), in FLIP_PLATFORM.OBS.
#
# Why this tier and not the workload root. A view runs with its OWNER's rights, so a view over
# SNOWFLAKE.ACCOUNT_USAGE can only be owned by a role that holds IMPORTED PRIVILEGES on database
# SNOWFLAKE. The deploy role holds exactly four account privileges (grants.tf) and that is not one
# of them, by design: account-wide usage and query history is more than a routine deploy key
# should be able to read. Owning the views here also means a workload `make destroy` takes the
# trust's objects and leaves the account's observability standing, the same reason the event table
# lives here.
#
# FLIP_TRUST_<CODE>.OBS stays as it is: the deploy role owns it and the observer already holds
# USAGE plus a future SELECT on its views, so a per-trust view added later needs no new grant.
#
# Only FLIP_<CODE>_OBSERVER can read these. FLIP_<CODE>_SVC, the role every service runs as, has
# no USAGE on FLIP_PLATFORM at all, so a container cannot read the logs of its neighbours, and
# OBSERVER is granted only to FLIP_<CODE>_DEPLOY (roles.tf), never into the service hierarchy.
#
# copy_grants on every view: the provider replaces a view when its statement changes, and without
# it the grants below would be dropped between the replace and the next apply of the grant.

# The base view. Every other view reads this one, so the SPCS resource-attribute keys are written
# out once. Referencing it by fully_qualified_name is also what orders the creates.
resource "snowflake_view" "events" {
  database    = snowflake_database.platform.name
  schema      = local.platform_schemas.obs
  name        = local.obs_views.events
  copy_grants = true
  comment     = "FLIP: the account event table with the SPCS resource attributes flattened (#1584)."

  statement = <<-SQL
    SELECT
      TIMESTAMP                                                      AS EVENT_TIME,
      RECORD_TYPE,
      RESOURCE_ATTRIBUTES:"snow.database.name"::VARCHAR               AS DATABASE_NAME,
      RESOURCE_ATTRIBUTES:"snow.schema.name"::VARCHAR                 AS SCHEMA_NAME,
      RESOURCE_ATTRIBUTES:"snow.service.name"::VARCHAR                AS SERVICE_NAME,
      RESOURCE_ATTRIBUTES:"snow.service.type"::VARCHAR                AS SERVICE_TYPE,
      RESOURCE_ATTRIBUTES:"snow.service.container.name"::VARCHAR      AS CONTAINER_NAME,
      RESOURCE_ATTRIBUTES:"snow.service.container.instance"::VARCHAR  AS CONTAINER_INSTANCE,
      RESOURCE_ATTRIBUTES:"snow.service.container.run.id"::VARCHAR    AS CONTAINER_RUN_ID,
      RESOURCE_ATTRIBUTES:"snow.compute_pool.name"::VARCHAR           AS COMPUTE_POOL_NAME,
      RECORD:severity_text::VARCHAR                                   AS SEVERITY,
      "VALUE"::VARCHAR                                                AS MESSAGE
    FROM ${local.event_table_fqn}
  SQL

  depends_on = [snowflake_execute.event_table]
}

# Service status and restarts, one row per container of a service. A container that restarts gets
# a new run id, so the distinct count is the restart count; a service nobody is logging from shows
# as SILENT rather than disappearing from the list.
resource "snowflake_view" "service_status" {
  database    = snowflake_database.platform.name
  schema      = local.platform_schemas.obs
  name        = local.obs_views.service_status
  copy_grants = true
  comment     = "FLIP: per-service, per-container last-seen, restart and error counts (#1584)."

  statement = <<-SQL
    SELECT
      SERVICE_NAME,
      SERVICE_TYPE,
      DATABASE_NAME,
      SCHEMA_NAME,
      CONTAINER_NAME,
      COMPUTE_POOL_NAME,
      MIN(EVENT_TIME)                                    AS FIRST_SEEN,
      MAX(EVENT_TIME)                                    AS LAST_SEEN,
      DATEDIFF('second', MAX(EVENT_TIME), CURRENT_TIMESTAMP()) AS SECONDS_SINCE_LAST_EVENT,
      COUNT(DISTINCT CONTAINER_RUN_ID)                   AS RUNS,
      GREATEST(COUNT(DISTINCT CONTAINER_RUN_ID) - 1, 0)  AS RESTARTS,
      COUNT_IF(SEVERITY IN ('ERROR', 'FATAL'))           AS ERROR_RECORDS,
      CASE
        WHEN MAX(EVENT_TIME) >= DATEADD('minute', -15, CURRENT_TIMESTAMP()) THEN 'ACTIVE'
        ELSE 'SILENT'
      END                                                AS STATUS
    FROM ${snowflake_view.events.fully_qualified_name}
    WHERE SERVICE_NAME IS NOT NULL
    GROUP BY ALL
  SQL
}

# One row per container run, so a restart loop is visible as a run list rather than a count.
resource "snowflake_view" "service_restarts" {
  database    = snowflake_database.platform.name
  schema      = local.platform_schemas.obs
  name        = local.obs_views.service_restarts
  copy_grants = true
  comment     = "FLIP: one row per container run, newest first. A short run life means a crash loop (#1584)."

  statement = <<-SQL
    SELECT
      SERVICE_NAME,
      CONTAINER_NAME,
      CONTAINER_INSTANCE,
      CONTAINER_RUN_ID,
      MIN(EVENT_TIME)                                     AS RUN_STARTED,
      MAX(EVENT_TIME)                                     AS RUN_LAST_EVENT,
      DATEDIFF('second', MIN(EVENT_TIME), MAX(EVENT_TIME)) AS RUN_SECONDS,
      COUNT_IF(SEVERITY IN ('ERROR', 'FATAL'))            AS ERROR_RECORDS
    FROM ${snowflake_view.events.fully_qualified_name}
    WHERE SERVICE_NAME IS NOT NULL
      AND CONTAINER_RUN_ID IS NOT NULL
    GROUP BY ALL
  SQL
}

# Recent errors per service. Bounded to a window so the dashboard tile cannot scan the whole table.
resource "snowflake_view" "recent_errors" {
  database    = snowflake_database.platform.name
  schema      = local.platform_schemas.obs
  name        = local.obs_views.recent_errors
  copy_grants = true
  comment     = "FLIP: ERROR and FATAL records of the last ${var.recent_errors_window_hours} hours, per service (#1584)."

  statement = <<-SQL
    SELECT
      EVENT_TIME,
      SERVICE_NAME,
      CONTAINER_NAME,
      CONTAINER_RUN_ID,
      SEVERITY,
      MESSAGE
    FROM ${snowflake_view.events.fully_qualified_name}
    WHERE RECORD_TYPE = 'LOG'
      AND SEVERITY IN ('ERROR', 'FATAL')
      AND EVENT_TIME >= DATEADD('hour', -${var.recent_errors_window_hours}, CURRENT_TIMESTAMP())
  SQL
}

# fl-client logs, per job. The fl-client is the one service a researcher's code runs inside, so it
# gets its own view: the service name carries the FL network (the #1582 naming contract), and the
# container run id is the job, one run per training job the client joins.
resource "snowflake_view" "fl_client_logs" {
  database    = snowflake_database.platform.name
  schema      = local.platform_schemas.obs
  name        = local.obs_views.fl_client_logs
  copy_grants = true
  comment     = "FLIP: fl-client log records keyed by job (one container run per job) (#1584)."

  statement = <<-SQL
    SELECT
      CONTAINER_RUN_ID AS JOB_ID,
      SERVICE_NAME,
      EVENT_TIME,
      SEVERITY,
      MESSAGE
    FROM ${snowflake_view.events.fully_qualified_name}
    WHERE RECORD_TYPE = 'LOG'
      AND SERVICE_NAME ILIKE '${local.fl_client_service_pattern}'
  SQL
}

# Cost per compute pool. SPCS is billed per node-second while a pool runs and resource monitors do
# not cover it, so this is the tile that catches a pool somebody left up.
resource "snowflake_view" "compute_pool_cost" {
  database    = snowflake_database.platform.name
  schema      = local.platform_schemas.obs
  name        = local.obs_views.compute_pool_cost
  copy_grants = true
  comment     = "FLIP: daily credits per compute pool, from SNOWFLAKE.ACCOUNT_USAGE (#1584)."

  statement = <<-SQL
    SELECT
      DATE_TRUNC('day', START_TIME)::DATE AS USAGE_DATE,
      COMPUTE_POOL_NAME,
      INSTANCE_FAMILY,
      SUM(CREDITS_USED)                   AS CREDITS
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWPARK_CONTAINER_SERVICES_HISTORY
    WHERE START_TIME >= DATEADD('day', -${var.cost_window_days}, CURRENT_TIMESTAMP())
    GROUP BY ALL
  SQL

  depends_on = [snowflake_schema.platform]
}

# Cost per warehouse. The trust warehouse carries the resource monitor, so this is the tile that
# says how close the month is to the quota.
resource "snowflake_view" "warehouse_cost" {
  database    = snowflake_database.platform.name
  schema      = local.platform_schemas.obs
  name        = local.obs_views.warehouse_cost
  copy_grants = true
  comment     = "FLIP: daily credits per warehouse, from SNOWFLAKE.ACCOUNT_USAGE (#1584)."

  statement = <<-SQL
    SELECT
      DATE_TRUNC('day', START_TIME)::DATE AS USAGE_DATE,
      WAREHOUSE_NAME,
      SUM(CREDITS_USED_COMPUTE)           AS CREDITS_COMPUTE,
      SUM(CREDITS_USED_CLOUD_SERVICES)    AS CREDITS_CLOUD_SERVICES,
      SUM(CREDITS_USED)                   AS CREDITS
    FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
    WHERE START_TIME >= DATEADD('day', -${var.cost_window_days}, CURRENT_TIMESTAMP())
    GROUP BY ALL
  SQL

  depends_on = [snowflake_schema.platform]
}
