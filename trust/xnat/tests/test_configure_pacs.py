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
"""Execution tests for the PACS/DQR configuration in configure-xnat.sh (FLIP#993).

The script is run for real against a stub ``curl`` that records every payload it is asked to POST or
PUT, so these assert what XNAT would actually receive rather than matching strings in the source.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

CONFIG_DIR = Path(__file__).resolve().parents[1] / "xnat" / "config"
SCRIPT = CONFIG_DIR / "configure-xnat.sh"

# A miniature XNAT: it keeps the /xapi/pacs and /xapi/dicomscp collections in files and mutates them
# on POST/PUT/DELETE, so a GET reflects what the script actually did rather than what the test said.
# The earlier stub echoed the *configured* AE title back, which made "the configured title reached
# XNAT" assertions circular — they held even when the script sent something else.
STUB_CURL = r"""#!/bin/bash
url=""; data=""; method="GET"; status_only=0; outfile=""; creds=""
prev=""
for a in "$@"; do
  case "$prev" in -d) data="$a";; -X) method="$a";; -o|--output) outfile="$a";; -u) creds="$a";; esac
  case "$a" in http*) url="$a";; '%{http_code}') status_only=1;; esac
  prev="$a"
done

# Every request, not only those carrying a body: a PUT whose whole meaning is its URL
# (/xapi/users/guest/enabled/false) is otherwise invisible to the tests.
printf '%s\n' "=== $method $url" >> "$PAYLOADS"
[ -n "$data" ] && printf '%s\n' "$data" >> "$PAYLOADS"
[ -n "$CREDS_LOG" ] && printf '%s %s %s\n' "$method" "$url" "$creds" >> "$CREDS_LOG"

edit() {  # edit <state-file> <jq-filter> [args...]
  local f="$1"; shift
  jq "$@" "$f" > "$f.tmp" && mv "$f.tmp" "$f"
}

# DQR's availability API over $AVAIL_STATE (one JSON array of intervals), with the behaviour the
# converge depends on, as read from the pinned DQR 3.0.0 jar: times stored the way
# PacsAvailability.validateTime stores them (24:00 as 00:00, H:MM zero-padded), overlap judged per
# day with an end not after the start running into the next day (DqrDateRange), a create refused
# with 400 on overlap, and a PUT that copies the day, times, threads, utilisation and pacsId from
# the body and answers 304 when none of them differ. Not modelled: the PUT trimming the day's other
# intervals to fit, which the script never relies on because it removes them first.
AVAIL_DEFS='
def dqr_time: if . == "24:00" then "00:00" elif test("^[0-9]:") then "0" + . else . end;
def stored: (.availabilityStart |= dqr_time) | (.availabilityEnd |= dqr_time);
def mins: split(":") | (.[0] | tonumber) * 60 + (.[1] | tonumber);
def span: (.availabilityStart | mins) as $s | (.availabilityEnd | mins) as $e
  | [$s, (if $e <= $s then $e + 1440 else $e end)];
def overlaps($o): .dayOfWeek == $o.dayOfWeek and (span as $a | ($o | span) as $b | $a[0] < $b[1] and $b[0] < $a[1]);
def copied: stored | {dayOfWeek, availabilityStart, availabilityEnd, threads, utilizationPercent,
  pacsId: (.pacsId // 0)};
'
pacs_of_url() { local p="${url#*/xapi/pacs/}"; printf '%s' "${p%%/*}"; }

status=200
body='{}'
case "$url" in
  *"/xapi/siteConfig/initialized")
    body="${INITIALIZED_BODY:-true}"
    ;;
  *"/xapi/pacs/"*"/availability/validate")
    pid=$(pacs_of_url)
    if [ -n "${AVAIL_VALIDATE_BODY:-}" ]; then
      body="$AVAIL_VALIDATE_BODY"
    else
      body=$(jq -c --argjson pid "$pid" --argjson e "$data" "$AVAIL_DEFS"'
        ($e | stored) as $n | [.[] | select(.pacsId == $pid and overlaps($n))] | length == 0' "$AVAIL_STATE")
    fi
    ;;
  *"/xapi/pacs/"*"/availability/"*)
    pid=$(pacs_of_url)
    id="${url##*/}"
    existing=$(jq -c --argjson id "$id" --argjson pid "$pid" \
      'map(select(.id == $id and .pacsId == $pid)) | .[0] // empty' "$AVAIL_STATE")
    if [ -z "$existing" ]; then
      status=404; body="No PACS availability entry with ID $id exists for PACS $pid"
    elif [ "$method" = "DELETE" ]; then
      edit "$AVAIL_STATE" --argjson id "$id" 'map(select(.id != $id))'
      body=true
    elif [ "$method" = "PUT" ]; then
      if ! jq -e '.dayOfWeek != null' <<< "$data" >/dev/null; then
        status=500; body='java.lang.NullPointerException'  # updatePacsAvailabilityInterval reads the body's day
      else
        fields=$(jq -c "$AVAIL_DEFS"' copied' <<< "$data")
        # A concurrent run got there first: the change is already stored when this PUT arrives.
        if [ -n "${AVAIL_PUT_ALREADY_APPLIED:-}" ]; then
          edit "$AVAIL_STATE" --argjson id "$id" --argjson n "$fields" 'map(if .id == $id then . + $n else . end)'
          existing=$(jq -c --argjson id "$id" 'map(select(.id == $id)) | .[0]' "$AVAIL_STATE")
        fi
        dirty=$(jq -n --argjson old "$existing" --argjson new "$fields" 'any($new | keys[]; $old[.] != $new[.])')
        if [ -n "${SWALLOW_AVAIL_PUT:-}" ]; then
          body="$existing"  # 200, nothing stored
        elif [ "$dirty" = "false" ]; then
          status=304; body=""
        else
          edit "$AVAIL_STATE" --argjson id "$id" --argjson n "$fields" 'map(if .id == $id then . + $n else . end)'
          body=$(jq -c --argjson id "$id" 'map(select(.id == $id)) | .[0]' "$AVAIL_STATE")
        fi
      fi
    else
      body="$existing"
    fi
    ;;
  *"/xapi/pacs/"*"/availability")
    pid=$(pacs_of_url)
    if [ "$method" = "POST" ] && [ -n "${AVAIL_STATUS:-}" ]; then
      status="$AVAIL_STATUS"
      body="${AVAIL_BODY:-{\}}"
    elif [ "$method" = "POST" ]; then
      # createPacsAvailabilityInterval: the PACS comes from the URL, and an overlap is a 400.
      n=$(jq -c --argjson pid "$pid" "$AVAIL_DEFS"' copied | .pacsId = $pid' <<< "$data")
      clash=$(jq --argjson n "$n" "$AVAIL_DEFS"'[.[] | select(.pacsId == $n.pacsId and overlaps($n))] | length' \
        "$AVAIL_STATE")
      if [ "$clash" != "0" ]; then
        status=400
        body="User admin tried to create an availability interval for PACS $pid but the interval was invalid"
        body+=" (probable overlap with existing interval)."
      else
        edit "$AVAIL_STATE" --argjson n "$n" '. + [$n + {id: ((map(.id) | max // 39) + 1), enabled: true}]'
        body=$(jq -c 'last' "$AVAIL_STATE")
      fi
    else
      # findAllByPacsIdGroupedByDayOfWeek: every day is a key, empty or not.
      body=$(jq -c --argjson pid "$pid" 'map(select(.pacsId == $pid)) as $mine
        | reduce ("MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY") as $d
            ({}; .[$d] = [$mine[] | select(.dayOfWeek == $d)])' "$AVAIL_STATE")
    fi
    ;;
  *"/xapi/pacs")
    if [ "$method" = "POST" ] && [ -z "$SWALLOW_PACS_POST" ]; then
      # XNAT assigns the id. Ours start at 7 so nothing can pass by assuming 1.
      edit "$PACS_STATE" --argjson id "$(( $(jq 'length' "$PACS_STATE") + 7 ))" --argjson e "$data" \
        '. + [$e + {id: $id}]'
    fi
    body=$(cat "$PACS_STATE")
    ;;
  *"/xapi/pacs/"*)
    id="${url##*/}"
    case "$method" in
      PUT) edit "$PACS_STATE" --argjson id "$id" --argjson e "$data" \
             'map(if .id == $id then $e + {id: $id} else . end)' ;;
      DELETE) edit "$PACS_STATE" --argjson id "$id" 'map(select(.id != $id))' ;;
    esac
    body=$(cat "$PACS_STATE")
    ;;
  *"/xapi/dicomscp")
    if [ "$method" = "POST" ]; then
      edit "$SCP_STATE" --argjson id "$(( $(jq 'length' "$SCP_STATE") + 5 ))" --argjson e "$data" \
        '. + [$e + {id: $id}]'
    fi
    body=$(cat "$SCP_STATE")
    ;;
  *"/xapi/dicomscp/"*)
    id="${url##*/}"
    [ "$method" = "DELETE" ] && edit "$SCP_STATE" --argjson id "$id" 'map(select(.id != $id))'
    body=$(cat "$SCP_STATE")
    ;;
esac

# Lets a test make one specific call fail, to exercise the error paths.
case "${FAIL_ON_URL:-__none__}" in
  __none__) ;;
  *) case "$url" in *"$FAIL_ON_URL"*) status="${FAIL_STATUS:-500}"; body='{"error":"stub failure"}' ;; esac ;;
esac

# XNAT refuses a password it does not hold (comma-separated list, e.g. the rotated-away initial one).
case ",${WRONG_LOGINS:-}," in
  *",${creds#*:},"*) [ -n "$creds" ] && { status=401; body='{"error":"unauthorized"}'; } ;;
esac

# A first boot still initialising its database: the first N logins fail whatever the password.
# The count covers every authenticated request, so keep N below the number of probe logins.
if [ -n "${UNREADY_LOGINS:-}" ] && [ -n "$creds" ]; then
  seen=$(( $(cat "$UNREADY_COUNT" 2>/dev/null || echo 0) + 1 ))
  echo "$seen" > "$UNREADY_COUNT"
  [ "$seen" -le "$UNREADY_LOGINS" ] && { status=401; body='{"error":"unauthorized"}'; }
fi

[ -n "$outfile" ] && [ "$outfile" != "/dev/null" ] && printf '%s' "$body" > "$outfile"
if [ "$status_only" = "1" ]; then printf '%s' "$status"; else printf '%s\n%s' "$body" "$status"; fi
case "$status" in 2*) exit 0 ;; esac
exit 0
"""

BASE_ENV = {
    "XNAT_ADMIN_USER": "admin",
    "XNAT_ADMIN_INITIAL_PASSWORD": "initial",  # pragma: allowlist secret
    "XNAT_ADMIN_PASSWORD": "rotated",  # pragma: allowlist secret
    "XNAT_SERVICE_USER": "flipServiceAccount",
    "XNAT_SERVICE_PASSWORD": "service",  # pragma: allowlist secret
    "XNAT_PORT": "8104",
    # The plugin-readiness wait polls until a DQR route answers, bounded only by wall clock. With
    # sleep stubbed out, a test that makes that route fail would spin at full speed for the default
    # 900s budget rather than failing; cap it so the harness can never hang on one.
    "XNAT_PLUGIN_READINESS_TIMEOUT_SECONDS": "5",
    "XNAT_PLUGIN_READINESS_POLL_SECONDS": "0",
}

# What the stub reports as already registered when a test does not say otherwise. Carries every
# kit-managed field the drift check compares (a live XNAT GET /xapi/pacs returns them all), with
# values matching the script's defaults so the entry reads as in-sync unless a test drifts one.
MOCK_PACS_REGISTRATION = (
    '[{"id":7,"aeTitle":"ORTHANC","host":"orthanc","queryRetrievePort":4242,'
    '"label":"Test PACS instance","supportsExtendedNegotiations":true}]'
)

# The SCP receivers a test can seed XNAT with, one JSON object each — reclamation is scoped by what
# created a receiver, so which of these survives is the whole point of those tests.
#
# FLIP-owned: created by an earlier run of this script, identified by the identifier it stamps on,
# whatever AE title and port it happens to carry. `GET /xapi/dicomscp` does return `identifier` per
# receiver (verified against a live XNAT), which is what makes ownership readable at all.
FLIP_OWNED_RECEIVER = '{"id":3,"aeTitle":"FLIPXNAT","port":8104,"identifier":"dqrObjectIdentifier"}'
# XNAT's own, created by the webapp on first boot: always titled "XNAT", never carrying FLIP's
# identifier.
STOCK_RECEIVER = '{"id":1,"aeTitle":"XNAT","port":8104,"identifier":"dicomObjectIdentifier"}'
# An operator's, registered by hand for some other local DICOM source: neither marker, so nothing
# here owns it.
OPERATOR_RECEIVER = '{"id":9,"aeTitle":"WARDCT","port":11113,"identifier":"dicomObjectIdentifier"}'
# The same unowned receiver, but squatting the port this deployment is about to bind. XNAT rejects a
# second receiver on a bound port, so this one cannot be worked around by registering alongside it.
SQUATTING_RECEIVER = '{"id":9,"aeTitle":"WARDCT","port":8104,"identifier":"dicomObjectIdentifier"}'


def run_configure(tmp_path, env_overrides=None, pacs_state=None, scp_state=None, avail_state=None):
    """Runs configure-xnat.sh against the stub and returns (exit code, payloads, combined output).

    Args:
        tmp_path: pytest tmp_path for the stub PATH and state files.
        env_overrides (dict | None): Environment for the run, layered over BASE_ENV.
        pacs_state (str | None): JSON array the stub starts with as XNAT's PACS registrations.
        scp_state (str | None): JSON array the stub starts with as XNAT's SCP receivers.
        avail_state (list | None): The availability intervals DQR starts with (see ``interval``).
            Read the schedule the run left behind with ``schedule_after``.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)  # parents: tests that run twice pass a nested tmp_path
    stub = bin_dir / "curl"
    stub.write_text(STUB_CURL)
    stub.chmod(0o755)
    # The script's fixed "wait for XNAT to settle" sleep is 10s of dead time per run, and the
    # readiness loops it guards are already satisfied instantly by the stub. Stubbing sleep keeps
    # the suite at seconds rather than minutes; nothing here is testing the waits.
    no_sleep = bin_dir / "sleep"
    no_sleep.write_text("#!/bin/sh\nexit 0\n")
    no_sleep.chmod(0o755)

    payloads = tmp_path / "payloads.txt"
    pacs_file = tmp_path / "pacs.json"
    pacs_file.write_text(pacs_state if pacs_state is not None else "[]")
    scp_file = tmp_path / "scp.json"
    # Default: XNAT's stock receiver, as the webapp creates it on first boot — title "XNAT" and the
    # plain DICOM identifier, not FLIP's.
    scp_file.write_text(scp_state if scp_state is not None else f"[{STOCK_RECEIVER}]")
    avail_file = tmp_path / "availability.json"
    avail_file.write_text(json.dumps(avail_state if avail_state is not None else []))

    env = {
        **os.environ,
        **BASE_ENV,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PAYLOADS": str(payloads),
        "PACS_STATE": str(pacs_file),
        "SCP_STATE": str(scp_file),
        "AVAIL_STATE": str(avail_file),
        "CREDS_LOG": str(tmp_path / "creds.txt"),
        "UNREADY_COUNT": str(tmp_path / "unready-count.txt"),
        **(env_overrides or {}),
    }

    result = subprocess.run(["bash", str(SCRIPT)], cwd=CONFIG_DIR, env=env, capture_output=True, text=True, timeout=120)
    body = payloads.read_text() if payloads.exists() else ""
    return result.returncode, body, result.stdout + result.stderr


def requests_made(payloads: str) -> list[tuple[str, str]]:
    """Every (method, url) the script issued, in order."""
    made = []
    for block in payloads.split("=== "):
        header = block.partition("\n")[0].split()
        if len(header) == 2:
            made.append((header[0], header[1]))
    return made


def body_for(payloads: str, endpoint: str) -> str:
    """The last raw request body sent to ``endpoint`` — for the payloads that are not objects."""
    found = None
    for block in payloads.split("=== "):
        header, _, rest = block.partition("\n")
        parts = header.split()
        if len(parts) == 2 and parts[1].endswith(endpoint) and rest.strip():
            found = rest.strip()
    assert found is not None, f"no body sent to {endpoint}"
    return found


def payload_for(payloads: str, endpoint: str) -> dict:
    """Returns the last JSON payload sent to ``endpoint``.

    Matched on the URL's path suffix rather than a substring: ``/xapi/pacs`` would otherwise also
    match ``/xapi/pacs/7/availability`` and return the wrong payload.
    """
    found = None
    for block in payloads.split("=== "):
        header, _, rest = block.partition("\n")
        parts = header.split()
        url = parts[1] if len(parts) == 2 else ""
        matches = url.endswith(endpoint) or (
            endpoint == "/xapi/pacs" and re.search(r"/xapi/pacs/\d+$", url) is not None
        )
        if matches and rest.strip().startswith("{"):
            found = json.loads(rest.strip())
    assert found is not None, f"no payload sent to {endpoint}"
    return found


WEEK = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def interval(id_, day, start="00:00", end="00:00", threads=1, util=100, pacs=7):
    """One DQR availability interval as GET {pacsId}/availability returns it.

    Times are in DQR's stored form, so the end of the day is ``00:00``, not the ``24:00`` a kit writes.
    """
    return {
        "id": id_,
        "pacsId": pacs,
        "dayOfWeek": day,
        "availabilityStart": start,
        "availabilityEnd": end,
        "threads": threads,
        "utilizationPercent": util,
        "enabled": True,
    }


# What a run with the shipped defaults leaves behind: all week, all day, one thread, 100%. The ids
# start at 40 so nothing passes by confusing an interval id with the PACS id (7).
DEFAULT_SCHEDULE = [interval(40 + i, day) for i, day in enumerate(WEEK)]


def availability_writes(payloads: str, method: str | None = None) -> list[tuple[str, str]]:
    """The writes the script made to DQR's schedule, in order: POST (create), PUT, DELETE.

    The ``validate`` POST is a read, so it is left out.
    """
    return [
        (m, u)
        for m, u in requests_made(payloads)
        if "/availability" in u
        and not u.endswith("/validate")
        and m in ("POST", "PUT", "DELETE")
        and (method is None or m == method)
    ]


def availability_bodies(payloads: str, method: str) -> list[tuple[str, dict]]:
    """(url, JSON body) for every ``method`` request to the availability API, in order."""
    found = []
    for block in payloads.split("=== "):
        header, _, rest = block.partition("\n")
        parts = header.split()
        if len(parts) == 2 and parts[0] == method and "/availability" in parts[1] and rest.strip().startswith("{"):
            found.append((parts[1], json.loads(rest.strip())))
    return found


def schedule_after(tmp_path) -> dict[str, list[dict]]:
    """DQR's schedule once the run has finished, by day, each day's intervals ordered by id."""
    intervals = json.loads((tmp_path / "availability.json").read_text())
    return {day: sorted((i for i in intervals if i["dayOfWeek"] == day), key=lambda i: i["id"]) for day in WEEK}


def window(item: dict) -> tuple:
    """The four fields the converge manages, for comparing intervals."""
    return (item["availabilityStart"], item["availabilityEnd"], item["threads"], item["utilizationPercent"])


def test_defaults_configure_the_mocked_orthanc(tmp_path):
    """An unconfigured deployment must still describe the mock exactly as before."""
    code, payloads, output = run_configure(tmp_path)
    assert code == 0, output

    pacs = payload_for(payloads, "/xapi/pacs")
    assert pacs["aeTitle"] == "ORTHANC"
    assert pacs["host"] == "orthanc"
    assert pacs["queryRetrievePort"] == 4242

    receiver = payload_for(payloads, "/xapi/dicomscp")
    assert receiver["aeTitle"] == "XNAT"
    assert receiver["port"] == 8104

    assert payload_for(payloads, "/xapi/dqr/settings")["dqrCallingAe"] == "XNAT"


def test_configured_pacs_and_ae_title_reach_xnat(tmp_path):
    """Every configured value must appear in what XNAT is actually sent."""
    code, payloads, output = run_configure(
        tmp_path,
        {
            "XNAT_AETITLE": "FLIPXNAT",
            "PACS_HOST": "10.0.0.10",
            "PACS_AETITLE": "SECTRA_QR",
            "PACS_QR_PORT": "8059",
            "PACS_LABEL": "GSTT Sectra PACS",
        },
    )
    assert code == 0, output

    pacs = payload_for(payloads, "/xapi/pacs")
    assert (pacs["aeTitle"], pacs["host"], pacs["queryRetrievePort"]) == ("SECTRA_QR", "10.0.0.10", 8059)
    assert pacs["label"] == "GSTT Sectra PACS"

    # The AE title has to reach all three places that must agree, or the C-STORE association the
    # PACS opens is addressed to a receiver that does not exist.
    assert payload_for(payloads, "/xapi/dicomscp")["aeTitle"] == "FLIPXNAT"
    assert payload_for(payloads, "/xapi/dqr/settings")["dqrCallingAe"] == "FLIPXNAT"


def test_throttle_settings_are_configurable(tmp_path):
    """The availability window and retry behaviour are the throttle for a production PACS."""
    code, payloads, output = run_configure(
        tmp_path,
        {
            "PACS_AVAILABILITY_DAYS": "SATURDAY,SUNDAY",
            "PACS_AVAILABILITY_START": "19:00",
            "PACS_AVAILABILITY_END": "07:00",
            "PACS_THREADS": "2",
            "PACS_UTILIZATION_PERCENT": "40",
            "DQR_MAX_PACS_REQUEST_ATTEMPTS": "25",
            "DQR_RETRY_WAIT_SECONDS": "120",
        },
    )
    assert code == 0, output

    dqr = payload_for(payloads, "/xapi/dqr/settings")
    assert dqr["dqrMaxPacsRequestAttempts"] == "25"
    assert dqr["dqrWaitToRetryRequestInSeconds"] == "120"

    availability = payload_for(payloads, "/availability")
    assert availability["availabilityStart"] == "19:00"
    assert availability["availabilityEnd"] == "07:00"
    assert availability["threads"] == 2
    assert availability["utilizationPercent"] == 40

    assert len(availability_writes(payloads, "POST")) == 2, "only the configured days should be scheduled"


def test_registration_updates_in_place_when_host_or_port_drift(tmp_path):
    """A kit change must not be silently ignored on redeploy, leaving DQR on the old PACS."""
    code, payloads, output = run_configure(
        tmp_path,
        {"PACS_HOST": "10.0.0.10", "PACS_QR_PORT": "8059"},
        pacs_state=MOCK_PACS_REGISTRATION,
    )
    assert code == 0, output
    assert "updating to 10.0.0.10:8059" in output

    pacs = payload_for(payloads, "/xapi/pacs")
    assert pacs["host"] == "10.0.0.10"
    assert pacs["queryRetrievePort"] == 8059


def test_registration_updates_in_place_when_flags_drift(tmp_path):
    """Drift in a kit-managed field other than host/port must also land, not log "leaving as-is".

    supportsExtendedNegotiations is the documented lever for a real PACS that rejects extended
    negotiation, and the k8s init job re-runs this script on every helm upgrade against persistent
    XNAT data — a host/port-only comparison keeps the old flag forever with no signal.
    """
    code, payloads, output = run_configure(
        tmp_path,
        {"PACS_SUPPORTS_EXTENDED_NEGOTIATIONS": "false"},
        pacs_state=MOCK_PACS_REGISTRATION,
    )
    assert code == 0, output
    assert "leaving as-is" not in output
    assert "supportsExtendedNegotiations" in output

    pacs = payload_for(payloads, "/xapi/pacs")
    assert pacs["supportsExtendedNegotiations"] is False
    assert pacs["host"] == "orthanc"
    assert pacs["queryRetrievePort"] == 4242


def test_matching_registration_is_left_alone(tmp_path):
    """An unchanged registration must not be rewritten on every redeploy."""
    code, payloads, output = run_configure(tmp_path, pacs_state=MOCK_PACS_REGISTRATION)
    assert code == 0, output
    assert "already registered at orthanc:4242 — leaving as-is" in output
    assert not [m for m, u in requests_made(payloads) if m in ("POST", "PUT") and u.endswith("/xapi/pacs")]


def test_availability_uses_the_resolved_pacs_id(tmp_path):
    """The schedule must be written against the real registration, not a hardcoded id of 1."""
    code, payloads, output = run_configure(tmp_path, pacs_state=MOCK_PACS_REGISTRATION)
    assert code == 0, output
    assert payload_for(payloads, "/availability")["pacsId"] == 7


@pytest.mark.parametrize("var", ["XNAT_AETITLE", "PACS_HOST", "PACS_AETITLE", "PACS_QR_PORT"])
def test_empty_values_fail_loudly(tmp_path, var):
    """An empty value would produce malformed JSON that XNAT rejects silently (FLIP#822/#862)."""
    code, _, output = run_configure(tmp_path, {var: ""})
    assert code != 0, f"empty {var} should abort the run"
    assert var in output


def deletes(payloads: str) -> list[str]:
    """URLs the script issued a DELETE against."""
    return [b.partition("\n")[0].split()[-1] for b in payloads.split("=== ") if b.startswith("DELETE ")]


def test_receiver_on_our_port_is_reclaimed_whatever_it_is_called(tmp_path):
    """Renaming the AE title must not strand the old receiver fighting for the same port."""
    code, payloads, output = run_configure(
        tmp_path,
        scp_state=f"[{FLIP_OWNED_RECEIVER}]",
    )
    assert code == 0, output
    assert "(id 3)" in output
    assert "FLIPXNAT" in output
    assert any(u.endswith("/xapi/dicomscp/3") for u in deletes(payloads))


def test_a_receiver_this_script_does_not_own_survives_the_reconcile(tmp_path):
    """An operator's second receiver must not be collateral damage of every redeploy.

    Reclamation is scoped to what FLIP created (identifier ``dqrObjectIdentifier``) plus XNAT's
    stock receiver. A receiver registered by hand for another local DICOM source carries neither
    marker, and deleting it would be silent: on Kubernetes the only record is a Job pod log the
    hook-delete-policy discards on success.
    """
    code, payloads, output = run_configure(
        tmp_path,
        scp_state=f"[{OPERATOR_RECEIVER}, {FLIP_OWNED_RECEIVER}, {STOCK_RECEIVER}]",
    )
    assert code == 0, output

    deleted = deletes(payloads)
    assert not any(u.endswith("/xapi/dicomscp/9") for u in deleted), "deleted a receiver FLIP does not own"
    assert "Removing SCP receiver 'WARDCT" not in output
    # The two it does own still go, or the re-created receiver fights the old one for the port.
    assert any(u.endswith("/xapi/dicomscp/3") for u in deleted), "left FLIP's own stale receiver behind"
    assert any(u.endswith("/xapi/dicomscp/1") for u in deleted), "left XNAT's stock receiver behind"


def test_a_receiver_squatting_our_port_is_refused_before_anything_is_deleted(tmp_path):
    """Refusing has to happen before the reclamation loop, not at the POST.

    An unowned receiver on the port this deployment binds is left alone by the reclamation above
    (correctly — it is not ours), and the POST that follows is then rejected by XNAT as a duplicate
    port. If the deletes have already run by that point, a redeploy that changed nothing else ends
    with FLIP's own working receiver gone and no replacement: strictly worse than the state it
    started in, and on Kubernetes the only record is a Job pod log discarded on success. The
    PACS-side guard further down refuses ahead of its deletes; this asserts the SCP side matches.
    """
    code, payloads, output = run_configure(
        tmp_path,
        scp_state=f"[{FLIP_OWNED_RECEIVER}, {SQUATTING_RECEIVER}]",
    )
    assert code != 0, "a squatted port should abort the run"
    assert not deletes(payloads), f"deleted something before refusing: {deletes(payloads)}"
    assert "WARDCT" in output, "the refusal must name the receiver holding the port"
    assert "8104" in output


def test_foreign_pacs_registrations_are_removed(tmp_path):
    """A trust XNAT retrieves from one PACS; a stale entry would leave DQR's choice ambiguous."""
    code, payloads, output = run_configure(
        tmp_path,
        {
            "PACS_AETITLE": "SECTRA_QR",
            "PACS_HOST": "10.0.0.10",
            "PACS_QR_PORT": "8059",
        },
        pacs_state='[{"id":1,"aeTitle":"ORTHANC","host":"orthanc","queryRetrievePort":4242}]',
    )
    assert code == 0, output
    assert "Removing PACS ORTHANC at orthanc:4242 (id 1)" in output
    assert any(u.endswith("/xapi/pacs/1") for u in deletes(payloads))


def test_receiver_is_reclaimed_when_port_and_title_both_change(tmp_path):
    """Matching on our port *or* our AE title left an orphan when both moved in one change."""
    code, payloads, output = run_configure(
        tmp_path,
        {"XNAT_PORT": "11112", "XNAT_AETITLE": "FLIPXNAT2"},
        scp_state=f"[{FLIP_OWNED_RECEIVER}]",
    )
    assert code == 0, output
    assert any(u.endswith("/xapi/dicomscp/3") for u in deletes(payloads)), (
        "the old receiver was left enabled and bound to its port"
    )


def test_refuses_to_delete_a_foreign_pacs_while_still_on_mock_defaults(tmp_path):
    """An unrelated redeploy must not delete a PACS the operator registered by hand."""
    code, payloads, output = run_configure(
        tmp_path,
        pacs_state='[{"id":1,"aeTitle":"SECTRA_QR","host":"10.0.0.10","queryRetrievePort":8059}]',
    )
    assert code != 0, "should refuse rather than delete a registration it may not own"
    assert "SECTRA_QR at 10.0.0.10:8059" in output
    assert not any("/xapi/pacs/1" in u for u in deletes(payloads)), "deleted it anyway"


def test_injected_json_in_a_pacs_value_cannot_change_other_fields(tmp_path):
    """Values are data, not JSON fragments: a crafted host must not flip defaultQueryRetrievePacs."""
    code, payloads, output = run_configure(
        tmp_path,
        {"PACS_HOST": 'x", "defaultQueryRetrievePacs": false, "z": "y'},
    )
    assert code == 0, output
    pacs = payload_for(payloads, "/xapi/pacs")
    assert pacs["defaultQueryRetrievePacs"] is True, "injected key won"
    assert pacs["host"] == 'x", "defaultQueryRetrievePacs": false, "z": "y'


def test_non_numeric_port_fails_naming_the_variable(tmp_path):
    """A bad port must fail here, not reach XNAT as an opaque 400."""
    code, _, output = run_configure(tmp_path, {"PACS_QR_PORT": "8059abc"})
    assert code != 0, "a non-numeric port was accepted"


def test_scp_receiver_binds_the_configured_port(tmp_path):
    """XNAT_PORT is the C-MOVE destination port; a receiver on any other port never gets the study."""
    code, payloads, output = run_configure(tmp_path, {"XNAT_PORT": "11112"})
    assert code == 0, output
    assert payload_for(payloads, "/xapi/dicomscp")["port"] == 11112


def test_scp_receiver_keeps_the_settings_the_dqr_import_path_depends_on(tmp_path):
    """These four are why the receiver is re-created rather than left at XNAT's defaults.

    ``dqrObjectIdentifier`` is what routes an arriving study to the project DQR requested it for;
    without ``directArchive`` + ``customProcessing`` the study lands in the prearchive and is never
    archived; ``anonymizationEnabled`` is what applies the site-wide anonymization script, so
    turning it off sends identifiable DICOM into the archive.
    """
    code, payloads, output = run_configure(tmp_path)
    assert code == 0, output

    receiver = payload_for(payloads, "/xapi/dicomscp")
    assert receiver["identifier"] == "dqrObjectIdentifier"
    assert receiver["directArchive"] is True
    assert receiver["customProcessing"] is True
    assert receiver["anonymizationEnabled"] is True
    assert receiver["enabled"] is True
    # Routing/whitelisting are off deliberately: FLIP routes by DQR's identifier, and a whitelist
    # here would silently drop studies the platform asked for.
    assert receiver["whitelistEnabled"] is False
    assert receiver["routingExpressionsEnabled"] is False


def test_site_wide_anonymization_is_uploaded_and_enabled(tmp_path):
    """The receiver's anonymizationEnabled only matters if the site script is on."""
    code, payloads, output = run_configure(tmp_path)
    assert code == 0, output
    assert body_for(payloads, "/xapi/anonymize/site/enabled") == "true"
    assert ("PUT", "http://xnat-web:8080/xapi/anonymize/site") in requests_made(payloads)


def test_dqr_stays_restricted_to_authorised_accounts(tmp_path):
    """allowAllUsersToUseDqr would let any XNAT account pull arbitrary studies from the trust PACS."""
    code, payloads, output = run_configure(tmp_path)
    assert code == 0, output
    assert payload_for(payloads, "/xapi/dqr/settings")["allowAllUsersToUseDqr"] is False


def test_guest_account_is_disabled(tmp_path):
    """An enabled guest is an unauthenticated reader of an archive holding patient imaging."""
    code, payloads, output = run_configure(tmp_path)
    assert code == 0, output
    assert ("PUT", "http://xnat-web:8080/xapi/users/guest/enabled/false") in requests_made(payloads)


def test_pacs_registration_carries_the_flags_dqr_selects_on(tmp_path):
    """defaultQueryRetrievePacs is how DQR picks this PACS, and imaging-api how it resolves the id."""
    code, payloads, output = run_configure(tmp_path)
    assert code == 0, output

    pacs = payload_for(payloads, "/xapi/pacs")
    assert pacs["defaultQueryRetrievePacs"] is True
    assert pacs["defaultStoragePacs"] is True
    assert pacs["queryable"] is True
    assert pacs["storable"] is True


def test_extended_negotiation_defaults_on_and_is_configurable(tmp_path):
    """A PACS without relational-query support rejects the association unless this is off."""
    code, payloads, output = run_configure(tmp_path)
    assert code == 0, output
    assert payload_for(payloads, "/xapi/pacs")["supportsExtendedNegotiations"] is True

    code, payloads, output = run_configure(tmp_path / "off", {"PACS_SUPPORTS_EXTENDED_NEGOTIATIONS": "false"})
    assert code == 0, output
    assert payload_for(payloads, "/xapi/pacs")["supportsExtendedNegotiations"] is False


@pytest.mark.parametrize("value", ["yes", "True", "1", ""])
def test_non_boolean_extended_negotiation_is_refused(tmp_path, value):
    """jq would take `1` as the number 1 and reject `yes` with a message naming neither the
    variable nor the accepted values."""
    code, _, output = run_configure(tmp_path, {"PACS_SUPPORTS_EXTENDED_NEGOTIATIONS": value})
    assert code != 0, f"{value!r} was accepted as a boolean"
    assert "PACS_SUPPORTS_EXTENDED_NEGOTIATIONS" in output


def test_availability_window_is_written_enabled(tmp_path):
    """A window is never written disabled.

    DQR 3.0.0 does not read an interval's ``enabled`` flag (only ``isAvailableNow``, which it computes
    from the clock and which has no setter, so the script no longer sends ``availableNow``), but a
    disabled write would be a trap the day a later DQR starts honouring it.
    """
    code, payloads, output = run_configure(tmp_path)
    assert code == 0, output

    availability = payload_for(payloads, "/availability")
    assert availability["enabled"] is True
    assert "availableNow" not in availability


def test_dqr_retry_and_poll_settings_reach_xnat(tmp_path):
    """The retry count and wait are the throttle a PACS team agrees to; a swap inverts it."""
    code, payloads, output = run_configure(
        tmp_path,
        {"DQR_MAX_PACS_REQUEST_ATTEMPTS": "25", "DQR_RETRY_WAIT_SECONDS": "120"},
    )
    assert code == 0, output

    dqr = payload_for(payloads, "/xapi/dqr/settings")
    assert dqr["dqrMaxPacsRequestAttempts"] == "25"
    assert dqr["dqrWaitToRetryRequestInSeconds"] == "120"
    assert dqr["pacsAvailabilityCheckFrequency"] == "1 minute"
    assert dqr["allowAllProjectsToUseDqr"] is True


def test_a_rejected_availability_window_fails_the_run(tmp_path):
    """A 400 that is not an overlap means the schedule was refused.

    Reporting it as applied is the worst outcome available: a trust that negotiated an
    out-of-hours window would be told it was in force while DQR retrieved around the clock.
    """
    code, _, output = run_configure(
        tmp_path,
        {"AVAIL_STATUS": "400", "AVAIL_BODY": '{"error":"Unknown day of week: FUNDAY"}'},
    )
    assert code != 0, "a refused availability window was reported as applied"
    assert "availability" in output.lower()


def test_a_server_error_on_availability_fails_the_run(tmp_path):
    code, _, output = run_configure(tmp_path, {"AVAIL_STATUS": "500", "AVAIL_BODY": '{"error":"boom"}'})
    assert code != 0, "a failed availability write was swallowed"


def test_an_overlap_refusal_is_a_failure_not_a_sign_the_window_is_in_place(tmp_path):
    """The old script read this 400 as "already configured", so a changed window never applied (FLIP#1455).

    The converge only creates on a day it has just found empty, after DQR's validate check agreed,
    so DQR refusing the create means the schedule changed under the run. That has to stop it.
    """
    code, _, output = run_configure(
        tmp_path,
        {
            "PACS_AVAILABILITY_DAYS": "MONDAY",
            "AVAIL_STATUS": "400",
            "AVAIL_BODY": "User admin tried to create an availability interval for PACS 7 but the interval was "
            "invalid (probable overlap with existing interval).",
        },
    )
    assert code != 0, "an overlap refusal was read as the window being in place"
    assert "HTTP 400" in output


def test_a_registration_that_did_not_take_fails_rather_than_configuring_nothing(tmp_path):
    """XNAT answering 200 without persisting is exactly how FLIP#822 stayed hidden."""
    code, _, output = run_configure(tmp_path, {"SWALLOW_PACS_POST": "1"})
    assert code != 0, "an unregistered PACS was treated as configured"
    assert "not registered" in output


def test_drift_is_corrected_in_place_rather_than_re_created(tmp_path):
    """A delete-and-recreate would change the PACS id under imaging-api's cache mid-flight."""
    code, payloads, output = run_configure(
        tmp_path,
        {"PACS_HOST": "10.0.0.10", "PACS_QR_PORT": "8059"},
        pacs_state=MOCK_PACS_REGISTRATION,
    )
    assert code == 0, output
    assert ("PUT", "http://xnat-web:8080/xapi/pacs/7") in requests_made(payloads)
    assert not [u for m, u in requests_made(payloads) if m == "DELETE" and "/xapi/pacs/" in u]


def test_credentials_are_not_echoed_when_a_call_fails(tmp_path):
    """The configure output is tee'd to a log file on the XNAT host."""
    # The password-rotation PUT: the one call whose -d body is itself a credential.
    code, _, output = run_configure(
        tmp_path,
        {"FAIL_ON_URL": "/xapi/users/admin", "FAIL_STATUS": "500"},
    )
    assert code != 0, "a 500 from XNAT did not abort the run"
    assert "<redacted>" in output, "the failing request was echoed without redaction"
    for secret in ("rotated", "initial", "service"):
        assert secret not in output, f"the {secret!r} password reached the configure log"


# ── Which admin password XNAT accepts picks the mode (FLIP#1204) ─────────────────────────────


def credentials_used(tmp_path, method: str, endpoint: str) -> list[str]:
    """The ``user:password`` every ``method`` request to ``endpoint`` authenticated with."""
    log = tmp_path / "creds.txt"
    used = []
    for line in log.read_text().splitlines() if log.exists() else []:
        made, url, creds = line.split(" ", 2)
        if made == method and url.endswith(endpoint):
            used.append(creds)
    return used


def test_a_configured_instance_converges_with_the_rotated_password(tmp_path):
    """upgrade-xnat redeploys onto a live data dir: the initial password is long gone."""
    code, payloads, output = run_configure(
        tmp_path, {"WRONG_LOGINS": "initial", "INITIALIZED_BODY": "true"}, pacs_state=MOCK_PACS_REGISTRATION
    )
    assert code == 0, output
    assert "converging" in output
    made = requests_made(payloads)
    assert not any(m == "PUT" and u.endswith("/xapi/users/admin") for m, u in made), "rotated a rotated password"
    assert credentials_used(tmp_path, "POST", "/xapi/siteConfig")[0] == "admin:rotated"  # the activation call
    assert any(m == "POST" and u.endswith("/xapi/dicomscp") for m, u in made), "receiver not re-applied"
    assert any(u.endswith("/xapi/dqr/settings") for _, u in made), "DQR lockdown not re-applied"


def test_a_migrated_instance_is_activated_with_the_rotated_password(tmp_path):
    """A 1.9→1.10 migration re-reports initialized=false; the initial password still does not work,
    so activating with it would 401 and leave XNAT stuck in /setup with no siteUrl."""
    code, payloads, output = run_configure(
        tmp_path, {"WRONG_LOGINS": "initial", "INITIALIZED_BODY": "false"}, pacs_state=MOCK_PACS_REGISTRATION
    )
    assert code == 0, output
    assert "migrated" in output
    assert credentials_used(tmp_path, "POST", "/xapi/siteConfig")[0] == "admin:rotated"  # the activation call
    assert not any(m == "PUT" and u.endswith("/xapi/users/admin") for m, u in requests_made(payloads))


def test_first_boot_activates_with_the_initial_password_and_rotates_it(tmp_path):
    code, payloads, output = run_configure(tmp_path, pacs_state=MOCK_PACS_REGISTRATION)
    assert code == 0, output
    assert credentials_used(tmp_path, "POST", "/xapi/siteConfig")[0] == "admin:initial"  # the activation call
    assert credentials_used(tmp_path, "PUT", "/xapi/users/admin") == ["admin:initial"]


def test_neither_password_working_stops_before_touching_xnat(tmp_path):
    code, payloads, output = run_configure(tmp_path, {"WRONG_LOGINS": "initial,rotated"})
    assert code == 1, output
    assert "neither admin password authenticates" in output
    assert not any(m in {"POST", "PUT", "DELETE"} for m, _ in requests_made(payloads))


# ── First boot: the login page answers before the database is ready (FLIP#1384) ──────────────


def logins_probed(tmp_path) -> int:
    """How many admin logins the password probes made (each failed one counts toward the lockout)."""
    return len(credentials_used(tmp_path, "GET", "/xapi/siteConfig/initialized"))


def test_first_boot_waits_out_the_window_where_no_password_works(tmp_path):
    # Two rounds of 401/401 (four logins), then XNAT is ready and the initial password works.
    code, _, output = run_configure(tmp_path, {"UNREADY_LOGINS": "4"}, pacs_state=MOCK_PACS_REGISTRATION)
    assert code == 0, output
    assert output.count("Retrying in") == 2, output
    assert "attempt 2/6" in output
    assert "attempt 3/6" not in output
    assert credentials_used(tmp_path, "POST", "/xapi/siteConfig")[0] == "admin:initial"  # the activation call


@pytest.mark.parametrize(
    ("initialized", "mode_message"), [("true", "already configured"), ("false", "migrated from an older XNAT")]
)
def test_a_configured_instance_still_converges_after_the_unready_window(tmp_path, initialized, mode_message):
    """An upgrade onto a live data dir can hit the same window; the rotated password must still win."""
    code, _, output = run_configure(
        tmp_path,
        {"WRONG_LOGINS": "initial", "UNREADY_LOGINS": "4", "INITIALIZED_BODY": initialized},
        pacs_state=MOCK_PACS_REGISTRATION,
    )
    assert code == 0, output
    assert output.count("Retrying in") == 2, output
    assert mode_message in output
    assert credentials_used(tmp_path, "POST", "/xapi/siteConfig")[0] == "admin:rotated"  # the activation call


def test_a_wrong_password_fails_well_short_of_the_xnat_lockout(tmp_path):
    """XNAT locks the account after about 20 failed logins; the retries must not get it there."""
    code, payloads, output = run_configure(tmp_path, {"WRONG_LOGINS": "initial,rotated"})
    assert code == 1, output
    assert "after 6 attempt(s)" in output
    assert "locks the account" in output
    assert logins_probed(tmp_path) == 12
    assert not any(m in {"POST", "PUT", "DELETE"} for m, _ in requests_made(payloads))


def test_identical_passwords_are_probed_once_per_round(tmp_path):
    """The dev kits set both passwords alike; a second probe of the same password is a wasted failed login."""
    code, _, output = run_configure(
        tmp_path,
        {"WRONG_LOGINS": "initial", "XNAT_ADMIN_PASSWORD": "initial"},  # pragma: allowlist secret
    )
    assert code == 1, output
    assert logins_probed(tmp_path) == 6


@pytest.mark.parametrize("value", ["0", "9", "6s", "1.5", "08", "abc", ""])
def test_an_unsafe_attempt_count_is_refused_before_any_login(tmp_path, value):
    """A count outside 1-8 could pass the lockout, and a non-number would never end the loop."""
    code, _, output = run_configure(tmp_path, {"WRONG_LOGINS": "initial,rotated", "XNAT_AUTH_PROBE_ATTEMPTS": value})
    if value == "":  # unset → the default of 6
        assert "after 6 attempt(s)" in output
        return
    assert code == 1, output
    assert "XNAT_AUTH_PROBE_ATTEMPTS must be" in output
    assert logins_probed(tmp_path) == 0


@pytest.mark.parametrize("value", ["20s", "0", "00", "-1", "1.5"])
def test_an_unusable_wait_is_refused_before_any_login(tmp_path, value):
    """A zero wait would spend the failed logins without giving XNAT any time to initialise."""
    code, _, output = run_configure(tmp_path, {"XNAT_AUTH_PROBE_WAIT_SECONDS": value})
    assert code == 1, output
    assert "XNAT_AUTH_PROBE_WAIT_SECONDS must be" in output
    assert logins_probed(tmp_path) == 0


@pytest.mark.parametrize(
    ("status", "hint"), [("000", "Nothing answered"), ("503", "server error"), ("401", "locks the account")]
)
def test_the_final_error_points_at_the_cause(tmp_path, status, hint):
    code, _, output = run_configure(
        tmp_path,
        {"FAIL_ON_URL": "/xapi/siteConfig/initialized", "FAIL_STATUS": status, "XNAT_AUTH_PROBE_ATTEMPTS": "2"},
    )
    assert code == 1, output
    assert f"initial: HTTP {status}, configured: HTTP {status}" in output
    assert hint in output


# ── The availability schedule converges onto the kit (FLIP#1455) ──────────────────────────────
#
# The old step POSTed every day on every run and read DQR's 400 "probable overlap" as "already
# configured", so on a live trust a changed window, thread count or utilisation never applied. These
# run the script against a DQR that starts with a schedule, and assert both the writes and what DQR
# holds afterwards.


@pytest.mark.parametrize(
    ("var", "value", "field", "stored"),
    [
        ("PACS_AVAILABILITY_START", "19:00", "availabilityStart", "19:00"),
        ("PACS_AVAILABILITY_END", "18:30", "availabilityEnd", "18:30"),
        ("PACS_THREADS", "3", "threads", 3),
        ("PACS_UTILIZATION_PERCENT", "40", "utilizationPercent", 40),
    ],
)
def test_a_changed_window_is_applied_in_place(tmp_path, var, value, field, stored):
    """Each throttle field the kit sets reaches a live schedule, by PUT on the existing interval."""
    code, payloads, output = run_configure(
        tmp_path, {var: value}, pacs_state=MOCK_PACS_REGISTRATION, avail_state=DEFAULT_SCHEDULE
    )
    assert code == 0, output

    expected = [("PUT", f"http://xnat-web:8080/xapi/pacs/7/availability/{40 + i}") for i in range(7)]
    assert availability_writes(payloads) == expected, "expected one in-place update per day and nothing else"
    assert f"drifted on {field}" in output

    after = schedule_after(tmp_path)
    for i, day in enumerate(WEEK):
        assert [item["id"] for item in after[day]] == [40 + i], f"{day} was not updated in place"
        assert after[day][0][field] == stored, f"{day} still has the old {field}"


def test_an_update_names_its_day_and_pacs(tmp_path):
    """DQR's PUT takes the day and the PACS from the body, not the URL.

    Without the day it fails with a NullPointerException. Without pacsId it reads 0 as a change and
    moves the interval to PACS 0, where this PACS's schedule no longer lists it.
    """
    code, payloads, output = run_configure(
        tmp_path, {"PACS_THREADS": "2"}, pacs_state=MOCK_PACS_REGISTRATION, avail_state=DEFAULT_SCHEDULE
    )
    assert code == 0, output

    puts = availability_bodies(payloads, "PUT")
    assert len(puts) == 7
    for (url, body), day in zip(puts, WEEK, strict=True):
        assert body["dayOfWeek"] == day, url
        assert body["pacsId"] == 7, url
    assert all(item["pacsId"] == 7 for items in schedule_after(tmp_path).values() for item in items)


def test_an_unchanged_schedule_is_left_untouched(tmp_path):
    """A re-run that changes nothing writes nothing, including against the 24:00 that DQR stores as 00:00."""
    code, payloads, output = run_configure(tmp_path, pacs_state=MOCK_PACS_REGISTRATION, avail_state=DEFAULT_SCHEDULE)
    assert code == 0, output

    assert availability_writes(payloads) == []
    assert not [u for m, u in requests_made(payloads) if u.endswith("/availability/validate")]
    assert output.count("already 00:00-24:00, 1 thread(s), 100%; leaving as-is") == 7
    assert "PACS availability schedule matches the kit" in output
    assert schedule_after(tmp_path) == {day: [item] for day, item in zip(WEEK, DEFAULT_SCHEDULE, strict=True)}


def test_a_removed_day_is_deleted(tmp_path):
    """Dropping a day from the kit stops retrieval on it, rather than leaving the old interval running."""
    code, payloads, output = run_configure(
        tmp_path,
        {"PACS_AVAILABILITY_DAYS": "MONDAY,TUESDAY,WEDNESDAY,THURSDAY,FRIDAY"},
        pacs_state=MOCK_PACS_REGISTRATION,
        avail_state=DEFAULT_SCHEDULE,
    )
    assert code == 0, output

    assert availability_writes(payloads) == [
        ("DELETE", "http://xnat-web:8080/xapi/pacs/7/availability/45"),
        ("DELETE", "http://xnat-web:8080/xapi/pacs/7/availability/46"),
    ]
    assert "SATURDAY is no longer in PACS_AVAILABILITY_DAYS" in output
    after = schedule_after(tmp_path)
    assert after["SATURDAY"] == []
    assert after["SUNDAY"] == []
    assert [after[day][0]["id"] for day in WEEK[:5]] == [40, 41, 42, 43, 44]


def test_a_missing_day_is_created_after_dqr_validates_it(tmp_path):
    """A day with no interval gets one, and DQR's own overlap check is asked before each create."""
    code, payloads, output = run_configure(
        tmp_path, pacs_state=MOCK_PACS_REGISTRATION, avail_state=[interval(40, "MONDAY")]
    )
    assert code == 0, output

    assert availability_writes(payloads) == [("POST", "http://xnat-web:8080/xapi/pacs/7/availability")] * 6
    created = [
        body["dayOfWeek"] for url, body in availability_bodies(payloads, "POST") if url.endswith("/availability")
    ]
    assert created == WEEK[1:]
    made = requests_made(payloads)
    creates = [i for i, (m, u) in enumerate(made) if m == "POST" and u.endswith("/availability")]
    for i in creates:
        assert made[i - 1] == ("POST", "http://xnat-web:8080/xapi/pacs/7/availability/validate"), (
            "a create was not preceded by DQR's validate check"
        )
    after = schedule_after(tmp_path)
    assert after["MONDAY"] == [interval(40, "MONDAY")], "the day that already matched was rewritten"
    assert all(len(after[day]) == 1 for day in WEEK)


def test_a_new_pacs_gets_the_whole_schedule(tmp_path):
    """Registering through the API pre-creates nothing (only DQR's admin UI does), so every day is created."""
    code, payloads, output = run_configure(tmp_path)
    assert code == 0, output
    assert len(availability_writes(payloads, "POST")) == 7
    assert all(
        window(items[0]) == ("00:00", "00:00", 1, 100) and len(items) == 1
        for items in schedule_after(tmp_path).values()
    )


def test_a_second_interval_on_a_listed_day_is_removed(tmp_path):
    """An extra window on a listed day widens retrieval beyond the agreed one. The matching one stays."""
    code, payloads, output = run_configure(
        tmp_path,
        {"PACS_AVAILABILITY_DAYS": "MONDAY", "PACS_AVAILABILITY_START": "18:00"},
        pacs_state=MOCK_PACS_REGISTRATION,
        avail_state=[interval(40, "MONDAY", end="08:00"), interval(41, "MONDAY", start="18:00")],
    )
    assert code == 0, output
    assert availability_writes(payloads) == [("DELETE", "http://xnat-web:8080/xapi/pacs/7/availability/40")]
    assert "removing extra interval 40" in output
    assert [item["id"] for item in schedule_after(tmp_path)["MONDAY"]] == [41]


def test_with_no_matching_interval_the_oldest_is_updated_and_the_rest_removed(tmp_path):
    code, payloads, output = run_configure(
        tmp_path,
        {"PACS_AVAILABILITY_DAYS": "MONDAY", "PACS_AVAILABILITY_START": "09:00", "PACS_AVAILABILITY_END": "17:00"},
        pacs_state=MOCK_PACS_REGISTRATION,
        avail_state=[interval(40, "MONDAY", end="08:00"), interval(41, "MONDAY", start="18:00")],
    )
    assert code == 0, output
    assert availability_writes(payloads) == [
        ("DELETE", "http://xnat-web:8080/xapi/pacs/7/availability/41"),
        ("PUT", "http://xnat-web:8080/xapi/pacs/7/availability/40"),
    ]
    assert [window(item) for item in schedule_after(tmp_path)["MONDAY"]] == [("09:00", "17:00", 1, 100)]


def test_removals_run_before_updates_and_updates_before_creations(tmp_path):
    """A failure part-way must leave less retrieval scheduled than agreed, never more."""
    code, payloads, output = run_configure(
        tmp_path,
        {"PACS_AVAILABILITY_DAYS": "MONDAY,TUESDAY", "PACS_AVAILABILITY_START": "08:00"},
        pacs_state=MOCK_PACS_REGISTRATION,
        avail_state=[interval(40, "MONDAY"), interval(45, "SATURDAY")],
    )
    assert code == 0, output
    assert [m for m, _ in availability_writes(payloads)] == ["DELETE", "PUT", "POST"]


def test_a_window_dqr_says_would_overlap_is_not_created(tmp_path):
    """validate answering false stops the run before the create, naming the day."""
    code, payloads, output = run_configure(
        tmp_path,
        {"PACS_AVAILABILITY_DAYS": "MONDAY", "AVAIL_VALIDATE_BODY": "false"},
        pacs_state=MOCK_PACS_REGISTRATION,
    )
    assert code != 0, "a window DQR refused was created anyway"
    assert availability_writes(payloads) == []
    assert "validate check refused the MONDAY window" in output


def test_a_not_modified_answer_to_an_update_is_accepted(tmp_path):
    """DQR answers 304 to a PUT with nothing to change. That is not a failure when the window is in place.

    The converge only PUTs a day it saw drift on, so a 304 means something else (a second run, an
    admin) got there between the read and the write. The read-back at the end is what decides.
    """
    code, payloads, output = run_configure(
        tmp_path,
        {"PACS_THREADS": "2", "AVAIL_PUT_ALREADY_APPLIED": "1"},
        pacs_state=MOCK_PACS_REGISTRATION,
        avail_state=DEFAULT_SCHEDULE,
    )
    assert code == 0, output
    assert "304 Not Modified" in output
    assert "request failed" not in output
    assert all(item["threads"] == 2 for items in schedule_after(tmp_path).values() for item in items)


def test_a_write_that_did_not_take_fails_the_run(tmp_path):
    """XNAT answering 200 without storing is how FLIP#822 stayed hidden, so the schedule is read back."""
    code, _, output = run_configure(
        tmp_path,
        {"PACS_THREADS": "2", "SWALLOW_AVAIL_PUT": "1"},
        pacs_state=MOCK_PACS_REGISTRATION,
        avail_state=DEFAULT_SCHEDULE,
    )
    assert code != 0, "a schedule that did not change was reported as matching the kit"
    assert "still differs from the kit" in output
    assert "update\tMONDAY\t40" in output


def test_a_second_run_changes_nothing(tmp_path):
    """Whatever the first run wrote, the next one reads back as matching. Covers H:MM and listing a day twice."""
    env = {"PACS_AVAILABILITY_DAYS": "MONDAY, TUESDAY,MONDAY", "PACS_AVAILABILITY_START": "7:30"}
    code, payloads, output = run_configure(tmp_path / "first", env, pacs_state=MOCK_PACS_REGISTRATION)
    assert code == 0, output
    assert len(availability_writes(payloads, "POST")) == 2, "a day listed twice was scheduled twice"
    first = json.loads((tmp_path / "first" / "availability.json").read_text())
    assert {(i["dayOfWeek"], i["availabilityStart"], i["availabilityEnd"]) for i in first} == {
        ("MONDAY", "07:30", "00:00"),
        ("TUESDAY", "07:30", "00:00"),
    }

    code, payloads, output = run_configure(
        tmp_path / "second", env, pacs_state=MOCK_PACS_REGISTRATION, avail_state=first
    )
    assert code == 0, output
    assert availability_writes(payloads) == []


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("PACS_AVAILABILITY_DAYS", "MONDAY,FUNDAY"),
        ("PACS_AVAILABILITY_DAYS", "monday"),
        ("PACS_AVAILABILITY_DAYS", ","),
        ("PACS_AVAILABILITY_START", "25:00"),
        ("PACS_AVAILABILITY_START", "7"),
        ("PACS_AVAILABILITY_START", "07:00:00"),
        ("PACS_AVAILABILITY_END", "07:60"),
        ("PACS_AVAILABILITY_END", "24:30"),
        ("PACS_THREADS", "0"),
        ("PACS_THREADS", "two"),
        ("PACS_THREADS", "1.5"),
        ("PACS_UTILIZATION_PERCENT", "0"),
        ("PACS_UTILIZATION_PERCENT", "101"),
        ("PACS_UTILIZATION_PERCENT", "50%"),
    ],
)
def test_an_invalid_schedule_is_refused_before_xnat_is_touched(tmp_path, var, value):
    """The converge removes before it writes, so a value DQR refused part-way would leave days unscheduled."""
    code, payloads, output = run_configure(tmp_path, {var: value}, avail_state=DEFAULT_SCHEDULE)
    assert code != 0, f"{var}={value!r} was accepted"
    assert var in output
    assert requests_made(payloads) == [], "XNAT was contacted before the schedule was validated"


def test_no_branch_reads_dqr_error_text_for_overlap():
    """The decision is DQR's validate endpoint and the read-back, never matching words in an error body.

    Comments may explain overlap. No line of code may mention it, so a grep for it cannot come back.
    """
    code_lines = [
        (number, line)
        for number, line in enumerate(SCRIPT.read_text().splitlines(), start=1)
        if line.strip() and not line.strip().startswith("#")
    ]
    offending = [f"{number}: {line.strip()}" for number, line in code_lines if "overlap" in line.lower()]
    assert not offending, "configure-xnat.sh decides on error text again:\n" + "\n".join(offending)
