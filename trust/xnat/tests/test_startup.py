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


@pytest.mark.parametrize(
    ("arch", "expected"),
    [
        pytest.param("x86_64", "always", id="amd64-keeps-digest-pinning"),
        pytest.param("aarch64", "never", id="known-non-amd64-opts-out"),
        # `docker info` returns empty whenever the daemon cannot be reached — not yet up, caller
        # not in the `docker` group, an unreachable rootless/remote DOCKER_HOST, a context needing
        # auth. Testing for a known-amd64 value would hand all of those `never` on an amd64 Linux
        # host, dropping digest pinning as a side effect of detection failing quietly.
        pytest.param("", "always", id="undetected-arch-fails-safe"),
    ],
)
def test_resolve_image_opts_out_only_for_a_known_non_amd64_arch(arch: str, expected: str) -> None:
    """Losing digest pinning must be an explicit decision, not a silent detection failure."""
    resolved = subprocess.run(
        [
            "make",
            "-f",
            "Makefile",
            "-f",
            "-",
            f"XNAT_NODE_ARCH={arch}",
            "KIT=Trust_1",
            "__probe_resolve",
        ],
        cwd=XNAT_DIR,
        input="__probe_resolve: ; @echo $(XNAT_RESOLVE_IMAGE)\n",
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert resolved.returncode == 0, resolved.stdout + resolved.stderr
    assert resolved.stdout.strip() == expected, (
        f"arch {arch!r} resolved --resolve-image to {resolved.stdout.strip()!r}, expected {expected!r}"
    )


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
        wrapper'd  + export  -> probe-bucket       wrapper'd,  no export  -> NOT-EXPORTED

    The seed and the probe target are delivered as a wrapper makefile on stdin (``-f -``)
    rather than through ``--eval``, which GNU Make only grew in 3.82: macOS ships 3.81, where
    ``--eval`` is rejected as an unrecognized option and the probe prints nothing — the test
    then failed on every Mac while passing in CI. An ``-f``'d assignment is no more auto-exported
    than an ``--eval``'d one, so the value can still only reach the sub-make environment through
    the ``export`` directive under test.

    **The ``-f`` order is load-bearing.** ``trust/Makefile`` derives
    ``MAKEFILE_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))`` and resolves
    ``FL_PROVISIONED_DIR`` against it. Make materialises a stdin makefile as a temp file, so
    passing the wrapper first (``-f -`` alone, with the real makefile pulled in by an ``include``)
    puts ``/tmp/GmXXXXXX`` at the head of ``MAKEFILE_LIST``; ``MAKEFILE_DIR`` becomes ``/tmp/`` and
    ``FL_PROVISIONED_DIR`` resolves against ``/`` — measured as
    ``/fl-services/nvflare/provision/workspace-dev``. ``--eval`` left ``MAKEFILE_LIST`` untouched,
    so this is the one axis on which the two are *not* equivalent, and the harness was parsing
    ``trust/Makefile`` in a state no real invocation produces. Passing the real makefile first and
    the wrapper second keeps ``$(firstword …)`` as ``Makefile`` and ``MAKEFILE_DIR`` correct. The
    wrapper must then NOT ``include Makefile`` itself, or make reads it twice and emits an
    "overriding recipe for target" warning per duplicated rule (29 of them, measured).

    Reading the wrapper second also flips which assignment wins: its seed is parsed after the
    ``-include``d env file, so ``probe-bucket`` now wins on a configured checkout too. The
    assertion stays on *presence* rather than the value, so it proves the same thing either way.
    """
    probe_makefile = (
        # Seeds a value for the CI case, where no env file supplies one. Read after the real
        # makefile, so this seed wins; presence is what is asserted, so either value proves it.
        "FLIP_ARTIFACTS_BUCKET_NAME = probe-bucket\n"
        "__probe: ; @printenv FLIP_ARTIFACTS_BUCKET_NAME || echo NOT-EXPORTED\n"
    )
    result = subprocess.run(
        [
            "make",
            "-C",
            "trust",
            # Real makefile first so MAKEFILE_DIR points at trust/; wrapper second so its seed
            # still wins. See the docstring — the order is not cosmetic.
            "-f",
            "Makefile",
            "-f",
            "-",
            # deploy/fl_backend.mk hard-fails on an unset backend, and a CI checkout has no
            # .env.development to supply one.
            "FL_BACKEND=nvflare",
            "__probe",
        ],
        cwd=REPO_ROOT,
        input=probe_makefile,
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


def _aws_export_probe(**caller_env: str) -> str:
    """Runs a probe target under ``trust/Makefile`` and reports the child's AWS environment.

    Args:
        **caller_env: Variables to set in make's own environment, as an operator's shell would.
            Both AWS names are stripped first so the host's real values cannot mask the result.

    Returns:
        str: The probe target's stdout.
    """
    probe_makefile = "__probe: ; @env | grep -E '^AWS_(PROFILE|REGION)=' || echo NONE-EXPORTED\n"
    env = {k: v for k, v in os.environ.items() if k not in ("AWS_PROFILE", "AWS_REGION")}
    env.update(caller_env)
    result = subprocess.run(
        ["make", "-C", "trust", "-f", "Makefile", "-f", "-", "FL_BACKEND=nvflare", "__probe"],
        cwd=REPO_ROOT,
        input=probe_makefile,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_undefined_aws_names_are_not_exported_as_empty() -> None:
    """A bare ``export`` on an undefined name defines it empty and exports ``AWS_PROFILE=``.

    ``-include ../$(MAIN_ENV_FILE)`` silently skips a missing file, and a developer may comment
    either key out to fall through to the default profile or to ambient SSO credentials — so both
    are routinely undefined here. Empty is worse than absent for the AWS CLI, and the two fail
    differently: ``AWS_PROFILE=`` gives "The config profile () could not be found" instead of
    falling back to the default credential chain, and ``AWS_REGION=`` shadows the region the
    profile defines in ``~/.aws/config``, giving "Invalid endpoint: https://s3..amazonaws.com".
    Both land on the ``make -C trust up-trust`` path this export exists to repair.
    """
    assert "NONE-EXPORTED" in _aws_export_probe(), (
        "an undefined AWS_PROFILE/AWS_REGION reached the sub-make environment as an empty value — "
        "the ifdef guards in trust/Makefile are missing, and a bare `export` DEFINES an undefined "
        "name as empty (origin=file) rather than passing a value through"
    )


def test_defined_aws_names_still_reach_the_sub_make() -> None:
    """The ifdef guards must not cost the pass-through the export exists for.

    Without these in the child environment the artifacts bucket NAME reaches the XNAT sub-make but
    the credentials to read it do not, and the dev plugin sync dies on "Unable to locate
    credentials".
    """
    out = _aws_export_probe(AWS_PROFILE="probe-profile", AWS_REGION="eu-west-2")
    assert "AWS_PROFILE=probe-profile" in out, f"AWS_PROFILE did not reach the sub-make\n{out}"
    assert "AWS_REGION=eu-west-2" in out, f"AWS_REGION did not reach the sub-make\n{out}"


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
    The shared .mk fragments trust/Makefile includes are copied rather than restated so the fixture
    cannot drift from the real ones; make resolves those includes against the CWD, so one missing
    here aborts the parse and reads as a loop that never ran.
    """
    (tmp_path / "deploy").mkdir()
    for fragment in ("fl_backend.mk", "instance.mk"):
        shutil.copy(REPO_ROOT / "deploy" / fragment, tmp_path / "deploy" / fragment)
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


# The fixture kit `_kit_tree` stages for slot 1 is .env.AAA.development, so a colliding stack is
# any OTHER kit file name and a legitimate restart is the same name from a different checkout.
OCCUPYING_KIT_FILE = "/elsewhere/checkout/trust/.env.ZZZ.development"
SAME_KIT_OTHER_CHECKOUT = "/elsewhere/checkout/trust/.env.AAA.development"
OCCUPYING_WORKING_DIR = "/elsewhere/checkout/trust"
# Guarded targets that drive `-p $(TRUST_PROJECT)` with a kit. up-trust is excluded only because
# its OMOP-data-dir guard runs first and would decide the exit status; it was never the gap.
SLOT_GUARDED_TARGETS = [
    "down-trust",
    "up-trust-ec2",
    "down-trust-ec2",
    "up-fl-clients-kit",
    "down-fl-clients-kit",
]


def _make_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a nested `make`, with the outer make's own variables stripped.

    This suite is itself normally run from `make unit_test`, and an inherited MAKELEVEL makes the
    nested make announce "Entering directory …" on stdout — which breaks an exact-output assertion
    only when the suite runs through make, i.e. exactly how CI runs it and not how a bare
    `pytest` invocation does.
    """
    env = dict(os.environ if base is None else base)
    for name in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS", "MAKE_TERMOUT", "MAKE_TERMERR"):
        env.pop(name, None)
    return env


def _docker_stub(
    tmp_path: Path,
    *,
    container_ids: str = "",
    environment_file: str = "",
    working_dir: str = "",
    networks: str = "",
    require_ps_all: bool = False,
    owning_project: str = "",
) -> dict[str, str]:
    """Put a fake `docker` first on PATH answering exactly the probes the makefile guards run.

    The guards shell out for three facts: which containers a compose project owns (`ps … -q`), one
    compose label off them (`ps … --format '{{.Label …}}'`), and a container's attached networks
    (`inspect … --format`). Every other docker call the recipes make — `docker compose …`, `docker
    network …` — has to succeed silently, so a non-zero exit can only have come from a guard and
    never from the absence of a daemon.

    `require_ps_all` makes the stub answer `ps` only when `-a` was passed, standing in for a
    stopped-but-not-removed stack: a guard that probed running containers alone would see an empty
    project and wave the collision through.

    `owning_project` makes the stub answer `ps … -q` only when the compose-project filter names
    that project, so a guard querying the wrong one sees an empty project instead of being handed
    these containers regardless. Without it the stub cannot distinguish `trust1` from `b-trust1`.
    """
    bin_dir = tmp_path / "docker-stub-bin"
    bin_dir.mkdir(exist_ok=True)
    emit_ids = f"printf '%s\\n' '{container_ids}'" if container_ids else ":"
    if owning_project:
        emit_ids = (
            f'case "$*" in *"com.docker.compose.project={owning_project} "*|'
            f'*"com.docker.compose.project={owning_project}") {emit_ids} ;; esac'
        )
    all_gate = (
        '  seen_all=0; for arg in "$@"; do if [ "$arg" = "-a" ]; then seen_all=1; fi; done\n'
        '  [ "$seen_all" = 1 ] || exit 0\n'
        if require_ps_all
        else ""
    )
    _write_executable(
        bin_dir / "docker",
        "#!/bin/sh\n"
        'if [ "$1" = "ps" ]; then\n'
        f"{all_gate}"
        '  for arg in "$@"; do\n'
        f'    if [ "$arg" = "-q" ]; then {emit_ids}; exit 0; fi\n'
        "  done\n"
        '  case "$*" in\n'
        f"    *environment_file*) printf '%s\\n' '{environment_file}' ;;\n"
        f"    *working_dir*) printf '%s\\n' '{working_dir}' ;;\n"
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "inspect" ]; then\n'
        f"  printf '%s\\n' '{networks}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )
    return _make_env({**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FLIP_INSTANCE": ""})


def _run_kit_target(
    makefile: Path, target: str, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run one KIT-selected target against the fixture tree, with docker stubbed out."""
    return subprocess.run(
        ["make", "-f", str(makefile), target, "KIT=AAA", "FL_BACKEND=nvflare"],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


@pytest.mark.parametrize("target", SLOT_GUARDED_TARGETS)
def test_slot_guard_refuses_every_kit_target_when_another_kit_holds_the_project(
    tmp_path: Path, target: str
) -> None:
    """A slot held by a different kit must stop every target that drives that compose project.

    Guarding only `up-trust` left `restart-trust` (which reaches `down-trust` first) able to run
    `down --remove-orphans` against another instance's live trust before any check ran.
    """
    trust_dir = _kit_tree(tmp_path)
    env = _docker_stub(
        tmp_path,
        container_ids="c0ffeec0ffee",
        environment_file=OCCUPYING_KIT_FILE,
        working_dir=OCCUPYING_WORKING_DIR,
    )

    result = _run_kit_target(REPO_ROOT / "trust" / "Makefile", target, trust_dir, env)

    combined = result.stdout + result.stderr
    assert result.returncode != 0, f"{target} proceeded onto a slot held by another kit\n{combined}"
    assert "FL kit slot 1 is already in use" in combined, combined
    assert ".env.ZZZ.development" in combined, combined


def test_slot_guard_allows_the_same_kit_from_another_checkout(tmp_path: Path) -> None:
    """Positive control: the refusal must key on the kit, not on merely finding containers.

    Restarting a trust — including from a second checkout of the repo — is the normal path and has
    to keep working, which is why the comparison is by kit-file basename.
    """
    trust_dir = _kit_tree(tmp_path)
    env = _docker_stub(
        tmp_path, container_ids="c0ffeec0ffee", environment_file=SAME_KIT_OTHER_CHECKOUT
    )

    result = _run_kit_target(REPO_ROOT / "trust" / "Makefile", "down-fl-clients-kit", trust_dir, env)

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "already in use" not in combined, combined


def test_slot_guard_passes_when_the_project_is_empty(tmp_path: Path) -> None:
    """Positive control: an unoccupied slot must not be mistaken for an unidentifiable one."""
    trust_dir = _kit_tree(tmp_path)
    env = _docker_stub(tmp_path)

    result = _run_kit_target(REPO_ROOT / "trust" / "Makefile", "down-fl-clients-kit", trust_dir, env)

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "❌" not in combined, combined


def test_slot_guard_refuses_a_project_whose_owning_kit_cannot_be_identified(tmp_path: Path) -> None:
    """Containers with no environment_file label must refuse, not silently wave the collision on.

    That label is what names the owning kit; treating its absence as "slot free" is how the whole
    protection would disappear on a compose upgrade without anything going red.
    """
    trust_dir = _kit_tree(tmp_path)
    env = _docker_stub(
        tmp_path,
        container_ids="c0ffeec0ffee",
        environment_file="",
        working_dir=OCCUPYING_WORKING_DIR,
    )

    result = _run_kit_target(REPO_ROOT / "trust" / "Makefile", "down-fl-clients-kit", trust_dir, env)

    combined = result.stdout + result.stderr
    assert result.returncode != 0, f"an unidentifiable project was treated as free\n{combined}"
    assert "environment_file" in combined, combined
    assert OCCUPYING_WORKING_DIR in combined, combined


def test_slot_guard_sees_a_stopped_colliding_stack(tmp_path: Path) -> None:
    """The occupancy probe must be `docker ps -a`: a stopped stack still owns the project name.

    `up` would recreate those very containers under the wrong kit, so a running-only probe protects
    nothing in exactly the case where the other stack was left stopped rather than torn down.
    """
    trust_dir = _kit_tree(tmp_path)
    env = _docker_stub(
        tmp_path,
        container_ids="c0ffeec0ffee",
        environment_file=OCCUPYING_KIT_FILE,
        working_dir=OCCUPYING_WORKING_DIR,
        require_ps_all=True,
    )

    result = _run_kit_target(REPO_ROOT / "trust" / "Makefile", "up-fl-clients-kit", trust_dir, env)

    combined = result.stdout + result.stderr
    assert result.returncode != 0, f"a stopped colliding stack went unnoticed\n{combined}"
    assert "FL kit slot 1 is already in use" in combined, combined


def test_xnat_network_guard_refuses_a_trust_on_a_different_instance(tmp_path: Path) -> None:
    """XNAT must refuse to attach to a network the live trust core services are not on.

    Both sides derive the network from FLIP_INSTANCE, and the standalone `make -C trust/xnat` path
    can easily miss it — producing a healthy-looking XNAT that imaging-api simply cannot reach.
    """
    trust_dir = _kit_tree(tmp_path)
    env = _docker_stub(
        tmp_path,
        container_ids="c0ffeec0ffee",
        networks="deploy_trust-network-1 ",
        owning_project="b-trust1",
    )
    env["FLIP_INSTANCE"] = "b"

    result = _run_kit_target(
        REPO_ROOT / "trust" / "xnat" / "Makefile", "create-xnat-network", trust_dir / "xnat", env
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0, f"XNAT attached to a network the trust is not on\n{combined}"
    assert "XNAT would use b-deploy_trust-network-1" in combined, combined
    assert "b-trust1 is on: deploy_trust-network-1" in combined, combined


def test_xnat_network_guard_ignores_another_instances_trust(tmp_path: Path) -> None:
    """The guard must read *our* instance's trust, not whoever owns the unprefixed project.

    FL kit slots are handed out per hub, so both instances run a `trust1`. Filtering on the
    unprefixed name reads the other hub's trust: it refuses a correctly-placed deploy whenever
    the default instance holds that number, and passes vacuously when nothing holds it.
    """
    trust_dir = _kit_tree(tmp_path)
    env = _docker_stub(
        tmp_path,
        container_ids="c0ffeec0ffee",
        networks="deploy_trust-network-1 ",
        owning_project="trust1",
    )
    env["FLIP_INSTANCE"] = "b"

    result = _run_kit_target(
        REPO_ROOT / "trust" / "xnat" / "Makefile", "create-xnat-network", trust_dir / "xnat", env
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"guard judged instance b against the default instance\n{combined}"


def test_xnat_network_guard_passes_when_the_trust_is_on_the_same_network(tmp_path: Path) -> None:
    """Positive control: a matching attachment must not be refused."""
    trust_dir = _kit_tree(tmp_path)
    env = _docker_stub(
        tmp_path,
        container_ids="c0ffeec0ffee",
        networks="b-deploy_trust-network-1 bridge ",
        owning_project="b-trust1",
    )
    env["FLIP_INSTANCE"] = "b"

    result = _run_kit_target(
        REPO_ROOT / "trust" / "xnat" / "Makefile", "create-xnat-network", trust_dir / "xnat", env
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "❌" not in combined, combined


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("fl-services/flower/provision/creds", "/base/repo/fl-services/flower/provision/creds"),
        ("./workspace-dev", "/base/repo/workspace-dev"),
        ("/opt/kits", "/opt/kits"),
        ("/opt/kits/../kits", "/opt/kits"),
    ],
)
def test_abs_or_relative_to_only_joins_relative_values(
    tmp_path: Path, value: str, expected: str
) -> None:
    """An already-absolute path must pass through, not be welded onto the base.

    `$(abspath)` only normalises the string it is handed, so joining unconditionally turned
    `FL_PROVISIONED_DIR=/opt/kits` into `<repo>/opt/kits` and reported a "not provisioned" error
    naming a path the caller never asked for.
    """
    probe = tmp_path / "probe.mk"
    probe.write_text(
        f"include {REPO_ROOT / 'deploy' / 'fl_backend.mk'}\n"
        "probe:\n"
        "\t@echo '$(call abs_or_relative_to,$(VALUE),$(BASE))'\n"
    )

    result = subprocess.run(
        ["make", "-f", str(probe), "probe", f"VALUE={value}", "BASE=/base/repo"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=_make_env(),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
