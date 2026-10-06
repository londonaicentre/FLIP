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

"""The C-STORE smoke's verdict: prearchive evidence, not dicom.log growth.

The check used to require XNAT's ``dicom.log`` to gain lines. The k3s trust never writes that
logger — the file stays 0 bytes while every store lands in the prearchive — so a healthy DICOM
path reported "dicom.log gained no lines" and failed. These tests pin the replacement: a new
prearchive object carrying the sent UID is the success evidence, dicom.log growth is a bonus,
and the FLIP#1228 importer signatures still fail the run however much else landed.

No cluster: ``decide()`` takes the evidence as a dict.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parent.parent
VERDICT_PY = CHART_DIR / "scripts" / "cstore_verdict.py"

_spec = importlib.util.spec_from_file_location("cstore_verdict", VERDICT_PY)
assert _spec is not None
assert _spec.loader is not None
cstore_verdict = importlib.util.module_from_spec(_spec)
# Register before exec: @dataclass resolves its module out of sys.modules.
sys.modules["cstore_verdict"] = cstore_verdict
_spec.loader.exec_module(cstore_verdict)

decide = cstore_verdict.decide


def evidence(**overrides: object) -> dict:
    """A healthy run: the store succeeded and the object is in the prearchive by SOP UID."""
    base: dict = {
        "store_failed_count": 0,
        "store_instance_count": 1,
        "receiver_log_lines": [],
        "dicom_log_new_lines": 0,
        "prearchive_matches": ["/data/xnat/prearchive/20261006_1/P/SCANS/1/DICOM/1.2.3-1.dcm"],
        "prearchive_match_mode": "sop",
        "prearchive_new": ["/data/xnat/prearchive/20261006_1/P/SCANS/1/DICOM/1.2.3-1.dcm"],
        "sop_uid": "1.2.3",
        "study_uid": "1.2.4",
    }
    base.update(overrides)
    return base


# ── The false negative this replaces ─────────────────────────────────────────────────


def test_a_silent_dicom_log_does_not_fail_a_store_that_reached_the_prearchive() -> None:
    """The exact k3s case: 0 failed instances, 0 new dicom.log lines, object in prearchive."""
    verdict = decide(evidence(dicom_log_new_lines=0))

    assert verdict.passed, verdict.reason
    assert any("prearchive" in note for note in verdict.notes)
    assert any("does not write that logger" in note for note in verdict.notes), (
        "a pass with a silent dicom.log does not explain why its silence is not a failure"
    )


def test_dicom_log_growth_is_reported_as_an_extra_signal_when_the_logger_is_active() -> None:
    """Where the logger IS written, its growth stays a positive signal — just not a requirement."""
    verdict = decide(evidence(dicom_log_new_lines=12))

    assert verdict.passed
    assert any("dicom.log also gained 12" in note for note in verdict.notes)


# ── The regression this smoke exists for (FLIP#1228) ─────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "ERROR org.nrg.dcm — java.lang.AbstractMethodError: org.nrg.dcm.Foo.bar()",
        "java.lang.NoSuchMethodError: org.nrg.xdat.X.y()",
        "WARN  unable to read DICOM object null",
    ],
)
def test_an_importer_failure_fails_the_run_whatever_landed(line: str) -> None:
    """A partially-imported object beside an AbstractMethodError is still the breakage."""
    verdict = decide(evidence(receiver_log_lines=[line], dicom_log_new_lines=1))

    assert not verdict.passed, f"{line!r} passed — the smoke no longer catches FLIP#1228"
    assert line in verdict.notes
    assert "plugins" in verdict.remedy
    assert "TROUBLESHOOTING.md" in verdict.remedy


def test_the_failure_signatures_are_matched_in_the_container_log_too() -> None:
    """On a deployment that never writes dicom.log the crash only surfaces on stdout."""
    pod_line = "2026-10-06 15:00:00 ERROR AbstractMethodError in the importer"
    verdict = decide(evidence(dicom_log_new_lines=0, receiver_log_lines=[pod_line]))

    assert not verdict.passed


def test_ordinary_log_lines_do_not_fail_the_run() -> None:
    verdict = decide(evidence(receiver_log_lines=["INFO received object, importing"], dicom_log_new_lines=1))

    assert verdict.passed


# ── The sender's own verdict ─────────────────────────────────────────────────────────


def test_a_failed_instance_fails_even_with_a_prearchive_object() -> None:
    verdict = decide(evidence(store_failed_count=1))

    assert not verdict.passed
    assert "1 failed instance" in verdict.reason


def test_an_unparsable_store_report_fails() -> None:
    """Indistinguishable from a store that never happened — passing on it restores the bug."""
    verdict = decide(evidence(store_failed_count=None))

    assert not verdict.passed
    assert "FailedInstancesCount" in verdict.reason


# ── Weaker and absent evidence ───────────────────────────────────────────────────────


def test_a_new_object_without_a_uid_match_passes_but_says_the_match_was_by_time() -> None:
    """XNAT can rewrite UIDs on receive; time-only evidence is real but weaker, and says so."""
    verdict = decide(
        evidence(prearchive_matches=[], prearchive_match_mode="", prearchive_new=["/data/xnat/prearchive/x.dcm"])
    )

    assert verdict.passed
    assert any("arrival time only" in note for note in verdict.notes)


def test_dicom_log_growth_alone_still_passes() -> None:
    """A deployment that imports straight past the prearchive keeps its old success route."""
    verdict = decide(
        evidence(prearchive_matches=[], prearchive_match_mode="", prearchive_new=[], dicom_log_new_lines=4)
    )

    assert verdict.passed
    assert any("no new prearchive object" in note for note in verdict.notes)


def test_no_receiver_side_evidence_at_all_fails() -> None:
    """A green association with nothing on the receiver is the false confidence to catch."""
    verdict = decide(
        evidence(prearchive_matches=[], prearchive_match_mode="", prearchive_new=[], dicom_log_new_lines=0)
    )

    assert not verdict.passed
    assert "no new prearchive object" in verdict.reason
    assert "TROUBLESHOOTING.md" in verdict.remedy


def test_a_study_uid_match_names_the_study_uid() -> None:
    verdict = decide(evidence(prearchive_match_mode="study"))

    assert verdict.passed
    assert any("StudyInstanceUID 1.2.4" in note for note in verdict.notes)


# ── The CLI the shell script actually calls ──────────────────────────────────────────


def _run_cli(ev: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VERDICT_PY)],
        input=json.dumps(ev),
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_cli_exits_zero_on_the_silent_dicom_log_case() -> None:
    result = _run_cli(evidence())

    assert result.returncode == 0, result.stderr
    assert "C-STORE smoke passed" in result.stdout


def test_the_cli_exits_non_zero_on_an_importer_failure() -> None:
    result = _run_cli(evidence(receiver_log_lines=["java.lang.AbstractMethodError: x"]))

    assert result.returncode == 1
    assert "importer failure" in result.stderr


# ── The shell script that gathers the evidence ───────────────────────────────────────


def test_the_smoke_script_checks_the_prearchive_and_no_longer_requires_log_growth() -> None:
    """The gatherer must look where the objects actually land, and defer the verdict."""
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    assert "prearchive" in script, "the smoke never looks at the prearchive, the only positive evidence"
    assert "cstore_verdict.py" in script, "the smoke does not hand its evidence to the tested decision function"
    assert "dicom.log gained no lines" not in script, (
        "the smoke still fails on a silent dicom.log — the false negative this fixes"
    )


def test_the_prearchive_window_uses_the_receivers_own_clock_in_epoch_seconds() -> None:
    """A locally-formatted timestamp against a UTC container finds nothing (or everything)."""
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    assert "date -u +%s" in script, "the smoke does not read the receiver's clock as epoch seconds"
    skew = "the prearchive scan does not compare against an absolute epoch, so TZ skew decides the result"
    assert "-newermt" in script, skew
    assert '"@$START"' in script, skew


def test_the_smoke_still_accepts_a_pinned_instance() -> None:
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    assert 'INSTANCE_ID="${INSTANCE_ID:-}"' in script, "the INSTANCE_ID= override is gone"


def test_the_smoke_asks_orthanc_for_the_uids_it_will_match_on() -> None:
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    forged = "the smoke matches the prearchive on arrival time alone, which a concurrent import can forge"
    assert "SOPInstanceUID" in script, forged
    assert "StudyInstanceUID" in script, forged
