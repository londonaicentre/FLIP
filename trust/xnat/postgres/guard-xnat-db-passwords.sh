#!/bin/sh
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

# xnat-db entrypoint guard: refuse to start Postgres with missing, placeholder
# or known-weak passwords, then hand over to the stock postgres entrypoint.
#
# The stock entrypoint consumes POSTGRES_PASSWORD (the superuser password,
# mapped from the kit's XNAT_DATASOURCE_ADMIN_PASSWORD) and the XNAT.sql init
# script consumes XNAT_DATASOURCE_PASSWORD (the xnat role's password) only at
# the very first initdb of the data volume — there is no later chance to catch
# a bad value, and unlike xnat-web (wait-for-postgres.sh) the stock image has
# no guard of its own. A skipped or partial credential mint would otherwise
# initialise the trust's XNAT database with passwords committed in plaintext
# across this public repo's kit templates.

set -e

fail() {
  >&2 echo "ERROR: $1 is $2."
  >&2 echo "       Mint real credentials into the trust's kit file with"
  >&2 echo "       'make generate-xnat-credentials' and redeploy."
  exit 1
}

for var in POSTGRES_PASSWORD XNAT_DATASOURCE_PASSWORD; do
  eval "value=\"\$$var\""
  if [ -z "$value" ]; then
    fail "$var" "not set"
  fi
  case "$value" in
    xnat|password|admin|postgres|"<run-make-generate-xnat-credentials>")
      fail "$var" "set to a weak default or placeholder value"
      ;;
  esac
done

if [ "$XNAT_DATASOURCE_PASSWORD" = "${XNAT_DATASOURCE_USERNAME:-xnat}" ]; then
  fail "XNAT_DATASOURCE_PASSWORD" "identical to XNAT_DATASOURCE_USERNAME"
fi

# The superuser and the xnat application role are separate credentials by
# design; wiring both to one value silently collapses that separation, so the
# app role's password becomes superuser-equivalent. Deployments that template
# these from a secret store are where this happens (both keys pointed at the
# same entry) and nothing downstream would notice.
if [ "$POSTGRES_PASSWORD" = "$XNAT_DATASOURCE_PASSWORD" ]; then
  fail "POSTGRES_PASSWORD" "identical to XNAT_DATASOURCE_PASSWORD (the superuser and xnat role must differ)"
fi

exec docker-entrypoint.sh "$@"
