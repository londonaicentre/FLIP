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
# Prepare a CI runner to deploy a Snowflake trust (snowflake_trust_deploy.yml, epic #1589).
#
#   1. Writes the kit file trust/.env.<CODE>.stag from the SF_KIT_FILE environment secret, mode
#      600. With DOCKER_TAG_INPUT it pins DOCKER_TAG and DOCKER_FL_TAG to that tag (a release or a
#      sha- build), the per-image XNAT_TAG / ORTHANC_TAG opt-outs left as the kit says.
#   2. Writes ~/.snowflake/config.toml with one connection, `ci`, that authenticates by workload
#      identity: account, user, role and warehouse only. There is no key, password or token in it;
#      the token arrives in the environment per call (sf-ci-token.sh).
#
# Refuses a kit for another trust, and one that still carries placeholders in a value sync-kit
# would push, before anything reaches Snowflake. Prints no kit value.
#
# Inputs (environment): SF_TRUST_CODE, SF_KIT_FILE, SNOWFLAKE_ORGANIZATION_NAME,
# SNOWFLAKE_ACCOUNT_NAME, SF_WAREHOUSE, and optionally DOCKER_TAG_INPUT. REPO_ROOT and
# SNOWFLAKE_HOME override the repository root and ~/.snowflake (tests).
set -euo pipefail

missing=()
for name in SF_TRUST_CODE SF_KIT_FILE SNOWFLAKE_ORGANIZATION_NAME SNOWFLAKE_ACCOUNT_NAME SF_WAREHOUSE; do
  [ -n "${!name:-}" ] || missing+=("$name")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "❌ Missing input(s): ${missing[*]}" >&2
  exit 65
fi
code=$SF_TRUST_CODE
[[ $code =~ ^[A-Z][A-Z0-9]{1,15}$ ]] || {
  echo "❌ SF_TRUST_CODE is not a trust code." >&2
  exit 65
}
for name in SNOWFLAKE_ORGANIZATION_NAME SNOWFLAKE_ACCOUNT_NAME SF_WAREHOUSE; do
  [[ ${!name} =~ ^[A-Za-z0-9_]{1,255}$ ]] || {
    echo "❌ $name is not a Snowflake identifier." >&2
    exit 65
  }
done
tag=${DOCKER_TAG_INPUT:-}
if [ -n "$tag" ] && ! [[ $tag =~ ^(v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?|sha-[0-9a-f]{7})$ ]]; then
  echo "❌ DOCKER_TAG_INPUT must be vX.Y.Z, vX.Y.Z-rc.N or sha-<7 hex>." >&2
  exit 65
fi

repo=${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}
kit=$repo/trust/.env.$code.stag
umask 077
printf '%s\n' "$SF_KIT_FILE" > "$kit"
chmod 600 "$kit"

value() { sed -n "s/^$1=//p" "$kit" | tail -1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }

kit_code=$(value TRUST_CODE)
if [ -n "$kit_code" ] && [ "$kit_code" != "$code" ]; then
  rm -f "$kit"
  echo "❌ SF_KIT_FILE is the kit of $kit_code, not $code." >&2
  exit 65
fi
for name in TRUST_API_KEY TRUST_INTERNAL_SERVICE_KEY AES_KEY_BASE64 CENTRAL_HUB_API_URL; do
  v=$(value "$name")
  if [ -z "$v" ] || [[ $v == \<*\> ]]; then
    rm -f "$kit"
    echo "❌ SF_KIT_FILE has no value for $name (register the trust and paste the whole kit)." >&2
    exit 65
  fi
done

if [ -n "$tag" ]; then
  for name in DOCKER_TAG DOCKER_FL_TAG; do
    if grep -q "^$name=" "$kit"; then
      sed -i "s/^$name=.*/$name=$tag/" "$kit"
    else
      printf '%s=%s\n' "$name" "$tag" >> "$kit"
    fi
  done
  echo "▶ Images pinned to $tag"
fi

home=${SNOWFLAKE_HOME:-$HOME/.snowflake}
mkdir -p "$home"
config=$home/config.toml
cat > "$config" <<EOF
default_connection_name = "ci"

[connections.ci]
account = "${SNOWFLAKE_ORGANIZATION_NAME}-${SNOWFLAKE_ACCOUNT_NAME}"
user = "FLIP_CI_APPLY_USER"
role = "FLIP_${code}_DEPLOY"
warehouse = "${SF_WAREHOUSE}"
authenticator = "WORKLOAD_IDENTITY"
workload_identity_provider = "OIDC"
EOF
chmod 600 "$config"
echo "✅ Kit for $code and the ci connection are in place (values not shown)."
