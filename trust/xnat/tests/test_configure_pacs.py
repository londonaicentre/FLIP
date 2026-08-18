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

# Answers the two listings the script parses. GET /xapi/pacs returns nothing until a POST has been
# seen, so a single run exercises the register-then-resolve path; seeding the marker file up front
# makes the same stub return an already-registered PACS instead.
STUB_CURL = r"""#!/bin/bash
url=""; data=""; method="GET"; status_only=0; outfile=""
prev=""
for a in "$@"; do
  case "$prev" in -d) data="$a";; -X) method="$a";; -o|--output) outfile="$a";; esac
  case "$a" in http*) url="$a";; '%{http_code}') status_only=1;; esac
  prev="$a"
done
if [ -n "$data" ]; then
  printf '%s\n' "=== $method $url" >> "$PAYLOADS"
  printf '%s\n' "$data" >> "$PAYLOADS"
fi
[ "$method" = "DELETE" ] && printf '%s\n' "=== DELETE $url" >> "$PAYLOADS"
body='{}'
case "$url" in
  *"/xapi/dicomscp"*) body="${STUB_SCP_JSON}" ;;
  *"/xapi/pacs")
    if [ -f "$REGISTERED" ]; then
      body='[{"id":'"$STUB_PACS_ID"',"aeTitle":"'"$STUB_PACS_AET"'","host":"'"$STUB_PACS_HOST"'","queryRetrievePort":'"$STUB_PACS_PORT"'}]'
    else
      body="${STUB_PACS_JSON:-[]}"
    fi
    [ "$method" = "POST" ] && touch "$REGISTERED"
    ;;
esac
[ -n "$outfile" ] && [ "$outfile" != "/dev/null" ] && printf '%s' "$body" > "$outfile"
if [ "$status_only" = "1" ]; then printf '200'; else printf '%s\n200' "$body"; fi
exit 0
"""

BASE_ENV = {
    "XNAT_ADMIN_USER": "admin",
    "XNAT_ADMIN_INITIAL_PASSWORD": "initial",
    "XNAT_ADMIN_PASSWORD": "rotated",
    "XNAT_SERVICE_USER": "flipServiceAccount",
    "XNAT_SERVICE_PASSWORD": "service",
    "XNAT_PORT": "8104",
}


def run_configure(tmp_path, env_overrides=None, pacs_already_registered=False):
    """Runs configure-xnat.sh against the stub and returns (exit code, payloads, combined output)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "curl"
    stub.write_text(STUB_CURL)
    stub.chmod(0o755)

    payloads = tmp_path / "payloads.txt"
    registered = tmp_path / "registered"
    if pacs_already_registered:
        registered.touch()

    env = {
        **os.environ,
        **BASE_ENV,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PAYLOADS": str(payloads),
        "REGISTERED": str(registered),
        # What the stub reports as registered. Defaults to the mock; when a test configures a
        # different PACS the stub echoes that back, mimicking XNAT after the POST succeeded.
        "STUB_SCP_JSON": '[{"id":1,"aeTitle":"XNAT","port":8104}]',
        "STUB_PACS_JSON": "[]",
        "STUB_PACS_ID": "7",
        "STUB_PACS_AET": (env_overrides or {}).get("PACS_AETITLE", "ORTHANC"),
        "STUB_PACS_HOST": "orthanc" if pacs_already_registered else (env_overrides or {}).get("PACS_HOST", "orthanc"),
        "STUB_PACS_PORT": "4242" if pacs_already_registered else (env_overrides or {}).get("PACS_QR_PORT", "4242"),
        **(env_overrides or {}),
    }

    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=CONFIG_DIR, env=env, capture_output=True, text=True, timeout=120
    )
    body = payloads.read_text() if payloads.exists() else ""
    return result.returncode, body, result.stdout + result.stderr


def payload_for(payloads: str, endpoint: str) -> dict:
    """Returns the last JSON payload sent to ``endpoint``.

    Matched on the URL's path suffix rather than a substring: ``/xapi/pacs`` would otherwise also
    match ``/xapi/pacs/7/availability`` and return the wrong payload.
    """
    found = None
    for block in payloads.split("=== "):
        header, _, rest = block.partition("\n")
        url = header.split()[-1] if header.split() else ""
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
        pacs_already_registered=True,  # stub reports ORTHANC at orthanc:4242
    )
    assert code == 0, output
    assert "updating to 10.0.0.10:8059" in output

    pacs = payload_for(payloads, "/xapi/pacs")
    assert pacs["host"] == "10.0.0.10"
    assert pacs["queryRetrievePort"] == 8059


def test_matching_registration_is_left_alone(tmp_path):
    """An unchanged registration must not be rewritten on every redeploy."""
    code, _, output = run_configure(tmp_path, pacs_already_registered=True)
    assert code == 0, output
    assert "already registered at orthanc:4242 — leaving as-is" in output


def test_availability_uses_the_resolved_pacs_id(tmp_path):
    """The schedule must be written against the real registration, not a hardcoded id of 1."""
    code, payloads, output = run_configure(tmp_path, pacs_already_registered=True)
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
        {"STUB_SCP_JSON": '[{"id":3,"aeTitle":"FLIPXNAT","port":8104}]'},
    )
    assert code == 0, output
    assert "Removing SCP receiver 'FLIPXNAT' (id 3)" in output
    assert any(u.endswith("/xapi/dicomscp/3") for u in deletes(payloads))


def test_foreign_pacs_registrations_are_removed(tmp_path):
    """A trust XNAT retrieves from one PACS; a stale entry would leave DQR's choice ambiguous."""
    code, payloads, output = run_configure(
        tmp_path,
        {
            "PACS_AETITLE": "SECTRA_QR",
            "PACS_HOST": "10.0.0.10",
            "PACS_QR_PORT": "8059",
            "STUB_PACS_JSON": '[{"id":1,"aeTitle":"ORTHANC","host":"orthanc","queryRetrievePort":4242}]',
        },
    )
    assert code == 0, output
    assert "Removing PACS 'ORTHANC' at orthanc:4242 (id 1)" in output
    assert any(u.endswith("/xapi/pacs/1") for u in deletes(payloads))
