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
# Least privilege, and nothing granted to PUBLIC: every grant in this file names one of the six
# FLIP roles. The static tests enforce both (tests/test_static_guards.py).

# --- admin: owns the tree ---------------------------------------------------------------------
resource "snowflake_grant_privileges_to_account_role" "admin_database" {
  account_role_name = snowflake_account_role.admin.name
  privileges        = ["USAGE", "MONITOR", "CREATE SCHEMA"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.trust.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "admin_schemas" {
  account_role_name = snowflake_account_role.admin.name
  privileges = [
    "USAGE",
    "MONITOR",
    "CREATE TABLE",
    "CREATE VIEW",
    "CREATE STAGE",
    "CREATE SECRET",
    "CREATE NETWORK RULE",
    "CREATE IMAGE REPOSITORY",
    "CREATE SERVICE",
    "CREATE TASK",
  ]

  on_schema {
    all_schemas_in_database = snowflake_database.trust.name
  }

  depends_on = [snowflake_schema.trust]
}

# --- deploy: creates and operates services, reads no clinical data ------------------------------
resource "snowflake_grant_privileges_to_account_role" "deploy_database" {
  account_role_name = snowflake_account_role.deploy.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.trust.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "deploy_app_schema" {
  account_role_name = snowflake_account_role.deploy.name
  privileges        = ["USAGE", "CREATE SERVICE", "CREATE SECRET", "CREATE NETWORK RULE"]

  on_schema {
    schema_name = "\"${snowflake_database.trust.name}\".\"${local.schemas.app}\""
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "deploy_stages_schema" {
  account_role_name = snowflake_account_role.deploy.name
  privileges        = ["USAGE"]

  on_schema {
    schema_name = "\"${snowflake_database.trust.name}\".\"${local.schemas.stages}\""
  }

  depends_on = [snowflake_schema.trust]
}

# The deploy identity pushes the FL kit, seed data and converter inputs onto stages.
resource "snowflake_grant_privileges_to_account_role" "deploy_stages" {
  account_role_name = snowflake_account_role.deploy.name
  privileges        = ["READ", "WRITE"]

  on_schema_object {
    all {
      object_type_plural = "STAGES"
      in_schema          = "\"${snowflake_database.trust.name}\".\"${local.schemas.stages}\""
    }
  }

  depends_on = [snowflake_stage_internal.trust]
}

resource "snowflake_grant_privileges_to_account_role" "deploy_image_repository" {
  account_role_name = snowflake_account_role.deploy.name
  privileges        = ["READ", "WRITE"]

  on_schema_object {
    object_type = "IMAGE REPOSITORY"
    object_name = snowflake_image_repository.images.fully_qualified_name
  }
}

resource "snowflake_grant_privileges_to_account_role" "deploy_pools" {
  for_each = snowflake_compute_pool.trust

  account_role_name = snowflake_account_role.deploy.name
  privileges        = ["USAGE", "MONITOR", "OPERATE"]

  on_account_object {
    object_type = "COMPUTE POOL"
    object_name = each.value.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "deploy_warehouse" {
  account_role_name = snowflake_account_role.deploy.name
  privileges        = ["USAGE", "OPERATE"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.trust.name
  }
}

# A service needs this account privilege to publish its (internal only) endpoints.
resource "snowflake_grant_privileges_to_account_role" "deploy_bind_endpoint" {
  account_role_name = snowflake_account_role.deploy.name
  privileges        = ["BIND SERVICE ENDPOINT"]
  on_account        = true
}

# --- svc: the role services run as --------------------------------------------------------------
resource "snowflake_grant_privileges_to_account_role" "svc_database" {
  account_role_name = snowflake_account_role.svc.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.trust.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "svc_schemas" {
  for_each = toset([local.schemas.app, local.schemas.stages])

  account_role_name = snowflake_account_role.svc.name
  privileges        = ["USAGE"]

  on_schema {
    schema_name = "\"${snowflake_database.trust.name}\".\"${each.value}\""
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "svc_stages" {
  account_role_name = snowflake_account_role.svc.name
  privileges        = ["READ", "WRITE"]

  on_schema_object {
    all {
      object_type_plural = "STAGES"
      in_schema          = "\"${snowflake_database.trust.name}\".\"${local.schemas.stages}\""
    }
  }

  depends_on = [snowflake_stage_internal.trust]
}

resource "snowflake_grant_privileges_to_account_role" "svc_image_repository" {
  account_role_name = snowflake_account_role.svc.name
  privileges        = ["READ"]

  on_schema_object {
    object_type = "IMAGE REPOSITORY"
    object_name = snowflake_image_repository.images.fully_qualified_name
  }
}

resource "snowflake_grant_privileges_to_account_role" "svc_pools" {
  for_each = snowflake_compute_pool.trust

  account_role_name = snowflake_account_role.svc.name
  privileges        = ["USAGE", "MONITOR"]

  on_account_object {
    object_type = "COMPUTE POOL"
    object_name = each.value.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "svc_warehouse" {
  account_role_name = snowflake_account_role.svc.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.trust.name
  }
}

# Secrets. Terraform creates no secret and holds no secret value: `make sync-kit` (#1582) writes
# them into the APP schema from the kit file. A FUTURE grant covers whatever sync-kit creates, so
# the privilege is declared here, by object class, without this root ever naming or reading one.
resource "snowflake_grant_privileges_to_account_role" "svc_future_secrets" {
  account_role_name = snowflake_account_role.svc.name
  privileges        = ["USAGE"]

  on_schema_object {
    future {
      object_type_plural = "SECRETS"
      in_schema          = "\"${snowflake_database.trust.name}\".\"${local.schemas.app}\""
    }
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "svc_eai" {
  count = var.enable_hub_egress ? 1 : 0

  account_role_name = snowflake_account_role.svc.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "INTEGRATION"
    object_name = snowflake_external_access_integration.hub[0].name
  }
}

# --- OMOP reader and loader ---------------------------------------------------------------------
resource "snowflake_grant_privileges_to_account_role" "omop_reader_database" {
  account_role_name = snowflake_account_role.omop_reader.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.trust.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "omop_reader_schema" {
  account_role_name = snowflake_account_role.omop_reader.name
  privileges        = ["USAGE"]

  on_schema {
    schema_name = "\"${snowflake_database.trust.name}\".\"${local.schemas.omop}\""
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "omop_reader_tables" {
  for_each = toset(["TABLES", "VIEWS"])

  account_role_name = snowflake_account_role.omop_reader.name
  privileges        = ["SELECT"]

  on_schema_object {
    future {
      object_type_plural = each.value
      in_schema          = "\"${snowflake_database.trust.name}\".\"${local.schemas.omop}\""
    }
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "omop_loader_schema" {
  account_role_name = snowflake_account_role.omop_loader.name
  privileges        = ["USAGE", "CREATE TABLE", "CREATE VIEW"]

  on_schema {
    schema_name = "\"${snowflake_database.trust.name}\".\"${local.schemas.omop}\""
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "omop_loader_tables" {
  account_role_name = snowflake_account_role.omop_loader.name
  privileges        = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]

  on_schema_object {
    future {
      object_type_plural = "TABLES"
      in_schema          = "\"${snowflake_database.trust.name}\".\"${local.schemas.omop}\""
    }
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "omop_loader_seed_stage" {
  account_role_name = snowflake_account_role.omop_loader.name
  privileges        = ["READ"]

  on_schema_object {
    object_type = "STAGE"
    object_name = snowflake_stage_internal.trust["SEED"].fully_qualified_name
  }
}

# --- observer: metadata and the event-table views, no clinical data ------------------------------
resource "snowflake_grant_privileges_to_account_role" "observer_database" {
  account_role_name = snowflake_account_role.observer.name
  privileges        = ["USAGE", "MONITOR"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.trust.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "observer_obs_schema" {
  account_role_name = snowflake_account_role.observer.name
  privileges        = ["USAGE"]

  on_schema {
    schema_name = "\"${snowflake_database.trust.name}\".\"${local.schemas.obs}\""
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "observer_obs_views" {
  account_role_name = snowflake_account_role.observer.name
  privileges        = ["SELECT"]

  on_schema_object {
    future {
      object_type_plural = "VIEWS"
      in_schema          = "\"${snowflake_database.trust.name}\".\"${local.schemas.obs}\""
    }
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "observer_pools" {
  for_each = snowflake_compute_pool.trust

  account_role_name = snowflake_account_role.observer.name
  privileges        = ["MONITOR"]

  on_account_object {
    object_type = "COMPUTE POOL"
    object_name = each.value.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "observer_warehouse" {
  account_role_name = snowflake_account_role.observer.name
  privileges        = ["MONITOR"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.trust.name
  }
}
