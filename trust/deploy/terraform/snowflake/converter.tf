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

# The DICOM -> NIfTI converter (FLIP#1578): dcm2niix as an SPCS job service, one per scan.
#
# Why a procedure. Launching a job needs CREATE SERVICE, and a service's OAuth token carries the
# role that OWNS it (SPCS cannot transfer a service's ownership). Giving the service role CREATE
# SERVICE would let any service, the fl-client running researcher code included, start arbitrary
# containers. Instead imaging-api (as FLIP_<CODE>_SVC) CALLs CONVERT_SCAN, an owner's-rights
# procedure owned by FLIP_<CODE>_CONVERTER. The procedure accepts three identifiers, refuses
# anything that is not [A-Za-z0-9_-], and launches a spec fixed here: image, pool, command and
# mounts are not parameters. The job is owned by CONVERTER, so dcm2niix (which parses DICOM from
# outside the trust) runs with CONVERTER's grants: READ on the archive, READ/WRITE on the
# converter I/O stage, CREATE SERVICE on APP. Not the deploy role's.
#
# Why one session's subpath. Stage volumes cannot be mounted read-only, so the job mounts only the
# session it converts (<PROJECT>/arc001/<SESSION> in XNAT's archive layout) and writes only under
# CONVERTER_IO/<PROJECT>/<SESSION>/<SCAN>. imaging-api then registers the output in XNAT over REST.
#
# Verified on the test account, 2026-10-28 (spike in the #1578 comment): job up in 6 s on a warm
# pool, 20 slices converted in 4 s, output visible to the caller's mount, REST registration OK.

locals {
  converter_job_prefix = "${local.prefix}_CONV_"
  # The upstream image, as a literal so trust/xnat/dcm2niix/check_image_pin_sync.sh keeps it in
  # step with ARG DCM2NIIX_VERSION; `make mirror-images` (#1582) copies it into the trust's
  # repository, which is what the job pulls.
  converter_source_image = "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724"
  converter_image        = "/${lower(snowflake_database.trust.name)}/${lower(local.schemas.app)}/${lower(local.image_repository)}/xnat-dcm2niix:${split(":", local.converter_source_image)[1]}"
  archive_stage          = "${snowflake_database.trust.name}.${local.schemas.stages}.XNAT_ARCHIVE"
  converter_stage        = "${snowflake_database.trust.name}.${local.schemas.stages}.CONVERTER_IO"
  app_schema_fqn         = "\"${snowflake_database.trust.name}\".\"${local.schemas.app}\""

  # Identifier check shared by both procedures. REGEXP_LIKE is implicitly anchored; ^...$ is
  # explicit anyway. Nothing reaches the dynamic SQL, a stage path or the shell command unless all
  # three arguments pass.
  converter_args_ok = <<-SQL
    IF (NOT (REGEXP_LIKE(:PROJECT, '^[A-Za-z0-9_-]{1,64}$')
             AND REGEXP_LIKE(:SESSION, '^[A-Za-z0-9_-]{1,64}$')
             AND REGEXP_LIKE(:SCAN, '^[A-Za-z0-9_-]{1,32}$'))) THEN
      RETURN 'rejected';
    END IF;
    -- One job name per scan, deterministic so CONVERT_STATUS finds it: SHA-1 keeps it short and
    -- free of the '-' a Snowflake identifier cannot hold unquoted.
    LET JOB VARCHAR := '${local.converter_job_prefix}' || UPPER(SUBSTR(SHA1(:PROJECT || '/' || :SESSION || '/' || :SCAN), 1, 20));
  SQL
}

resource "snowflake_procedure_sql" "convert_scan" {
  database    = snowflake_database.trust.name
  schema      = local.schemas.app
  name        = "CONVERT_SCAN"
  return_type = "VARCHAR"
  execute_as  = "OWNER"
  comment     = "FLIP ${local.code}: launch dcm2niix for one archived scan (FLIP#1578). Returns launched or rejected."

  arguments {
    arg_name      = "PROJECT"
    arg_data_type = "VARCHAR"
  }
  arguments {
    arg_name      = "SESSION"
    arg_data_type = "VARCHAR"
  }
  arguments {
    arg_name      = "SCAN"
    arg_data_type = "VARCHAR"
  }

  procedure_definition = <<-SQL
    BEGIN
      ${indent(2, local.converter_args_ok)}
      -- The spec holds no single quote (YAML strings are double-quoted and the identifiers cannot
      -- carry one), so it travels as a plain string literal; nor a double dollar, which would end the
      -- dollar-quoted procedure body the provider wraps it in.
      LET SPEC VARCHAR := 'spec:
      containers:
      - name: dcm2niix
        image: ${local.converter_image}
        command: [sh, -c, "dcm2niix -z y -b y -o /output /input/SCANS/' || :SCAN || '/DICOM"]
        resources: {requests: {cpu: 0.5, memory: 1Gi}, limits: {cpu: 2, memory: 4Gi}}
        volumeMounts: [{name: input, mountPath: /input}, {name: output, mountPath: /output}]
      volumes:
      - {name: input, source: "@${local.archive_stage}/' || :PROJECT || '/arc001/' || :SESSION || '", uid: 1001, gid: 1001}
      - {name: output, source: "@${local.converter_stage}/' || :PROJECT || '/' || :SESSION || '/' || :SCAN || '", uid: 1001, gid: 1001}
      logExporters: {eventTableConfig: {logLevel: ${var.service_log_level}}}';
      -- A re-run of the same scan replaces its finished job.
      EXECUTE IMMEDIATE 'DROP SERVICE IF EXISTS ${local.app_schema_fqn}.' || :JOB;
      EXECUTE IMMEDIATE 'EXECUTE JOB SERVICE IN COMPUTE POOL ${local.job_pool} NAME = ${local.app_schema_fqn}.' || :JOB
        || ' ASYNC = TRUE FROM SPECIFICATION ''' || :SPEC || '''';
      RETURN 'launched';
    END;
  SQL
}

resource "snowflake_procedure_sql" "convert_status" {
  database    = snowflake_database.trust.name
  schema      = local.schemas.app
  name        = "CONVERT_STATUS"
  return_type = "VARCHAR"
  execute_as  = "OWNER"
  comment     = "FLIP ${local.code}: SPCS status JSON of one scan's dcm2niix job (FLIP#1578); [] if none, rejected on a bad identifier."

  arguments {
    arg_name      = "PROJECT"
    arg_data_type = "VARCHAR"
  }
  arguments {
    arg_name      = "SESSION"
    arg_data_type = "VARCHAR"
  }
  arguments {
    arg_name      = "SCAN"
    arg_data_type = "VARCHAR"
  }

  procedure_definition = <<-SQL
    BEGIN
      ${indent(2, local.converter_args_ok)}
      LET FQN VARCHAR := '${snowflake_database.trust.name}.${local.schemas.app}.' || :JOB;
      LET STATUS VARCHAR := '[]';
      BEGIN
        SELECT SYSTEM$GET_SERVICE_STATUS(:FQN) INTO :STATUS;
      EXCEPTION
        WHEN STATEMENT_ERROR THEN
          RETURN '[]';
      END;
      RETURN STATUS;
    END;
  SQL
}

# The procedures pass to CONVERTER, so their owner's rights (and the jobs they start) are
# CONVERTER's, not the deploy role's. The deploy role inherits CONVERTER (platform tier), so later
# applies still manage them.
resource "snowflake_grant_ownership" "converter_procedures" {
  for_each = {
    convert_scan   = snowflake_procedure_sql.convert_scan.fully_qualified_name
    convert_status = snowflake_procedure_sql.convert_status.fully_qualified_name
  }

  account_role_name   = local.roles.converter
  outbound_privileges = "COPY"

  on {
    object_type = "PROCEDURE"
    object_name = each.value
  }
}

# --- converter: exactly what the dcm2niix job and its launcher need -----------------------------
resource "snowflake_grant_privileges_to_account_role" "converter_database" {
  account_role_name = local.roles.converter
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.trust.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "converter_schemas" {
  for_each = toset([local.schemas.app, local.schemas.stages])

  account_role_name = local.roles.converter
  privileges        = ["USAGE"]

  on_schema {
    schema_name = "\"${snowflake_database.trust.name}\".\"${each.value}\""
  }

  depends_on = [snowflake_schema.trust]
}

# EXECUTE JOB SERVICE is authorised by CREATE SERVICE on the schema; this is the only role that
# holds it at rest (the services role gets it only for the seconds of a deploy, #1582).
resource "snowflake_grant_privileges_to_account_role" "converter_create_service" {
  account_role_name = local.roles.converter
  privileges        = ["CREATE SERVICE"]

  on_schema {
    schema_name = local.app_schema_fqn
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "converter_job_pool" {
  account_role_name = local.roles.converter
  privileges        = ["USAGE", "MONITOR"]

  on_account_object {
    object_type = "COMPUTE POOL"
    object_name = snowflake_compute_pool.trust[local.job_pool].name
  }
}

resource "snowflake_grant_privileges_to_account_role" "converter_image_repository" {
  account_role_name = local.roles.converter
  privileges        = ["READ"]

  on_schema_object {
    object_type = "IMAGE REPOSITORY"
    object_name = snowflake_image_repository.images.fully_qualified_name
  }
}

resource "snowflake_grant_privileges_to_account_role" "converter_archive_read" {
  account_role_name = local.roles.converter
  privileges        = ["READ"]

  on_schema_object {
    object_type = "STAGE"
    object_name = snowflake_stage_internal.trust["XNAT_ARCHIVE"].fully_qualified_name
  }
}

resource "snowflake_grant_privileges_to_account_role" "converter_io_stage" {
  account_role_name = local.roles.converter
  privileges        = ["READ", "WRITE"]

  on_schema_object {
    object_type = "STAGE"
    object_name = snowflake_stage_internal.trust["CONVERTER_IO"].fully_qualified_name
  }
}

# The services role may CALL the two procedures, and nothing else of the converter's.
resource "snowflake_grant_privileges_to_account_role" "svc_converter_procedures" {
  for_each = snowflake_grant_ownership.converter_procedures

  account_role_name = local.roles.svc
  privileges        = ["USAGE"]

  on_schema_object {
    object_type = "PROCEDURE"
    object_name = each.value.on[0].object_name
  }
}
