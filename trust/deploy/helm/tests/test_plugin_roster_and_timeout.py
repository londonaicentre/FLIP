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

"""Guards for FLIP#1228: the rollout, the deploy budget, and the plugin check.

The bug was a correct chart that never reached the cluster. The plugin roster has been
right since August, yet the running pod kept jars that crash XNAT 1.10.0's importer on
every C-STORE while C-ECHO still passed — and five upgrades in a row read as a hook
problem rather than as an un-deployed chart.

Three things therefore have to stay true, and none is visible in a rendered chart:

* xnat-web is REPLACED on upgrade, not rolled: a singleton on a ReadWriteOnce volume
  cannot surge a second pod, so under RollingUpdate the rollout stalls and the old pod
  serves on with the old jars however long the deploy waits;
* the deploy timeout is ONE knob an operator can raise, not a literal per wait site;
* the plugin check asks XNAT on EVERY running xnat-web pod which plugins it loaded, so
  a surged pod cannot hide the one still serving.
"""

import base64
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
MAKEFILE = CHART_DIR / "Makefile"
TEMPLATES_DIR = CHART_DIR / "templates"

_SCRIPT = CHART_DIR / "check_status.py"
_spec = importlib.util.spec_from_file_location("check_status", _SCRIPT)
check_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_status)


# ── The deploy budget ────────────────────────────────────────────────────────────────


def test_helm_timeout_is_a_single_overridable_knob() -> None:
    """``HELM_TIMEOUT ?=`` so a slow site raises it on the command line."""
    text = MAKEFILE.read_text()

    assert re.search(r"^HELM_TIMEOUT \?= \S+$", text, re.MULTILINE), (
        "the Makefile no longer declares HELM_TIMEOUT with ?=, so a site whose xnat-init job "
        "runs longer than the default cannot raise the budget without editing the Makefile"
    )


def test_no_hardcoded_timeout_literal_waits_on_the_init_job() -> None:
    """A second literal is a second budget, and the two then drift apart.

    ``deploy`` and ``xnat-init`` wait on the SAME job by two different mechanisms. They
    carried 20m and 15m respectively; raising one left the other short, and whichever
    expired first blamed itself rather than the wait.
    """
    duration = re.compile(r"--timeout[= ](\d+[smh])")
    literals = [
        literal
        for line in MAKEFILE.read_text().splitlines()
        # The rollout-status wait answers a different question and keeps its own budget;
        # see the test below.
        if "rollout status" not in line
        for literal in duration.findall(line)
    ]

    assert not literals, (
        f"hardcoded --timeout literal(s) {literals} in the Makefile — every wait on the xnat-init "
        "job must read $(HELM_TIMEOUT), or raising the budget silently misses one of them. "
        "(A rollout-status wait on a Deployment is not one of these; give it its own variable.)"
    )


def test_the_rollout_waits_are_not_swept_up_by_the_same_knob() -> None:
    """The patch-kit-secrets rollout wait is a different question and keeps its own value.

    Stated so the guard above is read as "no literal *for the init job*" rather than "no
    literal anywhere": a 120s rollout wait on three small API Deployments has nothing to
    do with how long XNAT takes to initialise.
    """
    text = MAKEFILE.read_text()

    assert "rollout status deployment/" in text, "the rollout-status wait has gone — re-check the guard above"
    assert "--timeout=120s" in text or "$(ROLLOUT_TIMEOUT)" in text, (
        "the rollout wait lost its own budget; if it now reads $(HELM_TIMEOUT), a site that "
        "raises the XNAT budget to 45m also waits 45m for a trust-api that will never come up"
    )


# ── The plugin list ──────────────────────────────────────────────────────────────────


def test_xnat_plugin_ids_cover_exactly_the_manifests_plugins() -> None:
    """The image is built from the manifest, so each plugin there needs its XNAT id here, and no more."""
    manifest = CHART_DIR.parents[1] / "xnat" / "artifacts.manifest"
    jars = [
        line.split()[2].rsplit("/", 1)[-1] for line in manifest.read_text().splitlines() if line.startswith("plugin")
    ]
    keys = {re.match(r"(.+?)-\d", jar).group(1) for jar in jars}

    assert keys == set(check_status.XNAT_PLUGIN_IDS), "add the new plugin's `id=` to XNAT_PLUGIN_IDS"
    # As read from each jar's META-INF/xnat/*-plugin.properties and from /xapi/plugins (XNAT 1.10.0).
    assert check_status.XNAT_PLUGIN_IDS == {
        "batch-launch": "batchLaunchPlugin",
        "container-service": "containers",
        "dicom-query-retrieve": "dicom-query-retrieve",
        "ohif-viewer": "ohifViewerPlugin",
    }


# ── Reading the live release ─────────────────────────────────────────────────────────


def test_a_release_with_xnat_off_reads_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """kind CI deploys with xnat.enabled=false; the check must skip, not fail."""
    seen: dict[str, list[str]] = {}

    def fake_run(args: list[str], **_kwargs: object) -> tuple[bool, str]:
        seen["args"] = args
        return True, '{"xnat": {"enabled": false}}'

    monkeypatch.setattr(check_status, "check_command", lambda _cmd: True)
    monkeypatch.setattr(check_status, "run_command", fake_run)

    assert check_status.xnat_enabled("trust-release", "flip-trust") == (False, "")
    assert seen["args"][:4] == ["helm", "get", "values", "trust-release"]
    assert "--all" in seen["args"], "without --all, chart defaults are absent and xnat.enabled looks off"


@pytest.mark.parametrize(
    ("command_ok", "result", "because"),
    [
        (True, (False, "Error: release: not found"), "helm errored or timed out"),
        (True, (True, "   "), "helm returned nothing"),
        (True, (True, "{not json"), "helm returned unparseable JSON"),
        (True, (True, "null"), "helm returned no values"),
        (False, (True, "{}"), "helm is not installed"),
    ],
)
def test_values_that_cannot_be_read_are_not_reported_as_off(
    monkeypatch: pytest.MonkeyPatch, command_ok: bool, result: tuple[bool, str], because: str
) -> None:
    """Unreadable, never off — otherwise a broken helm silently turns this check off.

    The caller prints "off" as INFO, which does not touch the warning counter, so
    collapsing the two lets ``make status`` exit green with the check never having run.
    """
    monkeypatch.setattr(check_status, "check_command", lambda _cmd: command_ok)
    monkeypatch.setattr(check_status, "run_command", lambda *_a, **_k: result)

    enabled, why = check_status.xnat_enabled("trust-release", "flip-trust")

    assert enabled is None, because
    assert why, "unreadable values must say why, or the operator cannot act on it"


# ── The C-STORE smoke ────────────────────────────────────────────────────────────────


def test_the_cstore_smoke_reads_the_receiver_log() -> None:
    """Asserting only the association status is the false confidence this fixes.

    C-ECHO passed throughout FLIP#1228. A smoke that checks the store returned success and
    stops there would have passed too, because the abort happens in the importer after the
    association is established. The importer-failure patterns now live in
    ``scripts/cstore_verdict.py`` (see tests/test_cstore_verdict.py), which judges the
    receiver's log; the shell gathers it.
    """
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()
    verdict = (CHART_DIR / "scripts" / "cstore_verdict.py").read_text()

    assert "AbstractMethodError" in verdict, "the smoke does not look for the importer crash it exists to catch"
    assert "dicom.log" in script, "the smoke never reads the receiver's own log"
    container_log = "the smoke never reads the receiver's container log"
    assert "kubectl" in script, container_log
    assert "logs" in script, container_log
    assert "FailedInstancesCount" in script, "the smoke does not check whether the transfer itself succeeded"


def test_the_cstore_smoke_only_judges_lines_this_transfer_wrote() -> None:
    """A long-running trust has old errors in dicom.log; scanning it whole fails on history."""
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    reason = (
        "the smoke no longer marks the log before storing, so it either fails on errors that "
        "predate the run or passes by reading only a fixed tail"
    )
    assert "LOG_MARK" in script, reason
    assert "tail -n +" in script, reason


def test_the_instance_lookup_sends_since_with_limit() -> None:
    """Orthanc rejects ``limit`` without ``since`` with a 400.

    ``orthanc_curl`` runs ``curl -sS`` without ``--fail``, so that error body comes back
    on stdout with exit 0 and is mangled by the same ``tr``/``cut`` into something that
    looks like an instance id. The store then fails in Orthanc's words rather than the
    smoke's, and the run reads as a broken receiver rather than a bad listing.
    """
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    listing = re.search(r"/instances\?[^\"'\s]*", script)
    assert listing, "the smoke no longer lists Orthanc's instances to pick something to send"
    assert "since=" in listing.group(0), (
        f"{listing.group(0)} passes limit without since, which Orthanc answers with a 400"
    )


# ── The rollout that has to replace the pod ──────────────────────────────────────────


def test_xnat_web_replaces_its_pod_rather_than_surging_a_second_one() -> None:
    """A singleton on a ReadWriteOnce PVC cannot roll; it has to be replaced.

    Left at the default RollingUpdate, an upgrade surges the new pod ALONGSIDE the old
    one. The second XNAT is refused the volume on another node and shares one data
    directory and one database on the same node, so it never goes Ready — the rollout
    never completes and the old pod serves on with the old plugin jars, which is a
    corrected roster that never reaches the cluster no matter how long the deploy waits.
    orthanc.yaml carries the same declaration for the same reason.
    """
    spec = (TEMPLATES_DIR / "xnat-web.yaml").read_text()
    deployment = next(doc for doc in spec.split("\n---") if "kind: Deployment" in doc)

    assert re.search(r"^\s*strategy:\s*$\n\s*type:\s*Recreate\s*$", deployment, re.MULTILINE), (
        "the xnat-web Deployment no longer declares strategy.type=Recreate, so an upgrade "
        "surges a second XNAT onto the same RWO volume and database and the rollout stalls "
        "with the old pod still serving"
    )


# ── The check itself: GET /xapi/plugins on every running pod ─────────────────────────

ALL_IDS = tuple(check_status.XNAT_PLUGIN_IDS.values())


@pytest.fixture
def reported(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Collect ``print_status`` calls instead of printing them, for a release that runs XNAT."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(check_status, "print_status", lambda status, message: calls.append((status, message)))
    monkeypatch.setattr(check_status, "xnat_enabled", lambda *_a: (True, ""))
    return calls


def _xapi_plugins(*plugin_ids: str, status: str = "200") -> str:
    """What the in-pod fetch prints: the GET /xapi/plugins body, a newline, the HTTP status."""
    return json.dumps({pid: {"id": pid, "version": "1.0"} for pid in plugin_ids}) + f"\n{status}"


def _pods(monkeypatch: pytest.MonkeyPatch, fetched: dict[str, str | None]) -> None:
    """Running xnat-web pods and what each one's /xapi/plugins fetch prints (None: the exec fails)."""

    def fake_run(args: list[str], **_kwargs: object) -> tuple[bool, str]:
        out = fetched[args[2]]
        return (True, out) if out is not None else (False, "error: unable to upgrade connection")

    monkeypatch.setattr(check_status, "kubectl_list", lambda *_a, **_k: list(fetched))
    monkeypatch.setattr(check_status, "run_command", fake_run)


def test_every_running_pod_is_checked_so_a_surge_cannot_mask_a_broken_one(
    monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]
) -> None:
    """Running is not Ready, and the Service may still route to the old pod.

    Taking pods[0] would PASS the new pod while every C-STORE still lands on the old one.
    The broken pod is deliberately second here, so a first-pod-only check passes this scenario.
    """
    _pods(monkeypatch, {"pod/xnat-new": _xapi_plugins(*ALL_IDS), "pod/xnat-old": _xapi_plugins("containers")})

    check_status.check_xnat_plugins("trust-release", "flip-trust")

    fails = [message for status, message in reported if status == "FAIL"]
    assert any("xnat-old" in message and "dicom-query-retrieve" in message for message in fails), (
        "the broken second pod was not reported — a surged pod can hide the one still serving"
    )
    assert not any("xnat-new" in message for message in fails)
    assert any(status == "WARN" and "2 xnat-web pods" in message for status, message in reported), (
        "two running pods means a rollout in progress or stalled, and the operator needs telling"
    )


def test_values_that_could_not_be_read_warn_rather_than_skipping_quietly(
    monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]
) -> None:
    """INFO does not touch the warning counter, so a broken helm would exit green."""
    monkeypatch.setattr(check_status, "xnat_enabled", lambda *_a: (None, "helm timed out"))

    check_status.check_xnat_plugins("trust-release", "flip-trust")

    assert [status for status, _ in reported] == ["WARN"]
    assert "helm timed out" in reported[0][1]


def test_a_release_with_xnat_off_is_skipped_without_a_warning(
    monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]
) -> None:
    """XNAT off is a legitimate configuration (kind CI), not a degraded run."""
    monkeypatch.setattr(check_status, "xnat_enabled", lambda *_a: (False, ""))

    check_status.check_xnat_plugins("trust-release", "flip-trust")

    assert [status for status, _ in reported] == ["INFO"]


def test_no_running_pod_warns(monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]) -> None:
    """Nothing to inspect is unverified, not verified."""
    _pods(monkeypatch, {})

    check_status.check_xnat_plugins("trust-release", "flip-trust")

    assert [status for status, _ in reported] == ["WARN"]


def test_a_pod_that_loaded_every_plugin_passes(
    monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]
) -> None:
    _pods(monkeypatch, {"pod/xnat-1": _xapi_plugins(*ALL_IDS, "someSitePlugin")})

    check_status.check_xnat_plugins("trust-release", "flip-trust")

    assert reported == [("PASS", f"XNAT on 'pod/xnat-1' loaded all {len(ALL_IDS)} plugins")]


def test_each_plugin_xnat_did_not_load_fails(monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]) -> None:
    _pods(monkeypatch, {"pod/xnat-1": _xapi_plugins("containers", "ohifViewerPlugin")})

    check_status.check_xnat_plugins("trust-release", "flip-trust")

    assert [status for status, _ in reported] == ["FAIL", "FAIL"]
    assert "did not load the batch-launch plugin" in reported[0][1]
    assert "did not load the dicom-query-retrieve plugin" in reported[1][1]
    assert "tomcat container's startup log" in reported[0][1]


@pytest.mark.parametrize(
    ("fetched", "reason"),
    [
        (_xapi_plugins(status="401"), "HTTP 401"),
        ("<html>ok</html>\n200", "JSON object"),
        ('["containers"]\n200', "JSON object"),
        (None, "unable to upgrade connection"),
    ],
)
def test_when_xnat_cannot_be_asked_the_pod_is_unverified_not_passed(
    monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]], fetched: str | None, reason: str
) -> None:
    """Unreadable is never "nothing loaded" (a FAIL) nor "all loaded" (a PASS)."""
    _pods(monkeypatch, {"pod/xnat-1": fetched})

    check_status.check_xnat_plugins("trust-release", "flip-trust")

    assert [status for status, _ in reported] == ["WARN"], reported
    assert reason in reported[0][1]


def test_a_failed_exec_keeps_the_error_not_kubectls_container_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    """kubectl names the defaulted container on every exec into xnat-web; that is not the failure."""
    stderr = (
        'Defaulted container "tomcat" out of: tomcat, prepare-data-dirs (init), wait-for-xnat-db (init)\n'
        "error: container not found\n"
    )
    monkeypatch.setattr(check_status, "run_command", lambda *_a, **_k: (False, stderr))

    assert check_status.loaded_plugin_ids("pod/xnat-1", "flip-trust") == (None, "error: container not found")


@pytest.mark.parametrize("stderr", [None, b"partial \xff stderr"], ids=["none", "bytes"])
def test_a_timed_out_command_reports_text_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]], stderr: bytes | None
) -> None:
    """On a timeout ``e.stderr`` is None or bytes even with text=True; a wedged cluster must get a WARN.

    Driven through the real ``run_command``: the /xapi/plugins exec times out — the path that
    used to raise on ``None.splitlines()`` / ``bytes.startswith(str)``.
    """

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, 45, output=None, stderr=stderr)

    monkeypatch.setattr(check_status, "kubectl_list", lambda *_a, **_k: ["pod/xnat-1"])
    monkeypatch.setattr(check_status.subprocess, "run", run)

    check_status.check_xnat_plugins("trust-release", "flip-trust")

    assert [status for status, _ in reported] == ["WARN"], reported
    assert "Could not ask XNAT" in reported[0][1]


def test_a_real_timeout_is_reported_as_text() -> None:
    ok, message = check_status.run_command(["sleep", "5"], timeout=0.2)

    assert ok is False
    assert "timed out" in message


def test_the_fetch_puts_no_credential_in_any_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The credential is the pod's own, read in the pod and handed to curl on stdin.

    The kubectl argv is captured, then its in-pod part run here for real under ``sh`` with a stub
    ``curl`` that records its argv and stdin: the password is in neither kubectl's argv (it is never
    known on this side) nor curl's.
    """
    seen: list[list[str]] = []
    monkeypatch.setattr(check_status, "run_command", lambda args, **_k: (seen.append(args), (True, _xapi_plugins()))[1])
    check_status.loaded_plugin_ids("pod/xnat-1", "flip-trust")
    (argv,) = seen
    assert argv[:7] == ["kubectl", "exec", "pod/xnat-1", "-n", "flip-trust", "--", "sh"]

    stub = tmp_path / "curl"
    stub.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' "$*" > "{tmp_path}/argv"\ncat > "{tmp_path}/stdin"\nprintf \'{{}}\\n200\'\n'
    )
    stub.chmod(0o755)
    secret = "s3cret-pass"  # pragma: allowlist secret
    result = subprocess.run(
        argv[6:],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "XNAT_SERVICE_USER": "svc",
            "XNAT_SERVICE_PASSWORD": secret,
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not any(secret in arg for arg in argv)
    curl_argv = (tmp_path / "argv").read_text()
    assert secret not in curl_argv, "the password reached curl's argv"
    assert "-K -" in curl_argv, "curl no longer reads its config (the credential) from stdin"
    header = re.search(r'Authorization: Basic ([A-Za-z0-9+/=]+)"', (tmp_path / "stdin").read_text())
    assert header, "curl was not handed the credential on stdin"
    assert base64.b64decode(header.group(1)).decode() == f"svc:{secret}"


def test_the_fetch_refuses_without_the_pods_service_credential(tmp_path: Path) -> None:
    """No credential in the pod means no request at all, not an anonymous one that 401s."""
    stub = tmp_path / "curl"
    stub.write_text(f'#!/bin/sh\ntouch "{tmp_path}/called"\n')
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("XNAT_SERVICE_")}

    result = subprocess.run(
        ["sh", "-c", check_status._FETCH_LOADED_PLUGINS],
        env={**env, "PATH": f"{tmp_path}:{env['PATH']}"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "XNAT_SERVICE_PASSWORD" in result.stderr
    assert not (tmp_path / "called").exists()


def test_the_smoke_fails_a_curl_rather_than_reading_an_error_body() -> None:
    """`curl -sS` exits 0 on a 401/404, and Orthanc's 401 body is empty.

    Without `-f` a rotated credential reports "Orthanc holds no instances — seed the PACS
    first" against a fully seeded PACS, sending the operator to fix the wrong thing.
    """
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    assert re.search(r"curl -fsS|curl -sSf|curl --fail", script), (
        "orthanc_curl no longer passes --fail, so an HTTP error is indistinguishable from an "
        "empty result and is reported as a misleading success"
    )


def test_the_smoke_names_a_reason_for_each_failed_substitution() -> None:
    """Under `set -euo pipefail` a bare `$(kubectl …)` aborts on kubectl's stderr alone.

    The script's header promises every exit names its reason; a command substitution that
    dies unguarded breaks that promise exactly where the operator needs it most.
    """
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()
    lines = script.splitlines()

    def logical_line_at(index: int) -> str:
        """Join the statement starting at ``index`` across its backslash continuations."""
        collected = [lines[index]]
        while collected[-1].rstrip().endswith("\\") and index + 1 < len(lines):
            index += 1
            collected.append(lines[index])
        return "\n".join(collected)

    # The substitutions that run a remote command. `LOG_MARK`/`RECEIVED_MARK`/`RUN_START` are
    # now split out of `MARKS`/`CLOCK` by local awk, which cannot fail on a cluster.
    for assignment in ("ORTHANC_CREDS=$(", "MARKS=$(", "CLOCK=$(", "INSTANCE_ID=$(orthanc_curl"):
        starts = [i for i, line in enumerate(lines) if line.lstrip().startswith(assignment)]
        assert starts, f"{assignment}…) has gone — re-check this guard"
        statement = logical_line_at(starts[0])
        assert "|| fail" in statement, (
            f"`{assignment}…)` has no `|| fail` naming its reason, so a failing kubectl or curl "
            f"kills the script with only its own stderr. Statement was:\n{statement}"
        )


def test_the_smoke_acts_on_the_chosen_cluster() -> None:
    """A box with several kind clusters must not get whichever kubectl points at.

    The smoke reads a Secret and drives a real transfer, so the wrong cluster costs more
    than a confusing result. The Makefile passes KUBE_CONTEXT through and every kubectl
    in the script goes via the array built from it.
    """
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()
    makefile = MAKEFILE.read_text()

    assert "KUBE_CONTEXT" in script, "the smoke ignores KUBE_CONTEXT and acts on kubectl's current context"
    bare = [
        line
        for line in script.splitlines()
        if re.search(r"(?<![\"$\w-])kubectl ", line) and not line.lstrip().startswith("#")
    ]
    assert bare == ['  KUBECTL=(kubectl --context "$KUBE_CONTEXT")'] or not bare, (
        f"these kubectl calls bypass the context-aware $KUBECTL: {bare}"
    )

    smoke_recipe = makefile[makefile.index("smoke-cstore:") :].split("\n\n")[0]
    assert "KUBE_CONTEXT" in smoke_recipe, (
        "make smoke-cstore does not forward KUBE_CONTEXT, so the script cannot honour it"
    )
