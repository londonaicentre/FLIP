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

# Grants on objects this root OWNS, to roles the platform tier created.
#
# The deploy role owns every object here, and an owner may grant privileges on its own objects
# without MANAGE GRANTS. The platform tier granted the consumer roles TO the deploy role, so the
# deploy role may name them. Nothing below is an account-level grant: the four account privileges
# the deploy role holds are issued once, in ../snowflake-platform/grants.tf, and the static guards
# in tests/ fail this root if an `on_account = true` grant ever appears.
#
# The deploy role itself needs no grant: it owns what it creates. Nothing is granted to PUBLIC.

# --- svc: the role services run as --------------------------------------------------------------
resource "snowflake_grant_privileges_to_account_role" "svc_database" {
  account_role_name = local.roles.svc
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.trust.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "svc_schemas" {
  for_each = toset([local.schemas.app, local.schemas.stages])

  account_role_name = local.roles.svc
  privileges        = ["USAGE"]

  on_schema {
    schema_name = "\"${snowflake_database.trust.name}\".\"${each.value}\""
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "svc_stages" {
  account_role_name = local.roles.svc
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
  account_role_name = local.roles.svc
  privileges        = ["READ"]

  on_schema_object {
    object_type = "IMAGE REPOSITORY"
    object_name = snowflake_image_repository.images.fully_qualified_name
  }
}

resource "snowflake_grant_privileges_to_account_role" "svc_pools" {
  for_each = snowflake_compute_pool.trust

  account_role_name = local.roles.svc
  privileges        = ["USAGE", "MONITOR"]

  on_account_object {
    object_type = "COMPUTE POOL"
    object_name = each.value.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "svc_warehouse" {
  account_role_name = local.roles.svc
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.trust.name
  }
}

# Secrets. Terraform creates no secret and holds no secret value: `make sync-kit` (#1582) writes
# them into the APP schema from the kit file, owned by the deploy role. A FUTURE grant covers
# whatever sync-kit creates, so the privilege is declared here, by object class, without this root
# ever naming or reading one. Future grants inside a schema are an owner privilege, so this needs
# no MANAGE GRANTS either.
resource "snowflake_grant_privileges_to_account_role" "svc_future_secrets" {
  account_role_name = local.roles.svc
  privileges        = ["USAGE"]

  on_schema_object {
    future {
      object_type_plural = "SECRETS"
      in_schema          = "\"${snowflake_database.trust.name}\".\"${local.schemas.app}\""
    }
  }

  depends_on = [snowflake_schema.trust]
}

# --- OMOP reader and loader ---------------------------------------------------------------------
resource "snowflake_grant_privileges_to_account_role" "omop_reader_database" {
  account_role_name = local.roles.omop_reader
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.trust.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "omop_reader_schema" {
  account_role_name = local.roles.omop_reader
  privileges        = ["USAGE"]

  on_schema {
    schema_name = "\"${snowflake_database.trust.name}\".\"${local.schemas.omop}\""
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "omop_reader_tables" {
  for_each = toset(["TABLES", "VIEWS"])

  account_role_name = local.roles.omop_reader
  privileges        = ["SELECT"]

  on_schema_object {
    future {
      object_type_plural = each.value
      in_schema          = "\"${snowflake_database.trust.name}\".\"${local.schemas.omop}\""
    }
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "omop_loader_database" {
  account_role_name = local.roles.omop_loader
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.trust.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "omop_loader_schema" {
  account_role_name = local.roles.omop_loader
  privileges        = ["USAGE", "CREATE TABLE", "CREATE VIEW"]

  on_schema {
    schema_name = "\"${snowflake_database.trust.name}\".\"${local.schemas.omop}\""
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "omop_loader_tables" {
  account_role_name = local.roles.omop_loader
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
  account_role_name = local.roles.omop_loader
  privileges        = ["READ"]

  on_schema_object {
    object_type = "STAGE"
    object_name = snowflake_stage_internal.trust["SEED"].fully_qualified_name
  }
}

resource "snowflake_grant_privileges_to_account_role" "omop_loader_warehouse" {
  account_role_name = local.roles.omop_loader
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.trust.name
  }
}

# --- observer: metadata and the OBS views, no clinical data ---------------------------------------
# SELECT on the account event table itself is granted by the platform tier, which owns it.
resource "snowflake_grant_privileges_to_account_role" "observer_database" {
  account_role_name = local.roles.observer
  privileges        = ["USAGE", "MONITOR"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.trust.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "observer_obs_schema" {
  account_role_name = local.roles.observer
  privileges        = ["USAGE"]

  on_schema {
    schema_name = "\"${snowflake_database.trust.name}\".\"${local.schemas.obs}\""
  }

  depends_on = [snowflake_schema.trust]
}

resource "snowflake_grant_privileges_to_account_role" "observer_obs_views" {
  account_role_name = local.roles.observer
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

  account_role_name = local.roles.observer
  privileges        = ["MONITOR"]

  on_account_object {
    object_type = "COMPUTE POOL"
    object_name = each.value.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "observer_warehouse" {
  account_role_name = local.roles.observer
  privileges        = ["MONITOR"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.trust.name
  }
}
