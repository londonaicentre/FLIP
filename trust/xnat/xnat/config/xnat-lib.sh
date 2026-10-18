#!/bin/bash
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

# Helpers shared by configure-xnat.sh, configure-dcm2niix.sh and wait-for-xnat-plugins.sh, which
# source this file from their own directory. It only defines functions and jq definitions.
# Credentials always travel in the caller's arguments: configure-xnat.sh changes admin password
# part-way through a run.

# Prints its arguments with the value after -u (credentials) or -d/--data-binary (payloads carry the
# admin and service passwords) replaced, so a failure never writes them to the tee'd configure log.
scrub_args() {
  local redact_next=""
  local arg
  local out=""
  for arg in "$@"; do
    if [[ -n "$redact_next" ]]; then
      out+=" <redacted>"
      redact_next=""
    elif [[ "$arg" == "-u" || "$arg" == "-d" || "$arg" == "--data-binary" ]]; then
      out+=" $arg"
      redact_next=1
    else
      out+=" $arg"
    fi
  done
  printf '%s' "${out# }"
}

# One request to XNAT that never fails on its HTTP status, for callers that read the status
# themselves. Sets xnat_status (000 when nothing answered: refused or timed out), xnat_body and
# xnat_exit (curl's exit code).
# usage: xnat_request <connect timeout s> <max time s> <curl args>
xnat_request() {
  local connect_timeout=$1 max_time=$2 response
  shift 2
  xnat_exit=0
  response=$(curl -sS --connect-timeout "$connect_timeout" --max-time "$max_time" -w '\n%{http_code}' "$@") \
    || { xnat_exit=$?; response=$'\n000'; }
  # shellcheck disable=SC2034  # the caller reads them
  xnat_status=${response##*$'\n'} xnat_body=${response%$'\n'*}
}

# xnat_request that fails closed. On 2xx, prints the response body. On any other status, or a curl
# transport failure, reports the request (and, for HTTP failures, the body) on stderr and returns
# non-zero, which `set -e` turns into an abort at the call site. Bare `curl -s` exits 0 on HTTP
# errors, which is how a rejected configuration call once skipped its step invisibly (FLIP#822,
# FLIP#862). --allow-not-modified as the first argument also accepts a 304, which DQR answers to a
# PUT of an availability interval that already matches.
xnat_curl() {
  local accepted='^2[0-9]{2}$' xnat_status xnat_body xnat_exit
  if [[ "${1:-}" == "--allow-not-modified" ]]; then
    accepted='^(2[0-9]{2}|304)$'
    shift
  fi
  xnat_request 10 120 "$@"
  if [[ "$xnat_exit" -ne 0 ]]; then
    echo "ERROR: curl transport failure (exit $xnat_exit)" >&2
    echo "  args: $(scrub_args "$@")" >&2
    return 1
  fi
  # A middlebox's \r\n line endings must not make the callers' guards fail on an invisible
  # character (a raw CR is not valid inside JSON strings, so this is lossless).
  xnat_status=${xnat_status//$'\r'/} xnat_body=${xnat_body//$'\r'/}
  if ! [[ "$xnat_status" =~ $accepted ]]; then
    echo "ERROR: XNAT request failed with HTTP $xnat_status" >&2
    echo "  args: $(scrub_args "$@")" >&2
    echo "  body: $xnat_body" >&2
    return 1
  fi
  if [[ -n "$xnat_body" ]]; then
    printf '%s\n' "$xnat_body"
  fi
}

# jq definitions that compare an XNAT record with the fields a configuration sets: drift lists the
# keys whose values differ, a null reading the same as "". Prefix a jq program with them.
# shellcheck disable=SC2034  # read by the scripts that source this file
XNAT_JQ_DRIFT='
def norm: if . == null then "" else . end;
def drift($entry; $desired): [$desired | keys_unsorted[] | select(($entry[.] | norm) != ($desired[.] | norm))];
'

# Waits until XNAT serves its login page, bounded in wall-clock time with a timeout on each probe,
# so a dead or wedged XNAT fails the run loudly instead of printing dots forever.
# usage: wait_for_xnat <seconds>
wait_for_xnat() {
  local wait_start=$SECONDS
  echo "Waiting for XNAT to be available..."
  until curl --output /dev/null --silent --head --fail \
    --connect-timeout 5 --max-time 10 "$XNAT_URL/app/template/Login.vm"; do
    if (( SECONDS - wait_start >= $1 )); then
      echo "ERROR: XNAT did not become available within $((SECONDS - wait_start))s" >&2
      exit 1
    fi
    printf '.'
    sleep 1
  done
  echo "XNAT is up!"
}

# An id XNAT returns goes into a URL. XNAT generates it, but check it is a plain number before using it.
require_numeric_id() {  # require_numeric_id <what XNAT returned> <id>
  [[ "$2" =~ ^[0-9]+$ ]] && return
  echo "ERROR: $1 with id '$2'" >&2
  exit 1
}
