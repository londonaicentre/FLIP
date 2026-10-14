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
from pathlib import Path

import pytest

from plugin_fixtures import PLUGIN_JARS, declared_ids, fake_proc, plugins_listing, write_plugin_jar, xnat_home

REPO_ROOT = Path(__file__).resolve().parents[3]
XNAT_DIR = REPO_ROOT / "trust" / "xnat"
WAIT_FOR_PLUGINS = REPO_ROOT / "trust" / "xnat" / "xnat" / "config" / "wait-for-xnat-plugins.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


# A stub XNAT for the readiness wait. It answers by route — GET /xapi/plugins lists $PLUGINS_JSON,
# everything else (GET /xapi/dqr/settings) answers {} — and keeps a per-route request count in $n,
# so each test's BEHAVIOUR snippet can override `status` and `body` for the request in hand from
# $route, $n and $creds. The reply is printed the way the script's `-w '\n%{http_code}'` makes
# real curl print it: the body, a newline, the status.
READINESS_CURL = r"""#!/bin/sh
set -eu
url=""; creds=""; prev=""
for a in "$@"; do
  case "$prev" in -u) creds="$a" ;; esac
  case "$a" in http*) url="$a" ;; esac
  prev="$a"
done
case "$url" in
  */xapi/plugins) route=plugins ;;
  */xapi/dqr/settings) route=dqr ;;
  *) route=other ;;
esac
printf '%s %s\n' "$route" "$*" >> "$CURL_LOG"
n=$(( $(cat "$CURL_COUNTS/$route" 2>/dev/null || echo 0) + 1 ))
printf '%s' "$n" > "$CURL_COUNTS/$route"
status=200
body='{}'
if [ "$route" = plugins ]; then body="$PLUGINS_JSON"; fi
__BEHAVIOUR__
printf '%s\n%s' "$body" "$status"
"""


def _readiness_env(
    tmp_path: Path,
    behaviour: str = "",
    timeout: str = "5",
    initial_password: str = "test-password",
    rotated_password: str = "test-password",
    jars: dict[str, str | None] | None = None,
    listed: list[str] | None = None,
    tomcat: str = "beside",
) -> dict[str, str]:
    """Environment for one run of the wait against the stub XNAT.

    Args:
        tmp_path: Per-test directory for the stub, its logs and the fixture XNAT home.
        behaviour: Shell run per request, after the defaults are set, to override them.
        timeout: The wait's budget, in seconds.
        initial_password: The kit's initial admin password.
        rotated_password: The kit's configured admin password.
        jars: Fixture plugin jars in the XNAT home ({filename: declared id or None}).
        listed: The plugin ids GET /xapi/plugins lists; defaults to every id ``jars`` declares.
        tomcat: ``beside`` — a Tomcat in the wait's PID namespace loads this XNAT home, as in
            Compose; ``elsewhere`` — a Tomcat is visible but loads another home; ``none`` — no
            Tomcat at all, as in the chart's xnat-init pod.
    """
    jars = PLUGIN_JARS if jars is None else jars
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counts = tmp_path / "curl-counts"
    counts.mkdir()
    _write_executable(bin_dir / "curl", READINESS_CURL.replace("__BEHAVIOUR__", behaviour))
    home = xnat_home(tmp_path, jars)
    tomcat_home: Path | None = None
    if tomcat == "beside":
        tomcat_home = home
    elif tomcat == "elsewhere":
        # A home holding the very same jars, so only the directory's identity differs.
        tomcat_home = xnat_home(tmp_path / "other", jars)
    else:
        assert tomcat == "none", tomcat
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "XNAT_HOME": str(home),
        "XNAT_PLUGIN_READINESS_PROC_DIR": str(fake_proc(tmp_path, tomcat_home)),
        "PLUGINS_JSON": plugins_listing(declared_ids(jars) if listed is None else listed),
        "CURL_LOG": str(tmp_path / "curl-log"),
        "CURL_COUNTS": str(counts),
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


def _requests(tmp_path: Path, route: str | None = None) -> list[str]:
    """The curl argv of every request the wait made, optionally only those to one route."""
    log = tmp_path / "curl-log"
    lines = log.read_text().splitlines() if log.exists() else []
    return [line.split(" ", 1)[1] for line in lines if route is None or line.split(" ", 1)[0] == route]


# A listing that lacks two of the four fixture plugins, as an XNAT that did not load them answers.
PARTIAL_LISTING = plugins_listing(["containers", "dicom-query-retrieve"])


def test_readiness_waits_until_xnat_lists_every_plugin_the_jars_declare(tmp_path: Path) -> None:
    """A route answering is not enough: XNAT must report every installed plugin as loaded."""
    behaviour = f"""if [ "$route" = plugins ] && [ "$n" -lt 3 ]; then body='{PARTIAL_LISTING}'; fi"""

    result = _run_readiness(_readiness_env(tmp_path, behaviour))

    assert result.returncode == 0, result.stderr
    assert "missing: batchLaunchPlugin ohifViewerPlugin" in result.stdout, "the retry must name what is missing"
    assert "XNAT plugins are ready (HTTP 200)" in result.stdout
    assert len(_requests(tmp_path, "plugins")) == 3
    assert len(_requests(tmp_path, "dqr")) == 1, "the DQR route is asked only once the listing is complete"


def test_readiness_names_the_plugin_xnat_has_not_loaded(tmp_path: Path) -> None:
    """The fail-loud message must say which plugin, not just that the wait timed out."""
    loaded = [plugin_id for plugin_id in declared_ids() if plugin_id != "ohifViewerPlugin"]

    result = _run_readiness(_readiness_env(tmp_path, timeout="0", listed=loaded))

    assert result.returncode != 0
    assert "XNAT has not loaded: ohifViewerPlugin\n" in result.stderr, result.stderr
    assert "/xapi/plugins" in result.stderr
    assert not _requests(tmp_path, "dqr"), "the DQR route must not be asked while a plugin is missing"


def test_readiness_expects_a_plugin_because_a_jar_declares_it(tmp_path: Path) -> None:
    """The roster is read from the jars, so a jar this script has never heard of is waited for too."""
    jars = {**PLUGIN_JARS, "site-extra-1.0.jar": "siteExtraPlugin"}

    result = _run_readiness(_readiness_env(tmp_path, timeout="0", jars=jars, listed=declared_ids()))

    assert result.returncode != 0
    assert "XNAT has not loaded: siteExtraPlugin\n" in result.stderr, result.stderr


def test_readiness_expects_only_the_plugins_the_jars_declare(tmp_path: Path) -> None:
    """…and a plugin no jar declares is not expected, so the roster cannot be a list in the script."""
    jars: dict[str, str | None] = {"dicom-query-retrieve-3.0.0-xpl.jar": "dicom-query-retrieve"}

    result = _run_readiness(_readiness_env(tmp_path, jars=jars))

    assert result.returncode == 0, result.stderr
    assert "XNAT loaded dicom-query-retrieve and" in result.stdout


def test_a_jar_that_declares_no_plugin_is_not_waited_for(tmp_path: Path) -> None:
    """XNAT loads every jar in the directory, but only those with plugin properties are plugins."""
    jars = {**PLUGIN_JARS, "some-library-2.1.jar": None}

    result = _run_readiness(_readiness_env(tmp_path, jars=jars))

    assert result.returncode == 0, result.stderr
    assert "some-library-2.1.jar declares no XNAT plugin" in result.stdout


def test_an_empty_plugin_directory_fails_before_any_login(tmp_path: Path) -> None:
    """No jars is a broken install, not a vacuously ready one, and there is nothing to log in for."""
    result = _run_readiness(_readiness_env(tmp_path, jars={}))

    assert result.returncode != 0
    assert "no XNAT plugin jars found in" in result.stderr
    assert str(tmp_path / "xnat-home" / "plugins") in result.stderr
    # The hint covers every shape, not just the Compose dev cache: a Helm operator who lands here
    # (exec'd into xnat-web) gets the plugins from xnat.web.plugins.urls, not prepare-artifacts.
    assert "prepare-artifacts" in result.stderr
    assert "xnat.web.plugins.urls" in result.stderr
    assert not _requests(tmp_path), "the wait logged in although it had nothing to wait for"


def test_an_unreadable_jar_fails_naming_it_before_any_login(tmp_path: Path) -> None:
    """A failed download saved as a jar is one XNAT cannot load either; which plugin it was is unknowable."""
    env = _readiness_env(tmp_path)
    (Path(env["XNAT_HOME"]) / "plugins" / "error-page-1.0.jar").write_bytes(b"<html>503</html>")

    result = _run_readiness(env)

    assert result.returncode != 0
    assert "error-page-1.0.jar is not a readable jar" in result.stderr
    assert not _requests(tmp_path)


def test_a_truncated_jar_fails_naming_it_before_any_login(tmp_path: Path) -> None:
    """A real jar cut short has no central directory: unzip exits 9, and Java cannot load it either."""
    env = _readiness_env(tmp_path)
    jar = Path(env["XNAT_HOME"]) / "plugins" / "ohif-viewer-3.8.0-fat.jar"
    whole = jar.read_bytes()
    jar.write_bytes(whole[: len(whole) // 2])

    result = _run_readiness(env)

    assert result.returncode != 0
    assert "ohif-viewer-3.8.0-fat.jar is not a readable jar (unzip exit 9" in result.stderr, result.stderr
    assert not _requests(tmp_path)


def test_a_jar_unzip_only_warns_about_is_still_read(tmp_path: Path) -> None:
    """unzip exits 1 on an archive it reads despite a defect, such as bytes prepended; Java loads it too.

    Its declared id must therefore be expected like any other, not rejected as unreadable.
    """
    env = _readiness_env(tmp_path, timeout="0")
    sound = write_plugin_jar(tmp_path, "prefixed-1.0.jar", "prefixedPlugin").read_bytes()
    (Path(env["XNAT_HOME"]) / "plugins" / "prefixed-1.0.jar").write_bytes(b"#!/bin/sh\nexit 0\n" + sound)

    result = _run_readiness(env)

    assert "not a readable jar" not in result.stderr
    assert "XNAT has not loaded: prefixedPlugin\n" in result.stderr, result.stderr


# ── Away from XNAT: the chart's xnat-init job ─────────────────────────────────────────────────────


@pytest.mark.parametrize("tomcat", ["none", "elsewhere"])
def test_away_from_xnat_the_wait_takes_the_dqr_route_alone(tmp_path: Path, tomcat: str) -> None:
    """The xnat-init pod sees the image's baked jars, which the running XNAT never loads.

    The dangerous direction is a plugin the image carries but xnat.web.plugins.urls does not: a
    roster read from disk would wait out the whole budget for it on every install and upgrade. The
    same jars beside XNAT time out naming it (``test_readiness_expects_a_plugin_because_a_jar_declares_it``).
    ``elsewhere`` is a Tomcat that is visible but loads a different directory holding the same jars.
    """
    jars = {**PLUGIN_JARS, "baked-only-1.0.jar": "bakedOnlyPlugin"}

    result = _run_readiness(_readiness_env(tmp_path, jars=jars, listed=declared_ids(), tomcat=tomcat))

    assert result.returncode == 0, result.stderr
    assert "No Tomcat beside this script loads" in result.stdout, "the fallback must say why it was taken"
    assert "XNAT DQR plugin is ready (HTTP 200)." in result.stdout
    assert not _requests(tmp_path, "plugins"), "with no roster to check, there is nothing to ask XNAT for"
    assert len(_requests(tmp_path, "dqr")) == 1


def test_away_from_xnat_no_jar_is_read(tmp_path: Path) -> None:
    """An image with no baked jars, or a bad one, says nothing about the XNAT that is running."""
    env = _readiness_env(tmp_path, jars={}, tomcat="none")
    (Path(env["XNAT_HOME"]) / "plugins" / "error-page-1.0.jar").write_bytes(b"<html>503</html>")

    result = _run_readiness(env)

    assert result.returncode == 0, result.stderr
    assert "XNAT DQR plugin is ready" in result.stdout


def test_away_from_xnat_the_dqr_wait_stays_bounded_and_fail_loud(tmp_path: Path) -> None:
    result = _run_readiness(
        _readiness_env(tmp_path, 'if [ "$route" = dqr ]; then status=404; fi', timeout="0", tomcat="none")
    )

    assert result.returncode != 0
    assert "endpoint:   http://xnat-web:8080/xapi/dqr/settings" in result.stderr
    assert "last HTTP status: 404" in result.stderr


def test_away_from_xnat_the_rotated_password_is_reached_on_the_first_rejection(tmp_path: Path) -> None:
    """The credential handling is the same whichever probes run."""
    result = _run_readiness(
        _readiness_env(
            tmp_path,
            'case "$creds" in *:rotated) ;; *) status=401 ;; esac',
            initial_password="initial",  # pragma: allowlist secret
            rotated_password="rotated",  # pragma: allowlist secret
            tomcat="none",
        )
    )

    assert result.returncode == 0, result.stderr
    assert "XNAT DQR plugin is ready (HTTP 200, configured admin password)." in result.stdout
    probes = _requests(tmp_path, "dqr")
    assert sum("admin:initial" in line for line in probes) == 1
    assert sum("admin:rotated" in line for line in probes) == 1


def test_readiness_still_requires_the_dqr_route_once_every_plugin_is_listed(tmp_path: Path) -> None:
    """The listing does not prove a plugin's routes are registered, so DQR's own route still gates."""
    result = _run_readiness(_readiness_env(tmp_path, 'if [ "$route" = dqr ]; then status=404; fi', timeout="0"))

    assert result.returncode != 0
    assert "endpoint:   http://xnat-web:8080/xapi/dqr/settings" in result.stderr
    assert "last HTTP status: 404" in result.stderr
    assert "has not loaded" not in result.stderr, "every plugin was listed; only the route was missing"


def test_readiness_waits_for_the_dqr_route_after_the_listing_is_complete(tmp_path: Path) -> None:
    behaviour = 'if [ "$route" = dqr ] && [ "$n" -lt 3 ]; then status=404; fi'

    result = _run_readiness(_readiness_env(tmp_path, behaviour))

    assert result.returncode == 0, result.stderr
    assert "HTTP 404 from http://xnat-web:8080/xapi/dqr/settings" in result.stdout
    assert len(_requests(tmp_path, "dqr")) == 3


def test_a_listing_that_is_not_json_lists_nothing(tmp_path: Path) -> None:
    """A 200 the script cannot read must not pass the wait."""
    result = _run_readiness(
        _readiness_env(tmp_path, """if [ "$route" = plugins ]; then body='<html>ok</html>'; fi""", timeout="0")
    )

    assert result.returncode != 0
    assert f"XNAT has not loaded: {' '.join(declared_ids())}\n" in result.stderr, result.stderr


def test_readiness_fails_with_endpoint_and_status_on_timeout(tmp_path: Path) -> None:
    result = _run_readiness(_readiness_env(tmp_path, "status=404", timeout="0"))

    assert result.returncode != 0
    assert "/xapi/plugins" in result.stderr
    assert "last HTTP status: 404" in result.stderr
    assert "test-password" not in result.stdout + result.stderr


def test_readiness_probe_authenticates_against_both_routes(tmp_path: Path) -> None:
    """Both probes are only meaningful authenticated — unauthenticated XNAT 401s whatever the plugin state."""
    result = _run_readiness(_readiness_env(tmp_path))

    assert result.returncode == 0, result.stderr
    for route in ("plugins", "dqr"):
        (made,) = _requests(tmp_path, route)
        assert "-u admin:test-password" in made, f"the {route} probe was not authenticated"


def test_readiness_keeps_polling_while_connection_is_refused(tmp_path: Path) -> None:
    """`000` (curl could not connect) is the normal state for the first seconds of a boot.

    Real curl prints its -w status (000) and exits non-zero; the stub does the same.
    """
    behaviour = """if [ "$route" = plugins ] && [ "$n" -lt 3 ]; then printf '\\n000'; exit 7; fi"""

    result = _run_readiness(_readiness_env(tmp_path, behaviour))

    assert result.returncode == 0, result.stderr
    assert "HTTP 000" in result.stdout


def test_readiness_rejects_a_leading_zero_timeout(tmp_path: Path) -> None:
    """`09` is an invalid octal literal to bash arithmetic, which would silently disable the timeout."""
    result = _run_readiness(_readiness_env(tmp_path, "status=404", timeout="09"))

    assert result.returncode != 0
    assert "timeout='09'" in result.stderr, "the rejection should name the offending value"


def test_readiness_reaches_the_rotated_password_on_the_first_rejection(tmp_path: Path) -> None:
    """Re-running configuration against an already-rotated XNAT must succeed, and succeed cheaply.

    Waiting out a full run of rejections first would spend AUTH_FAILURE_LIMIT wrong-password logins
    and the whole backoff tolerance (~45s at the shipped defaults) on every retry — on the path this
    script exists to keep idempotent.
    """
    result = _run_readiness(
        _readiness_env(
            tmp_path,
            'case "$creds" in *:rotated) ;; *) status=401 ;; esac',
            initial_password="initial",  # pragma: allowlist secret
            rotated_password="rotated",  # pragma: allowlist secret
        )
    )

    assert result.returncode == 0, result.stderr
    probes = _requests(tmp_path, "plugins")
    assert sum("admin:initial" in line for line in probes) == 1, "one rejection is enough evidence"
    assert sum("admin:rotated" in line for line in probes) == 1


def test_an_accepted_alternate_password_is_kept_while_plugins_load(tmp_path: Path) -> None:
    """Accepted-but-not-ready proves which password XNAT holds; the rejected one is not tried again.

    Before /xapi/plugins could answer "authenticated, not ready yet" the wait had no such signal,
    and only a full run of rejections switched credentials — spending AUTH_FAILURE_LIMIT
    wrong-password logins toward XNAT's lockout while the plugins finished loading.
    """
    behaviour = (
        'case "$creds" in *:rotated) ;; *) status=401 ;; esac; '
        f"""if [ "$route" = plugins ] && [ "$n" -lt 4 ]; then body='{PARTIAL_LISTING}'; fi"""
    )

    result = _run_readiness(
        _readiness_env(
            tmp_path,
            behaviour,
            initial_password="initial",  # pragma: allowlist secret
            rotated_password="rotated",  # pragma: allowlist secret
        )
    )

    assert result.returncode == 0, result.stderr
    assert "continuing with the configured one" in result.stdout
    probes = _requests(tmp_path, "plugins")
    assert sum("admin:initial" in line for line in probes) == 1, "the rejected password was tried again"
    assert "admin:rotated" in probes[-1]


def test_readiness_forgives_a_transient_rejection_during_boot(tmp_path: Path) -> None:
    """A lone 401 amid inconclusive answers must not permanently switch away from a working credential.

    XNAT can reject a valid credential for a beat while its auth providers wire up. Switching on
    that blip would strand the run on the wrong password for the rest of the wait.
    """
    behaviour = (
        'if [ "$route" = plugins ]; then case "$n" in 1) status=404 ;; 2) status=401 ;; 3) status=503 ;; esac; fi'
    )

    result = _run_readiness(
        _readiness_env(
            tmp_path,
            behaviour,
            initial_password="initial",  # pragma: allowlist secret
            rotated_password="rotated",  # pragma: allowlist secret
        )
    )

    assert result.returncode == 0, result.stderr
    probes = _requests(tmp_path, "plugins")
    # The blip does spend one look at the other password — but the 503 it gets back is inconclusive
    # (XNAT answered neither password), so the run continues, and finishes, on the credential it
    # started with.
    assert sum("admin:rotated" in line for line in probes) == 1, "a single 401 must not switch"
    assert "admin:initial" in probes[-1], "the successful probe must be the original credential"


@pytest.mark.parametrize(
    "behaviour",
    [
        pytest.param("status=404", id="route-not-answering"),
        pytest.param(f"""if [ "$route" = plugins ]; then body='{PARTIAL_LISTING}'; fi""", id="plugins-missing"),
    ],
)
def test_readiness_does_not_replay_a_dead_credential_while_plugins_load(tmp_path: Path, behaviour: str) -> None:
    """Not ready yet means XNAT is still loading, so the *other* password must not be tried.

    Probing both credentials on every poll sends one wrong-password login every few seconds, which
    trips XNAT's 20-attempt account lockout long before the wait budget expires.
    """
    result = _run_readiness(
        _readiness_env(
            tmp_path,
            behaviour,
            timeout="0",
            initial_password="initial",  # pragma: allowlist secret
            rotated_password="rotated",  # pragma: allowlist secret
        )
    )

    assert result.returncode != 0
    assert not any("admin:rotated" in line for line in _requests(tmp_path))


def test_readiness_stops_well_before_lockout_when_both_credentials_are_rejected(tmp_path: Path) -> None:
    """Waiting cannot fix a wrong password, so fail fast instead of burning the whole budget."""
    result = _run_readiness(
        _readiness_env(
            tmp_path,
            "status=401",
            timeout="900",
            initial_password="initial",  # pragma: allowlist secret
            rotated_password="rotated",  # pragma: allowlist secret
        )
    )

    assert result.returncode != 0
    assert "rejected every admin credential" in result.stderr
    probes = _requests(tmp_path)
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

    assert "xnat_artifacts.sh check plugin" in stdout
    assert stdout.index("xnat_artifacts.sh check plugin") < stdout.index("xnat-reset")


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
            # Without this the probe's output is framed by "Entering/Leaving directory" whenever
            # make runs as a sub-make (MAKELEVEL > 0) — which is exactly how CI invokes the suite,
            # via `make -C trust/xnat/tests unit_test`. A bare interactive run is MAKELEVEL 0 and
            # prints nothing, so the banner would only ever appear in CI.
            "--no-print-directory",
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


def test_an_empty_resolve_image_override_cannot_swallow_the_next_flag() -> None:
    """An explicitly cleared override must fail naming the flag, not consume the one after it.

    ``?=`` yields to a command-line assignment even when that assignment is empty, so
    ``make up-xnat XNAT_RESOLVE_IMAGE=`` expands to nothing. Unquoted, the empty word
    disappears from the recipe and ``--resolve-image`` takes ``--detach=false`` as its
    value, failing with ``invalid argument "--detach=false"`` — which reads as a bug in
    the Makefile rather than as the override the caller actually typed.
    """
    stdout = _dry_run_up_xnat(XNAT_RESOLVE_IMAGE="").stdout

    assert "--resolve-image --detach=false" not in stdout, (
        "an unquoted expansion lets --resolve-image consume --detach=false as its argument"
    )
    assert '--resolve-image "" --detach=false' in stdout, (
        "the empty override must reach docker as an empty value for --resolve-image"
    )


def test_up_xnat_skips_the_host_plugin_cache_outside_development() -> None:
    """Only the development stack bind-mounts plugins; elsewhere they are baked into the image.

    An on-prem or EC2 trust host never prepares the dev cache, so checking it there would fail a
    bring-up that has no use for it.
    """
    for prod in ("true", "stag"):
        stdout = _dry_run_up_xnat(PROD=prod).stdout
        assert "xnat_artifacts.sh" not in stdout, f"PROD={prod} still reaches for the dev cache"


def test_root_smoke_target_resolves_relative_paths_from_repo_root() -> None:
    result = subprocess.run(
        [
            "make",
            "-n",
            "e2e_smoke",
            "MODEL_FILES_DIR=fl-tutorials/example/app_files",  # pragma: allowlist secret
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
    for fragment in ("env_mode.mk", "fl_backend.mk", "instance.mk"):
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

    status, calls = _run_up_loop(REPO_ROOT / "trust" / "xnat" / "Makefile", trust_dir / "xnat", tmp_path, child_exit=0)

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


def _run_kit_target(makefile: Path, target: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
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
def test_slot_guard_refuses_every_kit_target_when_another_kit_holds_the_project(tmp_path: Path, target: str) -> None:
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
    env = _docker_stub(tmp_path, container_ids="c0ffeec0ffee", environment_file=SAME_KIT_OTHER_CHECKOUT)

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

    result = _run_kit_target(REPO_ROOT / "trust" / "xnat" / "Makefile", "create-xnat-network", trust_dir / "xnat", env)

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

    result = _run_kit_target(REPO_ROOT / "trust" / "xnat" / "Makefile", "create-xnat-network", trust_dir / "xnat", env)

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

    result = _run_kit_target(REPO_ROOT / "trust" / "xnat" / "Makefile", "create-xnat-network", trust_dir / "xnat", env)

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
def test_abs_or_relative_to_only_joins_relative_values(tmp_path: Path, value: str, expected: str) -> None:
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
