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

# shellcheck shell=bash
#
# Shared by setup-datatypes.sh (which enables FLIP's extra XNAT data types) and
# verify-datatypes.sh (which proves they are enabled), so the two can never disagree about what is
# required or about what "set up" means.

: "${XNAT_DATASOURCE_NAME:?}" "${XNAT_DATASOURCE_USERNAME:?}" "${XNAT_DATASOURCE_PASSWORD:?}"

XNAT_DB_HOST="${XNAT_DB_HOST:-xnat-db}"
XNAT_DB_PORT="${XNAT_DB_PORT:-5432}"

# The data types FLIP needs that XNAT ships in its schema but leaves disabled.
#
# Format: element_name|singular|plural|code|description
#
# The code is the short prefix XNAT uses when generating IDs for new experiments of the type. The
# setup wizard has no field for it, so setup-datatypes.sh sets it separately.
REQUIRED_DATATYPES=(
  "xnat:smSessionData|SM Session|SM Sessions|SM|slide microscopy / digital pathology"
)

# A built-in type known to be correctly configured, used as the reference for how many rows a
# complete setup has. Comparing against live data rather than hard-coded counts means the check
# keeps working as groups and projects are added, which change the counts on every type alike.
TEMPLATE_ELEMENT="xnat:ctSessionData"

psql_xnat() {
  PGPASSWORD="${XNAT_DATASOURCE_PASSWORD}" psql \
    --host "${XNAT_DB_HOST}" --port "${XNAT_DB_PORT}" \
    --username "${XNAT_DATASOURCE_USERNAME}" --dbname "${XNAT_DATASOURCE_NAME}" \
    --no-align --tuples-only --quiet --set ON_ERROR_STOP=1 "$@"
}

# Count the rows making up one type's setup: security row, primary security fields, group grants,
# mapping sets, mappings.
chain_counts() {
  psql_xnat -c "
    SELECT (SELECT COUNT(*) FROM xdat_element_security WHERE element_name = '$1')
        || ' ' || (SELECT COUNT(*) FROM xdat_primary_security_field
                    WHERE primary_security_fields_primary_element_name = '$1')
        || ' ' || (SELECT COUNT(*) FROM xdat_element_access WHERE element_name = '$1')
        || ' ' || (SELECT COUNT(DISTINCT fms.xdat_field_mapping_set_id)
                     FROM xdat_element_access ea
                     JOIN xdat_field_mapping_set fms
                       ON fms.permissions_allow_set_xdat_elem_xdat_element_access_id = ea.xdat_element_access_id
                    WHERE ea.element_name = '$1')
        || ' ' || (SELECT COUNT(fm.xdat_field_mapping_id)
                     FROM xdat_element_access ea
                     JOIN xdat_field_mapping_set fms
                       ON fms.permissions_allow_set_xdat_elem_xdat_element_access_id = ea.xdat_element_access_id
                     JOIN xdat_field_mapping fm
                       ON fm.xdat_field_mapping_set_xdat_field_mapping_set_id = fms.xdat_field_mapping_set_id
                    WHERE ea.element_name = '$1');"
}

# Classify one type against the template, leaving the verdict in DT_STATE ("ok", "INCOMPLETE" or
# "not set up") and the counts in DT_* / DT_T_* for the caller to report.
#
# The verdict is returned in a variable rather than echoed because the counts have to survive with
# it, and a caller writing `state=$(datatype_state ...)` would run the whole thing in a subshell and
# get the verdict with every count still unset.
#
# The database is deliberately the source of truth here rather than /xapi/datatypes: XNAT caches
# element security, so the REST API keeps listing a type whose rows have gone (and, for the
# INCOMPLETE case, lists one whose rows never fully arrived).
datatype_state() {
  read -r DT_T_SEC DT_T_PSF DT_T_ACC DT_T_SETS DT_T_MAPS <<< "$(chain_counts "${TEMPLATE_ELEMENT}")"
  read -r DT_SEC DT_PSF DT_ACC DT_SETS DT_MAPS <<< "$(chain_counts "$1")"

  if [[ "${DT_SEC}" == "0" ]]; then
    DT_STATE="not set up"
  elif [[ "${DT_PSF}" -lt "${DT_T_PSF}" || "${DT_ACC}" -lt "${DT_T_ACC}" \
       || "${DT_SETS}" -lt "${DT_T_SETS}" || "${DT_MAPS}" -lt "${DT_T_MAPS}" ]]; then
    DT_STATE="INCOMPLETE"
  else
    DT_STATE="ok"
  fi
}

dt_counts_summary() {
  echo "security=${DT_SEC}/${DT_T_SEC} fields=${DT_PSF}/${DT_T_PSF} grants=${DT_ACC}/${DT_T_ACC}" \
       "sets=${DT_SETS}/${DT_T_SETS} mappings=${DT_MAPS}/${DT_T_MAPS}"
}
