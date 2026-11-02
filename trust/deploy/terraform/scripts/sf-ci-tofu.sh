#!/usr/bin/env bash
#
# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Run one Snowflake trust root the way CI does (epic #1589): S3 state, OpenTofu state encryption,
# and the role that matches the mode. The three snowflake_trust_*.yml workflows call it; an
# operator calls it once per root with `migrate` to move a laptop state into the bucket.
#
#   sf-ci-tofu.sh <platform|workload> <plan|apply|drift|migrate>
#
#   plan     pull requests. Connects as FLIP_PLATFORM_CI_READ (warehouse USAGE only) and plans
#            with -refresh=false -lock=false: the diff of the change against the stored state.
#            It reads nothing from the account and writes nothing anywhere.
#   apply    develop. Platform root as FLIP_PLATFORM_CI, workload root as FLIP_<CODE>_DEPLOY;
#            plans with the state lock and applies that saved plan.
#   drift    nightly, apply identity. A refreshed plan with -detailed-exitcode, no lock: the exit
#            code is 0 (in sync), 1 (error) or 2 (drift).
#   migrate  operator, once: `tofu init -migrate-state` from the local state into the bucket.
#
# The Snowflake login itself is the environment snowflakedb/snowflake-actions writes
# (SNOWFLAKE_AUTHENTICATOR=WORKLOAD_IDENTITY, SNOWFLAKE_TOKEN): no key, no password, and nothing
# here accepts one.
#
# Inputs (environment): SF_TRUST_CODE, SF_WAREHOUSE, SF_TFSTATE_BUCKET, AWS_REGION,
# SNOWFLAKE_ORGANIZATION_NAME, SNOWFLAKE_ACCOUNT_NAME, and the root's state passphrase
# (SF_PLATFORM_STATE_PASSPHRASE / SF_WORKLOAD_STATE_PASSPHRASE). The platform root also needs
# SF_DEPLOY_USER_PUBLIC_KEY (the deploy user's public key: public by definition).
set -euo pipefail

usage() {
  echo "usage: $0 <platform|workload> <plan|apply|drift|migrate>" >&2
  exit 64
}
[ $# -eq 2 ] || usage
root=$1
mode=$2
case $mode in plan | apply | drift | migrate) ;; *) usage ;; esac

TOFU=${TOFU:-tofu}
TF_DIR=${SF_TF_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}

missing=()
for name in SF_TRUST_CODE SF_WAREHOUSE SF_TFSTATE_BUCKET AWS_REGION SNOWFLAKE_ORGANIZATION_NAME SNOWFLAKE_ACCOUNT_NAME; do
  [ -n "${!name:-}" ] || missing+=("$name")
done

code=${SF_TRUST_CODE:-}
if [ -n "$code" ] && ! [[ $code =~ ^[A-Z][A-Z0-9]{1,15}$ ]]; then
  echo "❌ SF_TRUST_CODE must be upper case letters and digits (got '$code')." >&2
  exit 65
fi

read_role=FLIP_PLATFORM_CI_READ
case $root in
  platform)
    dir=snowflake-platform
    passphrase_var=SF_PLATFORM_STATE_PASSPHRASE
    [ -n "${SF_DEPLOY_USER_PUBLIC_KEY:-}" ] || missing+=(SF_DEPLOY_USER_PUBLIC_KEY)
    write_role=FLIP_PLATFORM_CI
    ;;
  workload)
    dir=snowflake
    passphrase_var=SF_WORKLOAD_STATE_PASSPHRASE
    write_role=FLIP_${code}_DEPLOY
    ;;
  *) usage ;;
esac
[ -n "${!passphrase_var:-}" ] || missing+=("$passphrase_var")

if [ ${#missing[@]} -gt 0 ]; then
  echo "❌ Missing input(s): ${missing[*]}" >&2
  exit 65
fi

# Pull requests plan as the read-only role; everything else runs as the role that may write.
if [ "$mode" = plan ]; then role=$read_role; else role=$write_role; fi

export TF_VAR_organization_name=$SNOWFLAKE_ORGANIZATION_NAME
export TF_VAR_account_name=$SNOWFLAKE_ACCOUNT_NAME
export TF_VAR_trust_code=$code
export TF_VAR_state_passphrase=${!passphrase_var}
if [ "$root" = platform ]; then
  export TF_VAR_platform_role=$role
  export TF_VAR_platform_warehouse=$SF_WAREHOUSE
  export TF_VAR_authenticator=WORKLOAD_IDENTITY
  export TF_VAR_deploy_user_public_key=$SF_DEPLOY_USER_PUBLIC_KEY
  export TF_VAR_ci_apply_user_name=FLIP_CI_APPLY_USER
else
  export TF_VAR_terraform_role=$role
  export TF_VAR_terraform_warehouse=$SF_WAREHOUSE
fi

cd "$TF_DIR/$dir"

# The roots declare `backend "local"`; a gitignored override selects S3, as the roots' README
# documents for a site's remote backend. Encryption is the committed example, verbatim.
printf 'terraform {\n  backend "s3" {}\n}\n' > backend_override.tf
[ -f encryption.tf ] || cp encryption.tf.example encryption.tf

key_prefix=snowflake-trust/$code
init_args=(
  -input=false
  -backend-config="bucket=$SF_TFSTATE_BUCKET"
  -backend-config="key=$key_prefix/$root.tfstate"
  -backend-config="region=$AWS_REGION"
  -backend-config="use_lockfile=true"
  -backend-config="encrypt=true"
  # Workspace listing must stay under the prefix the state roles may list.
  -backend-config="workspace_key_prefix=$key_prefix/workspaces"
)
if [ "$mode" = migrate ]; then
  "$TOFU" init -migrate-state -force-copy "${init_args[@]}"
  echo "✅ $root state for $code is now at s3://$SF_TFSTATE_BUCKET/$key_prefix/$root.tfstate"
  exit 0
fi
"$TOFU" init -reconfigure "${init_args[@]}"

echo "▶ $root $mode for $code as $role"
case $mode in
  plan)
    "$TOFU" plan -input=false -no-color -refresh=false -lock=false -out=tfplan
    ;;
  apply)
    "$TOFU" plan -input=false -no-color -out=tfplan
    "$TOFU" apply -input=false -no-color tfplan
    ;;
  drift)
    "$TOFU" plan -input=false -no-color -lock=false -detailed-exitcode
    ;;
esac
