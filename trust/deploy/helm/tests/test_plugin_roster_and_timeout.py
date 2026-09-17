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

"""Guards for the two halves of FLIP#1228: the deploy budget, and the roster check.

The bug was a correct chart that never reached the cluster. Five upgrades in a row
applied the corrected plugin roster and then failed their post-upgrade hook on a
hardcoded ``--timeout 20m``, shorter than the job's real ~22 minutes — so the failure
named Helm, the operator read it as a hook problem, and the pod kept running jars that
crash XNAT 1.10.0's importer on every C-STORE while C-ECHO still passed.

Two things therefore have to stay true, and neither is visible in a rendered chart:

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

    The prefix match is on ``<key>-``, so a longer plugin name starting with a shorter one
    would otherwise be reported as the wrong version of it.
    """
    expected = {"container-service": "container-service-3.8.1-fat.jar"}

    missing, stale = check_status.compare_plugin_roster(expected, ["container-service-3.8.1-fat.jar"])
    assert (missing, stale) == ([], [])


# ── Reading the roster from the live release ─────────────────────────────────────────


def test_expected_plugin_jars_ignores_a_release_with_xnat_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """kind CI deploys with xnat.enabled=false; the check must skip, not fail."""
    monkeypatch.setattr(check_status, "check_command", lambda _cmd: True)
    monkeypatch.setattr(
        check_status,
        "run_command",
        lambda *_a, **_k: (True, '{"xnat": {"enabled": false, "web": {"plugins": {"urls": {"a": "x/a-1.jar"}}}}}'),
    )

    assert check_status.expected_plugin_jars("trust-release", "flip-trust") is None


def test_expected_plugin_jars_reads_the_live_values_not_the_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """``helm get values`` is the source: a checkout can be ahead of what is deployed.

    Reading values.yaml off disk would have called the roster correct for the whole of
    FLIP#1228 — the file was right, the release was not.
    """
    seen: dict[str, list[str]] = {}

    def fake_run(args: list[str], **_kwargs: object) -> tuple[bool, str]:
        seen["args"] = args
        return True, (
            '{"xnat": {"enabled": true, "web": {"plugins": {"urls": {'
            '"container-service": "https://x/container-service-3.8.1-fat.jar",'
            '"dicom-query-retrieve": "https://y/dicom-query-retrieve-3.0.0-xpl.jar",'
            '"broken": "https://z/no-extension"'
            "}}}}}"
        )

    monkeypatch.setattr(check_status, "check_command", lambda _cmd: True)
    monkeypatch.setattr(check_status, "run_command", fake_run)

    jars = check_status.expected_plugin_jars("trust-release", "flip-trust")

    assert jars == {
        "container-service": "container-service-3.8.1-fat.jar",
        "dicom-query-retrieve": "dicom-query-retrieve-3.0.0-xpl.jar",
    }, "a URL with no derivable jar name must be dropped, not compared as an empty filename"
    assert seen["args"][:4] == ["helm", "get", "values", "trust-release"]
    assert "--all" in seen["args"], "without --all, chart defaults are absent and every plugin looks unconfigured"


def test_expected_plugin_jars_declines_without_helm(monkeypatch: pytest.MonkeyPatch) -> None:
    """No helm means no live values; skip rather than fall back to the checkout."""
    monkeypatch.setattr(check_status, "check_command", lambda _cmd: False)

    assert check_status.expected_plugin_jars("trust-release", "flip-trust") is None


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
