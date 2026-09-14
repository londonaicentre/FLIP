#!/bin/bash
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

# Verify that the XNAT data types FLIP needs beyond the built-in radiology set are fully set up.
#
# Slide microscopy is the case that matters: XNAT ships the xnat:smSessionData schema but leaves it
# inert, so a trust cannot archive a whole-slide image until it is enabled. setup-datatypes.sh does
# the enabling; this script proves it worked, and is safe to run on its own against any instance.
#
# WHY THIS IS A SEPARATE CHECK RATHER THAN A RETURN CODE
#
# Enabling a type writes five tables -- xdat_element_security, xdat_primary_security_field,
# xdat_element_access, xdat_field_mapping_set and xdat_field_mapping -- and a setup that writes only
# the first of them is the dangerous outcome, not an obviously broken one. Such a type is reported
# as enabled by /xapi/datatypes and looks correct in the admin UI, but every permission check fails
# closed, so archiving dies much later with "This user has insufficient privileges for the data
# type" -- which reads like a problem with the *user*, not a missing datatype.
#
# That is reachable both by hand (an admin who abandons the wizard partway) and by the tempting
# shortcut of seeding xdat_element_security in SQL and expecting XNAT to fill in the rest, which it
# does not: it treats the type as already configured and builds nothing. Checking the whole chain
# here turns a confusing failure at import time into an explicit one at bring-up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=datatypes-common.sh
source "${SCRIPT_DIR}/datatypes-common.sh"

failed=0

for spec in "${REQUIRED_DATATYPES[@]}"; do
  IFS='|' read -r element singular plural code description <<< "${spec}"
  datatype_state "${element}"
  state="${DT_STATE}"

  if [[ "${state}" == "ok" ]]; then
    echo "  ✅ ${element} (${DT_ACC} group grant(s), ${DT_MAPS} permission mapping(s))"
    continue
  fi

  failed=1
  echo "  ❌ ${element} — ${state} (${description})" >&2
  echo "       $(dt_counts_summary)" >&2
  if [[ "${state}" == "INCOMPLETE" ]]; then
    echo "       Present but not usable: XNAT will list it as enabled while every permission check" >&2
    echo "       fails, so archiving dies with 'insufficient privileges for the data type'." >&2
  fi
done

if [[ "${failed}" == "1" ]]; then
  cat >&2 <<'GUIDANCE'

  To set these up, run the sibling script:

    bash setup-datatypes.sh

  It drives the same wizard the admin UI uses (Administer > Data Types > Setup Additional Data
  Type) and is safe to re-run: types that are already registered are skipped.

  XNAT caches element security, so restart it if this check still disagrees with the admin UI.
GUIDANCE
  exit 1
fi
