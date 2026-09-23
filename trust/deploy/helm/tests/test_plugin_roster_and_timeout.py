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

"""Guards for FLIP#1228: the rollout, the deploy budget, and the roster check.

The bug was a correct chart that never reached the cluster. The plugin roster has been
right since August, yet the running pod kept jars that crash XNAT 1.10.0's importer on
every C-STORE while C-ECHO still passed — and five upgrades in a row read as a hook
problem rather than as an un-deployed chart.

Three things therefore have to stay true, and none is visible in a rendered chart:

* xnat-web is REPLACED on upgrade, not rolled: a singleton on a ReadWriteOnce volume
  cannot surge a second pod, so under RollingUpdate the rollout stalls and the old pod
  serves on with the old jars however long the deploy waits;
* the deploy timeout is ONE knob an operator can raise, not a literal per wait site;
* the roster check derives its expected filenames from the same URLs the chart
  downloads, so it cannot agree with a stale pod by carrying its own copy of the
  versions.
"""

import importlib.util
import re
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


# ── Filename derivation ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://github.com/NrgXnat/container-service/releases/download/3.8.1/container-service-3.8.1-fat.jar",
            "container-service-3.8.1-fat.jar",
        ),
        (
            "https://api.bitbucket.org/2.0/repositories/xnatdev/dicom-query-retrieve/downloads/"
            "dicom-query-retrieve-3.0.0-xpl.jar",
            "dicom-query-retrieve-3.0.0-xpl.jar",
        ),
        ("https://xnat.org/files/ohif-viewer-xnat-plugin/ohif-viewer-3.7.2.jar", "ohif-viewer-3.7.2.jar"),
        # A query string is not part of the filename the chart writes.
        ("https://example.org/downloads/batch-launch-0.9.0-xpl.jar?token=abc", "batch-launch-0.9.0-xpl.jar"),
    ],
)
def test_plugin_jar_name_matches_the_charts_own_derivation(url: str, expected: str) -> None:
    """The template takes the basename of the URL path; so must this."""
    assert check_status.plugin_jar_name(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # A redirect-to-asset URL: the downloaded filename is decided by the server, so the
        # expected name cannot be derived here. Guessing one would fail a correct pod.
        "https://github.com/NrgXnat/container-service/releases/latest/download",
        "https://example.org/plugins/",
        "",
        None,
    ],
)
def test_plugin_jar_name_declines_what_it_cannot_derive(url: object) -> None:
    """Return None rather than a guess — a wrong expectation reads as a stale pod."""
    assert check_status.plugin_jar_name(url) is None


# ── The comparison ───────────────────────────────────────────────────────────────────


def test_a_matching_roster_reports_nothing() -> None:
    """Extra jars the values do not name are not the pod's fault (an operator may add one)."""
    expected = {"dicom-query-retrieve": "dicom-query-retrieve-3.0.0-xpl.jar"}
    found = ["dicom-query-retrieve-3.0.0-xpl.jar", "some-site-plugin-1.0.jar"]

    assert check_status.compare_plugin_roster(expected, found) == ([], [])


def test_a_different_version_of_the_same_plugin_is_stale_not_missing() -> None:
    """This is the FLIP#1228 signal, and the message has to name both versions.

    "expected jar absent" and "wrong jar present" are different diagnoses: the first is a
    failed download, the second is a pod spec older than the values. Reporting the second
    as the first sends the operator to the init container's logs, which are clean.
    """
    expected = {
        "dicom-query-retrieve": "dicom-query-retrieve-3.0.0-xpl.jar",
        "container-service": "container-service-3.8.1-fat.jar",
    }
    found = ["dicom-query-retrieve-2.3.2-xpl.jar", "container-service-3.8.0-fat.jar"]

    missing, stale = check_status.compare_plugin_roster(expected, found)

    assert missing == []
    assert stale == [
        ("container-service", "container-service-3.8.1-fat.jar", "container-service-3.8.0-fat.jar"),
        ("dicom-query-retrieve", "dicom-query-retrieve-3.0.0-xpl.jar", "dicom-query-retrieve-2.3.2-xpl.jar"),
    ]


def test_an_absent_plugin_is_missing_not_stale() -> None:
    """Nothing of that name on disk means the download failed, not that the spec is old."""
    expected = {"ohif-viewer": "ohif-viewer-3.7.2.jar"}

    missing, stale = check_status.compare_plugin_roster(expected, ["container-service-3.8.1-fat.jar"])

    assert stale == []
    assert missing == [("ohif-viewer", "ohif-viewer-3.7.2.jar")]


def test_a_plugin_whose_name_prefixes_another_is_not_mistaken_for_it() -> None:
    """``container-service`` must not claim ``container-service-extras-*`` as its stale copy.

    ``container-service-`` prefixes ``container-service-extras-1.0.jar``, so a bare
    ``startswith`` reports a container-service that never downloaded as *stale*, naming a
    jar the operator must not touch and hiding the failed download. The expected jar is
    deliberately absent here, so the prefix branch really is the one under test.
    """
    expected = {"container-service": "container-service-3.8.1-fat.jar"}

    missing, stale = check_status.compare_plugin_roster(expected, ["container-service-extras-1.0.jar"])

    assert stale == []
    assert missing == [("container-service", "container-service-3.8.1-fat.jar")]


def test_a_jar_another_key_expects_is_never_reported_as_this_plugins_stale_copy() -> None:
    """Two roster keys, one present jar: it belongs to the key that expects it.

    ``foo-1.jar`` satisfies the digit-after-``<key>-`` rule for key ``foo`` as well, so
    without the roster's own claim being honoured, ``foo`` would be reported stale against
    a jar that is simply ``foo-1``'s, up to date and doing its job.
    """
    expected = {"foo": "foo-2.0.jar", "foo-1": "foo-1.jar"}

    missing, stale = check_status.compare_plugin_roster(expected, ["foo-1.jar"])

    assert stale == []
    assert missing == [("foo", "foo-2.0.jar")]


# ── Reading the roster from the live release ─────────────────────────────────────────


def test_expected_plugin_jars_ignores_a_release_with_xnat_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """kind CI deploys with xnat.enabled=false; the check must skip, not fail."""
    monkeypatch.setattr(check_status, "check_command", lambda _cmd: True)
    monkeypatch.setattr(
        check_status,
        "run_command",
        lambda *_a, **_k: (True, '{"xnat": {"enabled": false, "web": {"plugins": {"urls": {"a": "x/a-1.jar"}}}}}'),
    )

    roster = check_status.expected_plugin_jars("trust-release", "flip-trust")

    assert roster.status is check_status.RosterLookup.DISABLED


def test_expected_plugin_jars_skips_a_release_with_plugins_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """plugins.enabled=false means no download container and legitimately no jars.

    Comparing anyway FAILs every plugin and sends the operator to the logs of a container
    that was never created.
    """
    monkeypatch.setattr(check_status, "check_command", lambda _cmd: True)
    monkeypatch.setattr(
        check_status,
        "run_command",
        lambda *_a, **_k: (
            True,
            '{"xnat": {"enabled": true, "web": {"plugins": {"enabled": false, "urls": {"a": "x/a-1.jar"}}}}}',
        ),
    )

    roster = check_status.expected_plugin_jars("trust-release", "flip-trust")

    assert roster.status is check_status.RosterLookup.DISABLED
    assert "plugins.enabled" in roster.detail


@pytest.mark.parametrize(
    ("command_ok", "result", "because"),
    [
        (True, (False, "Error: release: not found"), "helm errored or timed out"),
        (True, (True, "   "), "helm returned nothing"),
        (True, (True, "{not json"), "helm returned unparseable JSON"),
        (False, (True, "{}"), "helm is not installed"),
    ],
)
def test_a_roster_that_cannot_be_read_is_not_reported_as_disabled(
    monkeypatch: pytest.MonkeyPatch, command_ok: bool, result: tuple[bool, str], because: str
) -> None:
    """UNREADABLE, never DISABLED — otherwise a broken helm silently turns this check off.

    The caller prints DISABLED as INFO, which does not touch the warning counter, so
    collapsing the two lets ``make status`` exit green with the one check that catches
    FLIP#1228 never having run.
    """
    monkeypatch.setattr(check_status, "check_command", lambda _cmd: command_ok)
    monkeypatch.setattr(check_status, "run_command", lambda *_a, **_k: result)

    roster = check_status.expected_plugin_jars("trust-release", "flip-trust")

    assert roster.status is check_status.RosterLookup.UNREADABLE, because
    assert roster.detail, "an unreadable roster must say why, or the operator cannot act on it"


def test_expected_plugin_jars_reads_the_live_values_not_the_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """``helm get values`` is the source: a checkout can be ahead of what is deployed.

    Reading values.yaml off disk would have called the roster correct for the whole of
    FLIP#1228 — the file was right, the release was not.
    """
    seen: dict[str, list[str]] = {}

    def fake_run(args: list[str], **_kwargs: object) -> tuple[bool, str]:
        seen["args"] = args
        return True, (
            # `--all` renders chart defaults too, so plugins.enabled is present in real
            # output; the check mirrors the template's own `and .Values…enabled`, which
            # treats an absent key as off.
            '{"xnat": {"enabled": true, "web": {"plugins": {"enabled": true, "urls": {'
            '"container-service": "https://x/container-service-3.8.1-fat.jar",'
            '"dicom-query-retrieve": "https://y/dicom-query-retrieve-3.0.0-xpl.jar",'
            '"broken": "https://z/no-extension"'
            "}}}}}"
        )

    monkeypatch.setattr(check_status, "check_command", lambda _cmd: True)
    monkeypatch.setattr(check_status, "run_command", fake_run)

    roster = check_status.expected_plugin_jars("trust-release", "flip-trust")

    assert roster.status is check_status.RosterLookup.OK
    assert roster.jars == {
        "container-service": "container-service-3.8.1-fat.jar",
        "dicom-query-retrieve": "dicom-query-retrieve-3.0.0-xpl.jar",
    }, "a URL with no derivable jar name must be dropped, not compared as an empty filename"
    assert roster.undrivable == ("broken",), (
        "a URL the chart's own download-plugins would SystemExit on must be reported, not "
        "silently dropped — that release cannot start a pod at all"
    )
    assert seen["args"][:4] == ["helm", "get", "values", "trust-release"]
    assert "--all" in seen["args"], "without --all, chart defaults are absent and every plugin looks unconfigured"


def test_expected_plugin_jars_declines_without_helm(monkeypatch: pytest.MonkeyPatch) -> None:
    """No helm means no live values; skip rather than fall back to the checkout."""
    monkeypatch.setattr(check_status, "check_command", lambda _cmd: False)

    assert check_status.expected_plugin_jars("trust-release", "flip-trust").status is (
        check_status.RosterLookup.UNREADABLE
    )


# ── The C-STORE smoke ────────────────────────────────────────────────────────────────


def test_the_cstore_smoke_reads_the_receiver_log() -> None:
    """Asserting only the association status is the false confidence this fixes.

    C-ECHO passed throughout FLIP#1228. A smoke that checks the store returned success and
    stops there would have passed too, because the abort happens in the importer after the
    association is established.
    """
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    assert "AbstractMethodError" in script, "the smoke does not look for the importer crash it exists to catch"
    assert "dicom.log" in script, "the smoke never reads the receiver's own log"
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


# ── The check itself: pod selection and the paths that report nothing ────────────────


@pytest.fixture
def reported(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Collect ``print_status`` calls instead of printing them."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(check_status, "print_status", lambda status, message: calls.append((status, message)))
    return calls


def _roster(**kwargs: object) -> object:
    """An OK roster asking for one jar, unless overridden."""
    defaults: dict[str, object] = {
        "status": check_status.RosterLookup.OK,
        "jars": {"dicom-query-retrieve": "dicom-query-retrieve-3.0.0-xpl.jar"},
    }
    defaults.update(kwargs)
    return check_status.PluginRoster(**defaults)  # type: ignore[arg-type]


def test_every_running_pod_is_checked_so_a_surge_cannot_mask_a_stale_one(
    monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]
) -> None:
    """Running is not Ready, and the Service may still route to the old pod.

    Taking pods[0] would report the NEW pod's correct jars and PASS while every C-STORE
    still lands on the stale one — the false confidence FLIP#1228 was made of. The stale
    pod is deliberately second here, so a first-pod-only check passes this scenario.
    """
    monkeypatch.setattr(check_status, "expected_plugin_jars", lambda *_a: _roster())
    monkeypatch.setattr(check_status, "kubectl_list", lambda *_a, **_k: ["pod/xnat-new", "pod/xnat-old"])

    listings = {
        "pod/xnat-new": "dicom-query-retrieve-3.0.0-xpl.jar",
        "pod/xnat-old": "dicom-query-retrieve-2.3.2-xpl.jar",
    }
    monkeypatch.setattr(check_status, "run_command", lambda args, **_k: (True, listings[args[2]]))

    check_status.check_xnat_plugin_roster("trust-release", "flip-trust")

    fails = [message for status, message in reported if status == "FAIL"]
    assert any("xnat-old" in message and "2.3.2" in message for message in fails), (
        "the stale second pod was not reported — a surged pod can hide the one still serving"
    )
    assert not [message for status, message in reported if status == "PASS"], (
        "the roster must not PASS while any running pod carries the wrong jars"
    )
    assert any(status == "WARN" and "2 xnat-web pods" in message for status, message in reported), (
        "two running pods means a rollout in progress or stalled, and the operator needs telling"
    )


def test_a_roster_that_could_not_be_read_warns_rather_than_skipping_quietly(
    monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]
) -> None:
    """INFO does not touch the warning counter, so a broken helm would exit green."""
    monkeypatch.setattr(
        check_status,
        "expected_plugin_jars",
        lambda *_a: _roster(status=check_status.RosterLookup.UNREADABLE, jars={}, detail="helm timed out"),
    )

    check_status.check_xnat_plugin_roster("trust-release", "flip-trust")

    assert [status for status, _ in reported] == ["WARN"]
    assert "helm timed out" in reported[0][1]


def test_a_disabled_release_is_skipped_without_a_warning(
    monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]
) -> None:
    """XNAT off is a legitimate configuration (kind CI), not a degraded run."""
    monkeypatch.setattr(
        check_status,
        "expected_plugin_jars",
        lambda *_a: _roster(status=check_status.RosterLookup.DISABLED, jars={}, detail="xnat.enabled is false"),
    )

    check_status.check_xnat_plugin_roster("trust-release", "flip-trust")

    assert [status for status, _ in reported] == ["INFO"]


def test_an_undrivable_url_is_reported_rather_than_dropped(
    monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]
) -> None:
    """The chart's own download container SystemExits on it, so no pod can start."""
    monkeypatch.setattr(check_status, "expected_plugin_jars", lambda *_a: _roster(undrivable=("broken",)))
    monkeypatch.setattr(check_status, "kubectl_list", lambda *_a, **_k: ["pod/xnat-1"])
    monkeypatch.setattr(
        check_status, "run_command", lambda *_a, **_k: (True, "dicom-query-retrieve-3.0.0-xpl.jar")
    )

    check_status.check_xnat_plugin_roster("trust-release", "flip-trust")

    assert any(status == "WARN" and "broken" in message for status, message in reported)


def test_no_running_pod_warns(monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]) -> None:
    """Nothing to inspect is unverified, not verified."""
    monkeypatch.setattr(check_status, "expected_plugin_jars", lambda *_a: _roster())
    monkeypatch.setattr(check_status, "kubectl_list", lambda *_a, **_k: [])

    check_status.check_xnat_plugin_roster("trust-release", "flip-trust")

    assert [status for status, _ in reported] == ["WARN"]
    assert not [message for status, message in reported if status == "PASS"]


def test_an_exec_failure_leaves_the_roster_unverified_rather_than_passing(
    monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]
) -> None:
    """A pod whose plugin dir could not be listed says nothing about its jars."""
    monkeypatch.setattr(check_status, "expected_plugin_jars", lambda *_a: _roster())
    monkeypatch.setattr(check_status, "kubectl_list", lambda *_a, **_k: ["pod/xnat-1"])
    monkeypatch.setattr(check_status, "run_command", lambda *_a, **_k: (False, "error: unable to upgrade connection"))

    check_status.check_xnat_plugin_roster("trust-release", "flip-trust")

    assert [status for status, _ in reported] == ["WARN"]
    assert not [message for status, message in reported if status == "PASS"], (
        "an unlistable pod must not count as a clean one"
    )


def test_a_single_matching_pod_passes(monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, str]]) -> None:
    """The happy path still reports PASS, naming the jars it matched."""
    monkeypatch.setattr(check_status, "expected_plugin_jars", lambda *_a: _roster())
    monkeypatch.setattr(check_status, "kubectl_list", lambda *_a, **_k: ["pod/xnat-1"])
    monkeypatch.setattr(
        check_status, "run_command", lambda *_a, **_k: (True, "dicom-query-retrieve-3.0.0-xpl.jar\nREADME")
    )

    check_status.check_xnat_plugin_roster("trust-release", "flip-trust")

    assert [status for status, _ in reported] == ["PASS"]
    assert "dicom-query-retrieve-3.0.0-xpl.jar" in reported[0][1]


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

    for assignment in ("ORTHANC_CREDS=$(", "LOG_MARK=$(", "INSTANCE_ID=$(orthanc_curl"):
        starts = [i for i, line in enumerate(lines) if line.lstrip().startswith(assignment)]
        assert starts, f"{assignment}…) has gone — re-check this guard"
        statement = logical_line_at(starts[0])
        assert "|| fail" in statement, (
            f"`{assignment}…)` has no `|| fail` naming its reason, so a failing kubectl or curl "
            f"kills the script with only its own stderr. Statement was:\n{statement}"
        )
