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
"""Execute configure-xnat.sh through first-boot DB/auth races (FLIP#1319).

Only curl and sleep are substituted. Sleep advances Bash's SECONDS in the script's own shell,
so the real shared 900s deadline and backoff run without a fifteen-minute test or a live XNAT.
"""

from pathlib import Path

import pytest

from test_configure_pacs import (
    MOCK_PACS_REGISTRATION,
    STUB_CURL,
    credentials_used,
    requests_made,
    run_configure,
)

READINESS_CURL = (
    STUB_CURL.replace(
        '    body="${INITIALIZED_BODY:-true}"',
        r"""    body="${INITIALIZED_BODY:-true}"
    count=$(grep -c '/xapi/siteConfig/initialized' "$PAYLOADS")
    if [ -n "$AUTH_SEQUENCE" ]; then
      IFS=',' read -r -a sequence <<< "$AUTH_SEQUENCE"
      status="${sequence[count-1]:-${AUTH_LAST_STATUS:-200}}"
    elif [ -n "$AUTH_PRIMARY_READY_AT" ]; then
      if [ "$creds" = "admin:initial" ]; then
        [ "${AUTH_NOW:-0}" -ge "$AUTH_PRIMARY_READY_AT" ] || status=401
      elif [ "$count" = "2" ]; then
        status=500
      else
        status=503
      fi
    elif [ "${AUTH_NOW:-0}" -lt "${AUTH_READY_AT:-0}" ]; then
      status=401
    fi
    printf '%s %s %s\n' "${AUTH_NOW:-0}" "$creds" "$status" >> "$AUTH_LOG"
    printf '%s %s\n' "${AUTH_NOW:-0}" "$max_time" >> "$AUTH_TIMEOUT_LOG"
    if [ "$status" = "000" ]; then exit 7; fi
    if [ -n "$AUTH_FAILURE_EXIT" ] && \
       { [ -z "$AUTH_FAILURE_EXIT_AT" ] || [ "$count" = "$AUTH_FAILURE_EXIT_AT" ]; }; then
      printf '%s\n%s' 'truncated body' "$status"
      exit "$AUTH_FAILURE_EXIT"
    fi""",
    )
    .replace(
        'case "$url" in\n',
        """case "$url" in
  *"/app/template/Login.vm")
    if [ "${AUTH_NOW:-0}" -lt "${LOGIN_READY_AT:-0}" ]; then exit 22; fi
    ;;\n""",
        1,
    )
    .replace(
        '-u) creds="$a";;',
        '-u) creds="$a";; --max-time) max_time="$a";;',
    )
)


def run_readiness(tmp_path: Path, **overrides: str):
    return run_configure(
        tmp_path,
        {"AUTH_LOG": str(tmp_path / "auth.txt"), "AUTH_TIMEOUT_LOG": str(tmp_path / "timeouts.txt"), **overrides},
        pacs_state=MOCK_PACS_REGISTRATION,
        curl_stub=READINESS_CURL,
        virtual_clock=True,
    )


def auth_probes(tmp_path: Path) -> list[tuple[int, str, str]]:
    return [
        (int(time), credential, status)
        for time, credential, status in (line.split() for line in (tmp_path / "auth.txt").read_text().splitlines())
    ]


@pytest.mark.parametrize("ready_at", [1, 800, 899], ids=["login-before-admin-account", "slow-db-init", "last-second"])
def test_first_boot_waits_for_the_admin_account_then_activates_and_rotates(tmp_path, ready_at):
    code, payloads, output = run_readiness(tmp_path, AUTH_READY_AT=str(ready_at), INITIALIZED_BODY="false")
    assert code == 0, output
    probes = auth_probes(tmp_path)
    assert probes[0][2] == "401"
    assert probes[-1][0] >= ready_at
    assert probes[-1][0] < 900
    assert probes[-1][2] == "200"
    assert len(probes) <= 6
    assert credentials_used(tmp_path, "POST", "/xapi/siteConfig")[0] == "admin:initial"
    assert credentials_used(tmp_path, "PUT", "/xapi/users/admin") == ["admin:initial"]
    made = requests_made(payloads)
    activation = next(
        i for i, (method, url) in enumerate(made) if method == "POST" and url.endswith("/xapi/siteConfig")
    )
    last_probe = max(i for i, (_, url) in enumerate(made) if url.endswith("/xapi/siteConfig/initialized"))
    plugin = next(i for i, (_, url) in enumerate(made) if url.endswith("/xapi/dqr/settings"))
    assert last_probe < activation < plugin


def test_identical_passwords_are_probed_once_per_round(tmp_path):
    code, _, output = run_readiness(tmp_path, XNAT_ADMIN_PASSWORD="initial", AUTH_READY_AT="1")
    assert code == 0, output
    probes = auth_probes(tmp_path)
    assert [status for _, _, status in probes] == ["401", "200"]
    assert [credential for _, credential, _ in probes] == ["admin:initial", "admin:initial"]
    assert probes[1][0] > probes[0][0]


@pytest.mark.parametrize("initialized", ["true", "false"], ids=["configured", "migrated"])
def test_an_already_rotated_password_still_converges_without_a_rejection_wait(tmp_path, initialized):
    code, payloads, output = run_readiness(tmp_path, WRONG_LOGINS="initial", INITIALIZED_BODY=initialized)
    assert code == 0, output
    assert [time for time, _, _ in auth_probes(tmp_path)] == [0, 0]
    assert credentials_used(tmp_path, "POST", "/xapi/siteConfig")[0] == "admin:rotated"
    assert not any(method == "PUT" and url.endswith("/xapi/users/admin") for method, url in requests_made(payloads))


def test_slow_initialization_can_still_select_the_rotated_password(tmp_path):
    code, payloads, output = run_readiness(tmp_path, WRONG_LOGINS="initial", AUTH_READY_AT="800")
    assert code == 0, output
    assert auth_probes(tmp_path)[-1][0] >= 800
    assert credentials_used(tmp_path, "POST", "/xapi/siteConfig")[0] == "admin:rotated"
    assert not any(method == "PUT" and url.endswith("/xapi/users/admin") for method, url in requests_made(payloads))


def test_a_reserved_candidate_is_not_replayed_while_the_other_route_is_starting(tmp_path):
    code, payloads, output = run_readiness(tmp_path, AUTH_SEQUENCE="401,500,401,503,401,500,503,200")
    assert code == 0, output
    assert sum(credential == "admin:initial" for _, credential, _ in auth_probes(tmp_path)) == 2
    assert credentials_used(tmp_path, "POST", "/xapi/siteConfig")[0] == "admin:rotated"


def test_asymmetric_failures_reserve_each_candidates_last_trial(tmp_path):
    code, payloads, output = run_readiness(tmp_path, AUTH_PRIMARY_READY_AT="130", INITIALIZED_BODY="false")
    assert code == 0, output
    initial_probes = [
        (time, status) for time, credential, status in auth_probes(tmp_path) if credential == "admin:initial"
    ]
    assert initial_probes == [(0, "401"), (60, "401"), (899, "200")]
    assert credentials_used(tmp_path, "POST", "/xapi/siteConfig")[0] == "admin:initial"
    assert credentials_used(tmp_path, "PUT", "/xapi/users/admin") == ["admin:initial"]


@pytest.mark.parametrize("same_password", [False, True], ids=["two-candidates-one-account", "identical-candidates"])
def test_wrong_credentials_stop_before_mutation_and_well_before_account_lockout(tmp_path, same_password):
    overrides = {"WRONG_LOGINS": "initial,rotated"}
    if same_password:
        overrides["XNAT_ADMIN_PASSWORD"] = "initial"  # pragma: allowlist secret (synthetic test password)
    code, payloads, output = run_readiness(tmp_path, **overrides)
    assert code == 1, output
    probes = auth_probes(tmp_path)
    assert len(probes) == (3 if same_password else 6)
    assert probes[-1][0] < 900
    assert probes[-1][0] > 60
    assert "neither admin password authenticates" in output
    assert "avoid locking the admin account" in output
    assert not any(method in {"POST", "PUT", "DELETE"} for method, _ in requests_made(payloads))
    assert "admin:initial" not in output
    assert "admin:rotated" not in output


def test_transient_transport_and_server_failures_are_retried_before_configuration(tmp_path):
    code, payloads, output = run_readiness(tmp_path, AUTH_SEQUENCE="000,503,404,500,200")
    assert code == 0, output
    assert [status for _, _, status in auth_probes(tmp_path)] == ["000", "503", "404", "500", "200"]
    assert credentials_used(tmp_path, "POST", "/xapi/siteConfig")[0] == "admin:initial"
    assert "Configuring XNAT instance" in output


def test_non_auth_failures_do_not_reset_the_account_rejection_budget(tmp_path):
    code, payloads, output = run_readiness(tmp_path, AUTH_SEQUENCE="401,403,500,404,000,503,401,403,401,403")
    assert code == 1, output
    probes = auth_probes(tmp_path)
    assert sum(status in {"401", "403"} for _, _, status in probes) == 6
    assert len(probes) == 10
    assert "6 rejected logins" in output
    assert not any(method in {"POST", "PUT", "DELETE"} for method, _ in requests_made(payloads))


def test_http_refusals_still_spend_the_budget_when_curl_cannot_finish_the_body(tmp_path):
    code, payloads, output = run_readiness(tmp_path, AUTH_SEQUENCE="401,403,401,403,401,403", AUTH_FAILURE_EXIT="28")
    assert code == 1, output
    assert len(auth_probes(tmp_path)) == 6
    assert "6 rejected logins" in output
    assert not any(method in {"POST", "PUT", "DELETE"} for method, _ in requests_made(payloads))


def test_a_200_with_a_truncated_body_cannot_select_a_credential(tmp_path):
    code, payloads, output = run_readiness(
        tmp_path, AUTH_SEQUENCE="200,500,200", AUTH_FAILURE_EXIT="28", AUTH_FAILURE_EXIT_AT="1"
    )
    assert code == 0, output
    assert [status for _, _, status in auth_probes(tmp_path)] == ["200", "500", "200"]
    assert credentials_used(tmp_path, "POST", "/xapi/siteConfig")[0] == "admin:initial"
    assert credentials_used(tmp_path, "PUT", "/xapi/users/admin") == ["admin:initial"]


def test_login_and_auth_share_one_deadline(tmp_path):
    code, payloads, output = run_readiness(tmp_path, LOGIN_READY_AT="850", AUTH_SEQUENCE="503", AUTH_LAST_STATUS="503")
    assert code == 1, output
    probes = auth_probes(tmp_path)
    assert probes[0][0] == 850
    assert probes[-1][0] < 900
    assert "within 900s" in output
    assert not any(method in {"POST", "PUT", "DELETE"} for method, _ in requests_made(payloads))


def test_probe_timeouts_and_sleep_fit_the_remaining_shared_budget(tmp_path):
    code, payloads, output = run_readiness(tmp_path, LOGIN_READY_AT="896", AUTH_SEQUENCE="503", AUTH_LAST_STATUS="503")
    assert code == 1, output
    assert "within 900s" in output
    timeouts = [tuple(map(int, line.split())) for line in (tmp_path / "timeouts.txt").read_text().splitlines()]
    assert timeouts
    assert all(0 < max_time <= 15 and time + max_time <= 900 for time, max_time in timeouts)
    assert not any(method in {"POST", "PUT", "DELETE"} for method, _ in requests_made(payloads))
