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

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
XNAT_DIR = REPO_ROOT / "trust" / "xnat"
ENSURE_PLUGINS = REPO_ROOT / "trust" / "xnat" / "scripts" / "ensure_plugins.sh"
WAIT_FOR_PLUGINS = REPO_ROOT / "trust" / "xnat" / "xnat" / "config" / "wait-for-xnat-plugins.sh"
PLUGIN_PREFIX = "xnat-1.10.0/plugins"
REQUIRED_PLUGIN_NAMES = (
    "batch-launch-test.jar",
    "container-service-test.jar",
    "dicom-query-retrieve-test.jar",
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _write_jar(path: Path) -> Path:
    """Write a minimal but genuinely valid zip, standing in for a plugin jar.

    The cache check validates archive structure, so a zero-byte file no longer counts as a present
    plugin — which is the whole point of the guard these fixtures exercise.
    """
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
    return path


def _aws_stub_writing_jars(template: Path, names: tuple[str, ...]) -> str:
    """Shell body for a fake `aws` that populates the sync destination with valid jars."""
    copies = "; ".join(f'cp "{template}" "$dest/{name}"' for name in names)
    return f'dest="$4"; mkdir -p "$dest"; {copies}'


def _plugin_env(tmp_path: Path, aws_body: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "aws", f"#!/bin/sh\nset -eu\n{aws_body}\n")
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _run_plugin_check(plugin_dir: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ENSURE_PLUGINS), str(plugin_dir), "test-artifacts", PLUGIN_PREFIX],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def test_plugin_check_skips_aws_for_matching_complete_cache(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    for name in REQUIRED_PLUGIN_NAMES:
        _write_jar(plugin_dir / name)
    (plugin_dir / ".s3-prefix").write_text(f"{PLUGIN_PREFIX}\n")
    env = _plugin_env(tmp_path, 'echo "AWS must not be called" >&2; exit 99')

    result = _run_plugin_check(plugin_dir, env)

    assert result.returncode == 0, result.stderr
    assert "Skipping S3 sync" in result.stdout


def test_plugin_check_downloads_and_validates_fresh_cache(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    template = _write_jar(tmp_path / "template.jar")
    env = _plugin_env(tmp_path, _aws_stub_writing_jars(template, REQUIRED_PLUGIN_NAMES))

    result = _run_plugin_check(plugin_dir, env)

    assert result.returncode == 0, result.stderr
    assert (plugin_dir / ".s3-prefix").read_text().strip() == PLUGIN_PREFIX


def test_plugin_check_propagates_sync_failure(tmp_path: Path) -> None:
    result = _run_plugin_check(tmp_path / "plugins", _plugin_env(tmp_path, "exit 42"))

    assert result.returncode == 42


def test_plugin_check_rejects_incomplete_download(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    template = _write_jar(tmp_path / "template.jar")
    env = _plugin_env(
        tmp_path,
        _aws_stub_writing_jars(template, ("batch-launch-test.jar", "container-service-test.jar")),
    )

    result = _run_plugin_check(plugin_dir, env)

    assert result.returncode != 0
    assert "dicom-query-retrieve-" in result.stdout


def test_plugin_check_resyncs_a_cache_holding_a_truncated_jar(tmp_path: Path) -> None:
    """A zero-byte jar satisfies a filename check but boots an XNAT with no plugin routes.

    Dev bind-mounts this directory into the running container, so accepting it would surface much
    later as a readiness timeout blamed on the DQR plugin.
    """
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    for name in REQUIRED_PLUGIN_NAMES:
        _write_jar(plugin_dir / name)
    (plugin_dir / REQUIRED_PLUGIN_NAMES[1]).write_bytes(b"")
    (plugin_dir / ".s3-prefix").write_text(f"{PLUGIN_PREFIX}\n")
    template = _write_jar(tmp_path / "template.jar")
    env = _plugin_env(tmp_path, _aws_stub_writing_jars(template, REQUIRED_PLUGIN_NAMES))

    result = _run_plugin_check(plugin_dir, env)

    assert result.returncode == 0, result.stderr
    assert "Skipping S3 sync" not in result.stdout, "a corrupt cached jar was accepted as present"


def test_plugin_check_rejects_a_corrupt_download(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    corrupt = 'dest="$4"; mkdir -p "$dest"; ' + "; ".join(
        f': > "$dest/{name}"' for name in REQUIRED_PLUGIN_NAMES
    )

    result = _run_plugin_check(plugin_dir, _plugin_env(tmp_path, corrupt))

    assert result.returncode != 0


def _readiness_env(
    tmp_path: Path,
    curl_body: str,
    timeout: str = "5",
    initial_password: str = "test-password",
    rotated_password: str = "test-password",
) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "curl", f"#!/bin/sh\nset -eu\n{curl_body}\n")
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "XNAT_ADMIN_USER": "admin",
        "XNAT_ADMIN_INITIAL_PASSWORD": initial_password,
        "XNAT_ADMIN_PASSWORD": rotated_password,
        "XNAT_PLUGIN_READINESS_TIMEOUT_SECONDS": timeout,
        "XNAT_PLUGIN_READINESS_POLL_SECONDS": "0",
        # The production backoff deliberately costs wall-clock time; zero it so the tests that
        # exercise rejected logins stay instant.
        "XNAT_PLUGIN_READINESS_AUTH_BACKOFF_SECONDS": "0",
    }


def _run_readiness(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the readiness helper under a hard timeout.

    The timeout is load-bearing: a regression in the script's own wall-clock guard makes the loop
    unbounded, and without it pytest would hang until the CI job limit and report a runner timeout
    instead of a red test.
    """
    return subprocess.run(
        ["bash", str(WAIT_FOR_PLUGINS)],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=30,
    )


def test_readiness_waits_for_authenticated_plugin_route(tmp_path: Path) -> None:
    count_file = tmp_path / "curl-count"
    curl_body = (
        f'count=$(cat "{count_file}" 2>/dev/null || echo 0); count=$((count + 1)); '
        f'printf "%s" "$count" > "{count_file}"; '
        'if [ "$count" -ge 2 ]; then printf "204"; else printf "404"; fi'
    )

    result = _run_readiness(_readiness_env(tmp_path, curl_body))

    assert result.returncode == 0, result.stderr
    assert "ready (HTTP 204)" in result.stdout


def test_readiness_fails_with_endpoint_and_status_on_timeout(tmp_path: Path) -> None:
    result = _run_readiness(_readiness_env(tmp_path, 'printf "404"', timeout="0"))

    assert result.returncode != 0
    assert "/xapi/dqr/settings" in result.stderr
    assert "last HTTP status: 404" in result.stderr
    assert "test-password" not in result.stdout + result.stderr


def test_readiness_probe_authenticates_against_the_plugin_route(tmp_path: Path) -> None:
    """The gate is only meaningful authenticated — unauthenticated XNAT 401s whatever the plugin state."""
    argv_log = tmp_path / "curl-argv"
    result = _run_readiness(
        _readiness_env(tmp_path, f'printf "%s\\n" "$*" >> "{argv_log}"; printf "200"')
    )

    assert result.returncode == 0, result.stderr
    recorded = argv_log.read_text()
    assert "-u admin:test-password" in recorded
    assert "/xapi/dqr/settings" in recorded


def test_readiness_keeps_polling_while_connection_is_refused(tmp_path: Path) -> None:
    """`000` (curl could not connect) is the normal state for the first seconds of a boot."""
    count_file = tmp_path / "curl-count"
    curl_body = (
        f'count=$(cat "{count_file}" 2>/dev/null || echo 0); count=$((count + 1)); '
        f'printf "%s" "$count" > "{count_file}"; '
        'if [ "$count" -ge 3 ]; then printf "200"; else exit 7; fi'
    )

    result = _run_readiness(_readiness_env(tmp_path, curl_body))

    assert result.returncode == 0, result.stderr
    assert "HTTP 000" in result.stdout


def test_readiness_rejects_a_leading_zero_timeout(tmp_path: Path) -> None:
    """`09` is an invalid octal literal to bash arithmetic, which would silently disable the timeout."""
    result = _run_readiness(_readiness_env(tmp_path, 'printf "404"', timeout="09"))

    assert result.returncode != 0
    assert "timeout='09'" in result.stderr, "the rejection should name the offending value"


def test_readiness_reaches_the_rotated_password_on_the_first_rejection(tmp_path: Path) -> None:
    """Re-running configuration against an already-rotated XNAT must succeed, and succeed cheaply.

    Waiting out a full run of rejections first would spend AUTH_FAILURE_LIMIT wrong-password logins
    and the whole backoff tolerance (~45s at the shipped defaults) on every retry — on the path this
    script exists to keep idempotent.
    """
    argv_log = tmp_path / "curl-argv"
    curl_body = (
        f'printf "%s\\n" "$*" >> "{argv_log}"; '
        'case "$*" in *admin:rotated*) printf "200" ;; *) printf "401" ;; esac'
    )

    result = _run_readiness(
        _readiness_env(tmp_path, curl_body, initial_password="initial", rotated_password="rotated")
    )

    assert result.returncode == 0, result.stderr
    probes = argv_log.read_text().strip().splitlines()
    assert sum("admin:initial" in line for line in probes) == 1, "one rejection is enough evidence"
    assert sum("admin:rotated" in line for line in probes) == 1


def test_readiness_forgives_a_transient_rejection_during_boot(tmp_path: Path) -> None:
    """A lone 401 amid 404s must not permanently switch away from a working credential.

    XNAT can reject a valid credential for a beat while its auth providers wire up. Switching on
    that blip would strand the run on the wrong password for the rest of the wait.
    """
    argv_log = tmp_path / "curl-argv"
    count_file = tmp_path / "curl-count"
    curl_body = (
        f'printf "%s\\n" "$*" >> "{argv_log}"; '
        f'count=$(cat "{count_file}" 2>/dev/null || echo 0); count=$((count + 1)); '
        f'printf "%s" "$count" > "{count_file}"; '
        'case "$count" in 1) printf "404" ;; 2) printf "401" ;; 3) printf "404" ;; *) printf "200" ;; esac'
    )

    result = _run_readiness(
        _readiness_env(tmp_path, curl_body, initial_password="initial", rotated_password="rotated")
    )

    assert result.returncode == 0, result.stderr
    probes = argv_log.read_text().strip().splitlines()
    # The blip does spend one look at the other password — but the 404 it gets back is inconclusive
    # (the route is not registered yet for either credential), so the run continues, and finishes,
    # on the credential it started with.
    assert sum("admin:rotated" in line for line in probes) == 1, "a single 401 must not switch"
    assert "admin:initial" in probes[-1], "the successful probe must be the original credential"


def test_readiness_does_not_replay_a_dead_credential_while_plugins_load(tmp_path: Path) -> None:
    """A 404 means the plugin is still registering, so the *other* password must not be tried.

    Probing both credentials on every poll sends one wrong-password login every few seconds, which
    trips XNAT's 20-attempt account lockout long before the wait budget expires.
    """
    argv_log = tmp_path / "curl-argv"
    result = _run_readiness(
        _readiness_env(
            tmp_path,
            f'printf "%s\\n" "$*" >> "{argv_log}"; printf "404"',
            timeout="0",
            initial_password="initial",
            rotated_password="rotated",
        )
    )

    assert result.returncode != 0
    assert "admin:rotated" not in argv_log.read_text()


def test_readiness_stops_well_before_lockout_when_both_credentials_are_rejected(tmp_path: Path) -> None:
    """Waiting cannot fix a wrong password, so fail fast instead of burning the whole budget."""
    argv_log = tmp_path / "curl-argv"
    result = _run_readiness(
        _readiness_env(
            tmp_path,
            f'printf "%s\\n" "$*" >> "{argv_log}"; printf "401"',
            timeout="900",
            initial_password="initial",
            rotated_password="rotated",
        )
    )

    assert result.returncode != 0
    assert "rejected every admin credential" in result.stderr
    probes = argv_log.read_text().strip().splitlines()
    assert len(probes) <= 6, f"{len(probes)} rejected logins risks XNAT's 20-attempt lockout"
    # Pinned per credential, not just in total: the early look at the other password on the first
    # rejection is charged to that password's own allowance, so it buys the fast path to a rotated
    # XNAT without widening the budget this bound exists to keep.
    for password in ("initial", "rotated"):
        spent = sum(f"admin:{password}" in line for line in probes)
        assert spent <= 3, f"{spent} rejected logins spent on the {password} password"


def _dry_run_up_xnat(**overrides: str) -> subprocess.CompletedProcess[str]:
    """Dry-run ``up-xnat`` from trust/xnat/ and return the recipe make would have executed."""
    return subprocess.run(
        ["make", "-n", "up-xnat", "KIT=Trust_1", *(f"{k}={v}" for k, v in overrides.items())],
        cwd=XNAT_DIR,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_dev_up_xnat_validates_the_plugin_cache_before_tearing_xnat_down() -> None:
    """A failed download must not leave the trust with a torn-down XNAT and no replacement."""
    stdout = _dry_run_up_xnat().stdout

    assert "ensure_plugins.sh" in stdout
    assert stdout.index("ensure_plugins.sh") < stdout.index("xnat-reset")


def test_up_xnat_skips_the_host_plugin_cache_outside_development() -> None:
    """Only the development stack bind-mounts plugins; elsewhere they are baked into the image.

    Running the download unconditionally broke the documented on-prem bring-up, which has neither
    FLIP_ARTIFACTS_BUCKET_NAME nor any need for the cache.
    """
    for prod in ("true", "stag"):
        stdout = _dry_run_up_xnat(PROD=prod).stdout
        assert "ensure_plugins.sh" not in stdout, f"PROD={prod} still reaches for the dev cache"


@pytest.mark.parametrize(
    "bucket",
    [
        pytest.param("", id="unset"),
        # .env.development.example ships this value, so a fresh clone reaches the download with it
        # still in place. It is non-empty, so an emptiness check passes it through to `aws s3 sync`,
        # which fails on bucket-name validation instead of naming the variable to set.
        pytest.param("<your-xnat-artifacts-bucket-name>", id="placeholder"),
    ],
)
def test_plugin_download_names_the_variable_it_needs(bucket: str) -> None:
    result = subprocess.run(
        ["make", "xnat-plugins-download", f"FLIP_ARTIFACTS_BUCKET_NAME={bucket}"],
        cwd=XNAT_DIR,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "FLIP_ARTIFACTS_BUCKET_NAME" in result.stdout
    assert ".env.development" in result.stdout, "should say where to set it"
    assert "Usage:" not in result.stdout, "leaked the script's usage line instead of naming the var"
    assert "aws" not in result.stderr.lower(), "reached the S3 sync instead of failing at the guard"


def test_trust_makefile_exports_the_artifacts_bucket_to_the_xnat_sub_make() -> None:
    """`make -C trust up-trust` reaches up-xnat, which in development needs this in its env.

    The bucket name is seeded as a *makefile* variable rather than on the command line, because
    make auto-exports command-line variables — that route would pass whether or not the export
    directive exists, so it would prove nothing. ``trust/Makefile`` gets its real value from an
    ``-include``d env file, which is likewise not auto-exported.

    The assertion is on *presence*, not on the seeded value. Asserting the value fails on any
    checkout with a populated ``.env.development``, because the real bucket name from that file
    wins over the seed — so the test used to pass in CI (clean checkout, no env file) and fail on
    a configured developer machine, which is exactly backwards (FLIP#970).

    Presence alone is still a real assertion. Verified against GNU Make: neither an ``-include``d
    nor an ``--eval``'d variable is exported on its own, so *whichever* value arrives, it can only
    have got there through the ``export`` directive under test::

        -include'd + export  -> from-env-file      -include'd, no export  -> NOT-EXPORTED
        --eval'd   + export  -> probe-bucket       --eval'd,   no export  -> NOT-EXPORTED
    """
    result = subprocess.run(
        [
            "make",
            "-C",
            "trust",
            # deploy/fl_backend.mk hard-fails on an unset backend, and a CI checkout has no
            # .env.development to supply one.
            "FL_BACKEND=nvflare",
            # Seeds a value for the CI case, where no env file supplies one. On a configured
            # checkout the env file's real value arrives instead — either proves the export.
            "--eval=FLIP_ARTIFACTS_BUCKET_NAME=probe-bucket",
            "--eval=__probe: ; @printenv FLIP_ARTIFACTS_BUCKET_NAME || echo NOT-EXPORTED",
            "__probe",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    combined = result.stdout + result.stderr
    assert "NOT-EXPORTED" not in result.stdout, (
        "FLIP_ARTIFACTS_BUCKET_NAME did not reach the sub-make environment — the "
        "`export FLIP_ARTIFACTS_BUCKET_NAME` directive in trust/Makefile is missing. Without it "
        f"the dev XNAT plugin download runs against a bucket-less S3 URI.\n{combined}"
    )
    # Guards the guard: a make failure that printed neither the value nor NOT-EXPORTED would
    # otherwise satisfy the assertion above by saying nothing at all.
    assert result.stdout.strip(), f"the probe target produced no output at all\n{combined}"


def test_root_smoke_target_resolves_relative_paths_from_repo_root() -> None:
    result = subprocess.run(
        [
            "make",
            "-n",
            "e2e_smoke",
            "MODEL_FILES_DIR=fl-tutorials/example/app_files",
            "QUERY_FILE=fl-tutorials/example/query.sql",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert f'MODEL_FILES_DIR="{REPO_ROOT}/fl-tutorials/example/app_files"' in result.stdout
    assert f'QUERY_FILE="{REPO_ROOT}/fl-tutorials/example/query.sql"' in result.stdout


def _kit_tree(tmp_path: Path) -> Path:
    """Stage a fixture trust tree whose kit files drive the per-trust `up` loops.

    Kit files are gitignored, so a CI checkout has none and the loops would otherwise not run.
    fl_backend.mk is copied rather than restated so the fixture cannot drift from the real one.
    """
    (tmp_path / "deploy").mkdir()
    shutil.copy(REPO_ROOT / "deploy" / "fl_backend.mk", tmp_path / "deploy" / "fl_backend.mk")
    trust_dir = tmp_path / "trust"
    (trust_dir / "xnat").mkdir(parents=True)
    for slot, code in enumerate(("AAA", "BBB"), start=1):
        (trust_dir / f".env.{code}.development").write_text(f"FL_KIT_SLOT_NUMBER={slot}\n")
    return trust_dir


def _run_up_loop(
    makefile: Path, cwd: Path, tmp_path: Path, child_exit: int, skip: tuple[str, ...] = ()
) -> tuple[int, int]:
    """Drive an aggregate `up` target with every per-trust sub-make stubbed out.

    `skip` names prerequisites to neutralise with make's -o flag. trust/Makefile's `up` depends on
    create-networks, which talks to the Docker daemon — absent on a CI runner, where it would fail
    before the loop is ever reached and look exactly like a loop that never ran.

    Returns (aggregate exit status, number of sub-make invocations).
    """
    record = tmp_path / "sub-make-calls"
    stub = tmp_path / "fake-make"
    _write_executable(stub, f'#!/bin/sh\necho "$@" >> "{record}"\nexit {child_exit}\n')

    result = subprocess.run(
        [
            "make",
            "-f",
            str(makefile),
            "up",
            f"MAKE={stub}",
            "FL_BACKEND=nvflare",
            *(arg for target in skip for arg in ("-o", target)),
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    calls = len(record.read_text().splitlines()) if record.exists() else 0
    return result.returncode, calls


def test_up_loops_abort_on_the_first_failing_trust(tmp_path: Path) -> None:
    """A failing per-trust sub-make must fail the aggregate target and stop the loop.

    Asserted by execution rather than by matching the recipe text: prefixing the recipe with make's
    ignore-errors marker, or rewording the message, leaves any string match intact while the
    aggregate silently returns success.
    """
    trust_dir = _kit_tree(tmp_path)

    for makefile, cwd, skip in (
        (REPO_ROOT / "trust" / "xnat" / "Makefile", trust_dir / "xnat", ()),
        (REPO_ROOT / "trust" / "Makefile", trust_dir, ("create-networks",)),
    ):
        status, calls = _run_up_loop(makefile, cwd, tmp_path, child_exit=1, skip=skip)
        assert status != 0, f"{makefile.parent.name} up reported success despite a failing trust"
        assert calls == 1, f"{makefile.parent.name} up kept going after a failure ({calls} calls)"
        (tmp_path / "sub-make-calls").unlink()


def test_up_loops_succeed_when_every_trust_starts(tmp_path: Path) -> None:
    """Positive control: the abort assertion only means something if the loop can pass."""
    trust_dir = _kit_tree(tmp_path)

    status, calls = _run_up_loop(
        REPO_ROOT / "trust" / "xnat" / "Makefile", trust_dir / "xnat", tmp_path, child_exit=0
    )

    assert status == 0
    assert calls == 2, "both fixture kits should have been started"
