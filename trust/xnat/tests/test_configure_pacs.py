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
url=""; data=""; method="GET"; status_only=0; outfile=""
prev=""
for a in "$@"; do
  case "$prev" in -d) data="$a";; -X) method="$a";; -o|--output) outfile="$a";; esac
  case "$a" in http*) url="$a";; '%{http_code}') status_only=1;; esac
  prev="$a"
done

# Every request, not only those carrying a body: a PUT whose whole meaning is its URL
# (/xapi/users/guest/enabled/false) is otherwise invisible to the tests.
printf '%s\n' "=== $method $url" >> "$PAYLOADS"
[ -n "$data" ] && printf '%s\n' "$data" >> "$PAYLOADS"

edit() {  # edit <state-file> <jq-filter> [args...]
  local f="$1"; shift
  jq "$@" "$f" > "$f.tmp" && mv "$f.tmp" "$f"
}

status=200
body='{}'
case "$url" in
  *"/xapi/pacs/"*"/availability")
    status="${AVAIL_STATUS:-200}"
    body="${AVAIL_BODY:-{\}}"
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

[ -n "$outfile" ] && [ "$outfile" != "/dev/null" ] && printf '%s' "$body" > "$outfile"
if [ "$status_only" = "1" ]; then printf '%s' "$status"; else printf '%s\n%s' "$body" "$status"; fi
case "$status" in 2*) exit 0 ;; esac
exit 0
"""

BASE_ENV = {
    "XNAT_ADMIN_USER": "admin",
    "XNAT_ADMIN_INITIAL_PASSWORD": "initial",
    "XNAT_ADMIN_PASSWORD": "rotated",
    "XNAT_SERVICE_USER": "flipServiceAccount",
    "XNAT_SERVICE_PASSWORD": "service",
    "XNAT_PORT": "8104",
    # The plugin-readiness wait polls until a DQR route answers, bounded only by wall clock. With
    # sleep stubbed out, a test that makes that route fail would spin at full speed for the default
    # 900s budget rather than failing; cap it so the harness can never hang on one.
    "XNAT_PLUGIN_READINESS_TIMEOUT_SECONDS": "5",
    "XNAT_PLUGIN_READINESS_POLL_SECONDS": "0",
}

# What the stub reports as already registered when a test does not say otherwise.
MOCK_PACS_REGISTRATION = '[{"id":7,"aeTitle":"ORTHANC","host":"orthanc","queryRetrievePort":4242}]'


def run_configure(tmp_path, env_overrides=None, pacs_state=None, scp_state=None):
    """Runs configure-xnat.sh against the stub and returns (exit code, payloads, combined output).

    Args:
        tmp_path: pytest tmp_path for the stub PATH and state files.
        env_overrides (dict | None): Environment for the run, layered over BASE_ENV.
        pacs_state (str | None): JSON array the stub starts with as XNAT's PACS registrations.
        scp_state (str | None): JSON array the stub starts with as XNAT's SCP receivers.
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
    scp_file.write_text(scp_state if scp_state is not None else '[{"id":1,"aeTitle":"XNAT","port":8104}]')

    env = {
        **os.environ,
        **BASE_ENV,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PAYLOADS": str(payloads),
        "PACS_STATE": str(pacs_file),
        "SCP_STATE": str(scp_file),
        **(env_overrides or {}),
    }

    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=CONFIG_DIR, env=env, capture_output=True, text=True, timeout=120
    )
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

    assert output.count("Setting PACS availability for") == 2, "only the configured days should be scheduled"


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
        scp_state='[{"id":3,"aeTitle":"FLIPXNAT","port":8104}]',
    )
    assert code == 0, output
    assert "(id 3)" in output
    assert "FLIPXNAT" in output
    assert any(u.endswith("/xapi/dicomscp/3") for u in deletes(payloads))


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
        scp_state='[{"id":3,"aeTitle":"FLIPXNAT","port":8104}]',
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


def test_availability_window_is_enabled_and_live(tmp_path):
    """A window written disabled is a schedule that silently never applies."""
    code, payloads, output = run_configure(tmp_path)
    assert code == 0, output

    availability = payload_for(payloads, "/availability")
    assert availability["enabled"] is True
    assert availability["availableNow"] is True


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


def test_an_overlapping_availability_interval_is_tolerated(tmp_path):
    """DQR pre-creates intervals at registration, so this specific 400 is not a failure."""
    code, _, output = run_configure(
        tmp_path,
        {
            "PACS_AVAILABILITY_DAYS": "MONDAY",
            "AVAIL_STATUS": "400",
            "AVAIL_BODY": '{"error":"probable overlap with existing interval"}',
        },
    )
    assert code == 0, output
    assert "already exists" in output


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
