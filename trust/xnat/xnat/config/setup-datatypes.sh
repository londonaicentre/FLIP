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

# Enable the XNAT data types FLIP needs beyond the built-in radiology set.
#
# Slide microscopy is the case that matters: XNAT ships the xnat:smSessionData schema but leaves it
# inert, so a trust cannot archive a whole-slide image until the type is enabled. In the UI that is
# Administer > Data Types > Setup Additional Data Type. This script performs the same setup without
# a browser, so a fresh trust comes up able to archive pathology rather than needing a manual step
# that is easy to forget and easy to get half-right.
#
# It is intended to be run after configure-xnat.sh (which rotates the admin password), and is safe
# to re-run: a type that is already fully set up is skipped.
#
# HOW IT WORKS, AND WHY NOT SOME OTHER WAY
#
# There is no REST API for this. /xapi/datatypes is read-only, and the one write endpoint next to
# it -- POST /xapi/datatypes/create -- generates a brand-new type from a schema stub, which is not
# what enabling a type XNAT already ships means.
#
# Writing the rows directly does not work either. Enabling a type populates xdat_element_security,
# xdat_primary_security_field, xdat_element_access, xdat_field_mapping_set and xdat_field_mapping,
# and each carries an *_info column that is a foreign key into a matching *_meta_data table. That is
# XFT's persistence layer, and a bring-up script has no business reimplementing it. Seeding only
# xdat_element_security and hoping XNAT fills in the rest is worse than doing nothing: XNAT then
# treats the type as already configured, builds none of the remaining rows, and reports it as
# enabled while every permission check fails closed.
#
# So this drives the admin UI's own wizard, which is what writes those rows correctly. That is
# practical because the wizard is a plain four-step HTML form chain that keeps no server-side state
# between steps -- each step re-emits the whole item as hidden inputs, so replaying a step's fields
# back with the next step's submit button is exactly what a browser does. The wizard's own last step
# hands off to XNAT's generic ModifyItem action, which is what saves the item through XFT.
#
# The consequence for maintenance: this script posts back whatever fields the previous page
# rendered, rather than hard-coding the field list, so a new field in a future XNAT version is
# carried through instead of silently dropped. What it hard-codes is only what a human would type.

set -euo pipefail

: "${XNAT_ADMIN_USER:?}" "${XNAT_ADMIN_PASSWORD:?}"

XNAT_URL="${XNAT_URL:-http://xnat-web:8080}" # internal to Docker network
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=datatypes-common.sh
source "${SCRIPT_DIR}/datatypes-common.sh"

COOKIE_JAR="$(mktemp)"
BODY_FILE="$(mktemp)"
trap 'rm -f "${COOKIE_JAR}" "${BODY_FILE}"' EXIT

# Fields of the form currently being filled in, and where it posts to. Populated by read_form.
declare -A FIELDS=()
FORM_ACTION=""

# Extract one form's action and its submittable fields from an HTML page, the way a browser would:
# hidden and text inputs always, radios and checkboxes only when checked, submit buttons never (the
# caller names the one it is "clicking").
#
# The form is chosen by its action rather than by being first on the page -- XNAT's admin pages
# open with the site-wide quick-search box, so "the first form" is the wrong one and, being a form
# that posts nowhere useful, a silent one to get wrong.
#
# Output is NUL-separated because XNAT field names contain colons, dots, slashes and brackets, and
# values may contain spaces.
parse_form() {
  FORM_PATTERN="$2" perl -0777 -ne '
    my $wanted = $ENV{FORM_PATTERN};
    my $form;
    while (/<form\b.*?<\/form>/gsi) {
      my $candidate = $&;
      my ($action) = $candidate =~ /<form\b[^>]*\baction\s*=\s*"([^"]*)"/i;
      next unless defined $action && $action =~ /$wanted/;
      $form = $candidate;
      print "__ACTION__\0", $action, "\0";
      last;
    }
    exit 0 unless defined $form;
    while ($form =~ /<input\b([^>]*?)\/?>/gsi) {
      my $attrs = $1;
      my ($name) = $attrs =~ /\bname\s*=\s*"([^"]*)"/i;
      next unless defined $name && length $name;
      my ($type) = $attrs =~ /\btype\s*=\s*"([^"]*)"/i;
      $type = defined $type ? lc $type : "text";
      next if $type =~ /^(?:submit|button|reset|image|file)$/;
      next if $type =~ /^(?:radio|checkbox)$/ && $attrs !~ /\bchecked\b/i;
      my ($value) = $attrs =~ /\bvalue\s*=\s*"([^"]*)"/i;
      $value = defined $value ? $value : "";
      for ($name, $value) { s/&amp;/&/g; s/&lt;/</g; s/&gt;/>/g; s/&quot;/"/g; s/&#39;/'"'"'/g; }
      print $name, "\0", $value, "\0";
    }
  ' "$1"
}

read_form() {
  local name value
  FIELDS=()
  FORM_ACTION=""
  while IFS= read -r -d '' name && IFS= read -r -d '' value; do
    if [[ -z "${name}" ]]; then
      continue
    elif [[ "${name}" == "__ACTION__" ]]; then
      FORM_ACTION="${value}"
    else
      FIELDS["${name}"]="${value}"
    fi
  done < <(parse_form "$1" "$2")

  # XNAT injects its CSRF token from JavaScript, so the value parsed out of the page is the literal
  # JS expression rather than a token. Replace it with the real one, read from the same page.
  FIELDS["XNAT_CSRF"]="$(perl -0777 -ne "print \$1 if /csrfToken\s*=\s*'([0-9a-fA-F-]{36})'/" "$1")"
}

# Guard against a page that came back without the form we meant to fill in -- which is how an
# expired session, a wrong password or a renamed screen would otherwise present: as a form with no
# fields, posted nowhere, reported as success.
require_form() {
  [[ -n "${FORM_ACTION}" ]] || { echo "ERROR: no form matching /$1/ on $2" >&2; return 1; }
}

# Fetch a page into BODY_FILE and read the named form out of it.
get_page() {
  local status
  status=$(curl --silent --show-error --location --cookie "${COOKIE_JAR}" --cookie-jar "${COOKIE_JAR}" \
    --output "${BODY_FILE}" --write-out '%{http_code}' "${XNAT_URL}$1")
  [[ "${status}" =~ ^2 ]] || { echo "ERROR: GET $1 returned HTTP ${status}" >&2; return 1; }
  read_form "${BODY_FILE}" "$2"
  require_form "$2" "$1"
}

# Post the current form back, "clicking" the named submit button, then read whatever form comes back
# so the next step can be filled in the same way. The expected next action is a pattern rather than
# a requirement: the last step of a chain returns no form at all.
submit_form() {
  local button="$1" next_pattern="${2:-}" status name
  local -a data=(--data-urlencode "${button}=Submit")
  for name in "${!FIELDS[@]}"; do
    data+=(--data-urlencode "${name}=${FIELDS[${name}]}")
  done

  status=$(curl --silent --show-error --location --cookie "${COOKIE_JAR}" --cookie-jar "${COOKIE_JAR}" \
    --output "${BODY_FILE}" --write-out '%{http_code}' "${data[@]}" "${XNAT_URL}${FORM_ACTION}")
  [[ "${status}" =~ ^2 ]] || { echo "ERROR: POST ${FORM_ACTION} (${button}) returned HTTP ${status}" >&2; return 1; }

  # XNAT reports a rejected step by re-rendering it with an error block rather than by failing the
  # request, so without this a bad value looks like a step that simply did not advance.
  if grep -q 'Invalid parameters' "${BODY_FILE}"; then
    echo "ERROR: ${button} was rejected by XNAT:" >&2
    perl -0777 -ne 'print "$1\n" if /class="error">(.*?)<\/div>/s' "${BODY_FILE}" \
      | sed -e 's/<[^>]*>/ /g' -e 's/  */ /g' >&2
    return 1
  fi

  if [[ -n "${next_pattern}" ]]; then
    read_form "${BODY_FILE}" "${next_pattern}"
  else
    FIELDS=()
    FORM_ACTION=""
  fi
}

login() {
  local status
  status=$(curl --silent --show-error --cookie-jar "${COOKIE_JAR}" --output /dev/null --write-out '%{http_code}' \
    --data-urlencode "username=${XNAT_ADMIN_USER}" --data-urlencode "password=${XNAT_ADMIN_PASSWORD}" \
    "${XNAT_URL}/login")
  # A successful form login redirects; a rejected one re-renders the login page with 200.
  [[ "${status}" == "302" ]] || { echo "ERROR: login as ${XNAT_ADMIN_USER} failed (HTTP ${status})" >&2; return 1; }
}

# Run the setup wizard. Only the type and its display names are supplied; every other answer is left
# at the wizard's own default, which is what makes a scripted setup identical to a careful manual
# one rather than a second opinion about how the type should be configured.
run_wizard() {
  local element="$1" singular="$2" plural="$3"
  local wizard='/app/action/(ElementSecurityWizard|ModifyItem)$'

  get_page "/app/template/XDATScreen_add_xdat_element_security.vm/popup/true" "${wizard}"
  FIELDS["xdat:element_security.element_name"]="${element}"
  submit_form "eventSubmit_doStep1" "${wizard}"

  FIELDS["xdat:element_security.singular"]="${singular}"
  FIELDS["xdat:element_security.plural"]="${plural}"
  submit_form "eventSubmit_doStep2" "${wizard}"

  # Step 3 completes the setup for a secured type: it derives the primary security fields from the
  # schema and saves. The fourth step exists only to let an admin hand-pick those fields instead, so
  # for a default setup there is nothing left to submit and no form comes back.
  submit_form "eventSubmit_doStep3" "${wizard}"
  if [[ -n "${FORM_ACTION}" ]]; then
    submit_form "eventSubmit_doPerform"
  fi
}

# The wizard has no field for the short code XNAT uses when generating experiment IDs; the Data
# Types admin page does. Its form carries every registered type's current values, so it is read and
# posted back whole with one cell changed, exactly as saving that page in a browser would.
set_code() {
  local element="$1" code="$2" name index=""

  get_page "/app/template/XDATScreen_dataTypes.vm" '/app/action/ManageDataTypes$'
  for name in "${!FIELDS[@]}"; do
    if [[ "${name}" == *"/element_name" && "${FIELDS[${name}]}" == "${element}" ]]; then
      [[ "${name}" =~ \[([0-9]+)\] ]] && index="${BASH_REMATCH[1]}"
      break
    fi
  done
  if [[ -z "${index}" ]]; then
    echo "ERROR: ${element} is not listed on the Data Types page, so its code cannot be set" >&2
    return 1
  fi

  FIELDS["xdat:security/element_security_set/element_security[${index}]/code"]="${code}"
  submit_form "save"
}

# Wrapped in a function, and run only when this file is executed rather than sourced, so the tests
# can exercise the form handling above without logging in to anything.
main() {
  login

  for spec in "${REQUIRED_DATATYPES[@]}"; do
    IFS='|' read -r element singular plural code description <<< "${spec}"

    datatype_state "${element}"
    state="${DT_STATE}"
    if [[ "${state}" == "ok" ]]; then
      echo "  ✅ ${element} — already set up"
      continue
    fi
    if [[ "${state}" == "INCOMPLETE" ]]; then
      echo "  ⚠️  ${element} — half-configured ($(dt_counts_summary)); re-running the wizard"
    fi

    echo "  ⏳ ${element} (${description}) — running the data type setup wizard"
    run_wizard "${element}" "${singular}" "${plural}"
    set_code "${element}" "${code}"
    echo "  ✅ ${element} — set up as ${singular} / ${plural} (${code})"
  done

  # Prove the whole permission chain landed, not just the row the REST API reports on. A half-written
  # chain is the failure this pairing exists to catch -- see verify-datatypes.sh.
  exec bash "${SCRIPT_DIR}/verify-datatypes.sh"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
