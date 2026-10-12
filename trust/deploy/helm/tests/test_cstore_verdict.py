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

"""The C-STORE smoke's verdict: received.log and prearchive evidence, not dicom.log growth.

The check used to require XNAT's ``dicom.log`` to gain lines. That is the receiver's ERROR log —
successful receipts go to ``received.log`` — and the k3s trust never writes it at all (0 bytes
while every store lands in the prearchive), so a healthy DICOM path failed. These tests pin the
replacement: a new prearchive object carrying the sent UID, else a new ``received.log`` line, is
the success evidence; ``dicom.log`` growth is never a success route; and the FLIP#1228 importer
signatures still fail the run however much else landed.

They also pin the anonymised shape this trust actually runs: ``configure-xnat.sh`` sets
``anonymizationEnabled: true`` and ``anon_script.das`` hashes the Study/Series/SOP UIDs, so the
stored object cannot carry the UIDs read from Orthanc and the UID match never fires.

No cluster: ``decide()`` takes the evidence as a dict, ``build_evidence()`` as an env mapping.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
parse_scan = cstore_verdict.parse_scan
build_evidence = cstore_verdict.build_evidence

# One real received.log line from the k3s trust, shape preserved: timestamp, SCP thread, calling
# AE, peer, and the prearchive file the receiver wrote.
RECEIVED_LINE = (
    "2026-10-06 15:22:14,030 [dicom-scp-4] - ORTHANC@/10.42.0.15:54890:"
    "/data/xnat/prearchive/20261006_152213859/FAK86136985/SCANS/967247/DICOM/1.2.826.0.1.3680043-967247-1.dcm"
)


def evidence(**overrides: object) -> dict:
    """A healthy run: the store succeeded and the object is in the prearchive by SOP UID."""
    base: dict = {
        "store_failed_count": 0,
        "store_instance_count": 1,
        "receiver_log_lines": [],
        "dicom_log_new_lines": 0,
        "received_log_lines": [],
        "prearchive_matches": ["/data/xnat/prearchive/20261006_1/P/SCANS/1/DICOM/1.2.3-1.dcm"],
        "prearchive_match_mode": "sop",
        "prearchive_new": ["/data/xnat/prearchive/20261006_1/P/SCANS/1/DICOM/1.2.3-1.dcm"],
        "sop_uid": "1.2.3",
        "study_uid": "1.2.4",
        "timed_out": False,
        "timeout_seconds": 90,
    }
    base.update(overrides)
    return base


def anonymised_evidence(**overrides: object) -> dict:
    """A healthy run on THIS trust: anon hashed the UIDs, so only received.log identifies it."""
    return evidence(
        prearchive_matches=[],
        prearchive_match_mode="",
        prearchive_new=["/data/xnat/prearchive/20261006_152213859/FAK/SCANS/1/DICOM/2.25.8812-1.dcm"],
        received_log_lines=[RECEIVED_LINE],
        **overrides,
    )


# ── The false negative this replaces ─────────────────────────────────────────────────


def test_a_silent_dicom_log_does_not_fail_a_store_that_reached_the_prearchive() -> None:
    """The exact k3s case: 0 failed instances, 0 new dicom.log lines, object in prearchive."""
    verdict = decide(evidence(dicom_log_new_lines=0))

    assert verdict.passed, verdict.reason
    assert any("prearchive" in note for note in verdict.notes)
    assert any("error log" in note for note in verdict.notes), (
        "a pass with a silent dicom.log does not explain why its silence is not a failure"
    )


def test_dicom_log_growth_is_context_not_a_second_tick() -> None:
    """Where the logger IS written it records errors, so growth is reported, never celebrated."""
    verdict = decide(evidence(dicom_log_new_lines=12))

    assert verdict.passed
    note = next(n for n in verdict.notes if "dicom.log gained 12" in n)
    assert "errors only" in note, "dicom.log growth is presented as if it were success evidence"
    assert "✓" not in note


# ── The receiver FLIP actually configures: anonymised UIDs ───────────────────────────


def test_an_anonymised_receiver_passes_on_the_received_log_line() -> None:
    """anon_script.das hashUIDs the Study/Series/SOP UIDs, so no UID match is possible.

    Without received.log every healthy run would poll to the deadline and pass on "some object
    arrived", which a concurrent DQR/C-MOVE import satisfies just as well.
    """
    verdict = decide(anonymised_evidence())

    assert verdict.passed, verdict.reason
    assert "received.log" in verdict.reason
    assert any(RECEIVED_LINE in note for note in verdict.notes)
    assert not any("arrival time only" in note for note in verdict.notes), (
        "an anonymised run still passes on arrival time — the fallback is the normal path again"
    )


def test_the_anonymised_pass_explains_why_no_uid_matched() -> None:
    verdict = decide(anonymised_evidence())

    assert any("hashes the Study/Series/SOP UIDs" in note for note in verdict.notes)


def test_an_importer_failure_beats_a_received_log_line() -> None:
    verdict = decide(anonymised_evidence(receiver_log_lines=["java.lang.AbstractMethodError: x"]))

    assert not verdict.passed


def test_build_evidence_prefers_this_senders_received_log_lines() -> None:
    """A different sender's receipts are set aside where the AE is distinguishable.

    This narrows the evidence; it does not identify the object. An import from the *same* PACS
    arrives under the same calling AE and cannot be told apart.
    """
    other = RECEIVED_LINE.replace("ORTHANC@", "SOMEPACS@")
    built = build_evidence({"NEW_RECEIVED": f"{other}\n{RECEIVED_LINE}\n", "SENDER_AE": "ORTHANC"})

    assert built["received_log_lines"] == [RECEIVED_LINE]


def test_build_evidence_keeps_every_line_when_the_sender_ae_never_appears() -> None:
    """A wrong or renamed AE must degrade to "any new line", never to no evidence at all."""
    built = build_evidence({"NEW_RECEIVED": f"{RECEIVED_LINE}\n", "SENDER_AE": "NOTORTHANC"})

    assert built["received_log_lines"] == [RECEIVED_LINE]


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


def test_a_new_object_without_a_uid_or_receipt_passes_when_received_log_does_not_exist() -> None:
    """The genuine last resort: real evidence, but a concurrent import looks identical.

    A receiver with no ``received.log`` at all cannot produce stronger evidence, so arrival time
    is the best there is and the run still passes (exit 0) with its warning.
    """
    verdict = decide(
        evidence(
            prearchive_matches=[],
            prearchive_match_mode="",
            prearchive_new=["/data/xnat/prearchive/x.dcm"],
            received_log_exists=False,
        )
    )

    assert verdict.passed
    assert not verdict.inconclusive
    assert verdict.exit_code == 0
    note = next(n for n in verdict.notes if "arrival time only" in n)
    assert "concurrent import would look the same" in note
    assert any("not present on this receiver" in n for n in verdict.notes), (
        "the pass does not say why arrival time was allowed to be enough"
    )


# ── Arrival time beside a present, silent received.log: inconclusive, not a pass ─────


def _arrival_time_only(**overrides: object) -> dict:
    return evidence(
        prearchive_matches=[],
        prearchive_match_mode="",
        prearchive_new=["/data/xnat/prearchive/x.dcm"],
        received_log_lines=[],
        **overrides,
    )


def test_arrival_time_only_with_a_present_received_log_is_inconclusive_not_a_pass() -> None:
    """received.log exists and stayed silent through a store it records every receipt of.

    Exiting 0 there told every caller "the DICOM path works" on evidence a concurrent DQR/C-MOVE
    import satisfies just as well, with the doubt carried only by a warning line nobody reads.
    """
    verdict = decide(_arrival_time_only(received_log_exists=True))

    assert not verdict.passed
    assert verdict.inconclusive
    assert verdict.exit_code == 2
    assert "inconclusive" in verdict.reason
    assert "received.log exists" in verdict.reason


def test_the_inconclusive_verdict_still_names_the_arrival_time_weakness_and_a_remedy() -> None:
    verdict = decide(_arrival_time_only(received_log_exists=True))

    assert any("arrival time only" in n for n in verdict.notes)
    assert any("exit 2" in n for n in verdict.notes)
    assert "TROUBLESHOOTING.md §2.7" in verdict.remedy


def test_a_received_log_line_beats_the_inconclusive_path() -> None:
    """Present AND written to is the normal healthy run — exit 0, never 2."""
    verdict = decide(anonymised_evidence(received_log_exists=True))

    assert verdict.passed
    assert not verdict.inconclusive
    assert verdict.exit_code == 0


def test_a_uid_match_beats_the_inconclusive_path() -> None:
    verdict = decide(evidence(received_log_exists=True, received_log_lines=[]))

    assert verdict.passed
    assert verdict.exit_code == 0


def test_an_importer_failure_beats_the_inconclusive_path() -> None:
    """A real failure stays exit 1: 2 must never soften a FLIP#1228 regression."""
    verdict = decide(
        _arrival_time_only(received_log_exists=True, receiver_log_lines=["java.lang.AbstractMethodError: x"])
    )

    assert not verdict.passed
    assert not verdict.inconclusive
    assert verdict.exit_code == 1


def test_no_evidence_at_all_stays_a_failure_even_with_a_present_received_log() -> None:
    """Nothing arrived is a failure, not an inconclusive — exit 1."""
    verdict = decide(
        evidence(
            prearchive_matches=[],
            prearchive_match_mode="",
            prearchive_new=[],
            received_log_lines=[],
            received_log_exists=True,
            timed_out=True,
        )
    )

    assert not verdict.inconclusive
    assert verdict.exit_code == 1


def test_build_evidence_reads_the_received_log_existence_probe() -> None:
    """`wc -l` cannot tell a missing received.log from an empty one — hence a separate probe."""
    assert build_evidence({"RECEIVED_LOG_PRESENT": "1"})["received_log_exists"] is True
    assert build_evidence({"RECEIVED_LOG_PRESENT": "0"})["received_log_exists"] is False
    assert build_evidence({})["received_log_exists"] is False


def test_dicom_log_growth_alone_does_not_pass() -> None:
    """dicom.log records errors, not receipts — its growth can never be success evidence.

    Treating it as success passed any importer error outside the three named signatures as
    "the receiver logged the transfer".
    """
    verdict = decide(
        evidence(
            prearchive_matches=[],
            prearchive_match_mode="",
            prearchive_new=[],
            received_log_lines=[],
            dicom_log_new_lines=4,
        )
    )

    assert not verdict.passed, "dicom.log growth alone still passes — but that logger is errors-only"
    assert "TROUBLESHOOTING.md" in verdict.remedy


def test_no_receiver_side_evidence_at_all_fails() -> None:
    """A green association with nothing on the receiver is the false confidence to catch."""
    verdict = decide(
        evidence(
            prearchive_matches=[],
            prearchive_match_mode="",
            prearchive_new=[],
            received_log_lines=[],
            dicom_log_new_lines=0,
        )
    )

    assert not verdict.passed
    assert "no new prearchive object" in verdict.reason
    assert "TROUBLESHOOTING.md" in verdict.remedy


def test_a_timed_out_poll_says_so_and_names_the_window() -> None:
    """An operator who waited 90s for nothing should be told that is what happened."""
    verdict = decide(
        evidence(
            prearchive_matches=[],
            prearchive_match_mode="",
            prearchive_new=[],
            received_log_lines=[],
            timed_out=True,
            timeout_seconds=90,
        )
    )

    assert not verdict.passed
    assert "within 90s" in verdict.reason
    assert "TROUBLESHOOTING.md" in verdict.remedy


def test_a_study_uid_match_names_the_study_uid() -> None:
    verdict = decide(evidence(prearchive_match_mode="study"))

    assert verdict.passed
    assert any("StudyInstanceUID 1.2.4" in note for note in verdict.notes)


# ── Malformed evidence produces a verdict, not a traceback ───────────────────────────


def test_evidence_that_is_not_an_object_fails_cleanly() -> None:
    verdict = decide([])  # type: ignore[arg-type]

    assert not verdict.passed
    assert "not an object" in verdict.reason


def test_an_unparsable_dicom_log_count_does_not_crash() -> None:
    verdict = decide(evidence(dicom_log_new_lines="abc"))

    assert verdict.passed


def test_an_empty_evidence_object_fails_on_the_missing_store_report() -> None:
    verdict = decide({})

    assert not verdict.passed
    assert "FailedInstancesCount" in verdict.reason


# ── The scan parser: SOP beats Study beats arrival time ──────────────────────────────


def test_parse_scan_prefers_a_sop_match_over_a_study_match_listed_first() -> None:
    """`find` lists in directory order, so the stronger match is not necessarily first."""
    result = parse_scan("study\t/pa/A.dcm\nsop\t/pa/B.dcm\n")

    assert result.mode == "sop"
    assert result.matches == ["/pa/B.dcm"]
    assert result.new_files == ["/pa/A.dcm", "/pa/B.dcm"]


def test_parse_scan_keeps_every_match_at_the_winning_mode() -> None:
    result = parse_scan("sop\t/pa/A.dcm\nsop\t/pa/B.dcm\nstudy\t/pa/C.dcm\n")

    assert result.mode == "sop"
    assert result.matches == ["/pa/A.dcm", "/pa/B.dcm"]


def test_parse_scan_reports_time_only_files_as_new_but_not_as_matches() -> None:
    result = parse_scan("time\t/pa/A.dcm\ntime\t/pa/B.dcm\n")

    assert result.mode == ""
    assert result.matches == []
    assert result.new_files == ["/pa/A.dcm", "/pa/B.dcm"]


@pytest.mark.parametrize("junk", ["", "no tab here\n", "weird\t\n", "\t/pa/A.dcm\n"])
def test_parse_scan_ignores_lines_it_cannot_read(junk: str) -> None:
    result = parse_scan(junk)

    assert result.new_files == []
    assert result.matches == []


def test_build_evidence_applies_the_same_precedence() -> None:
    built = build_evidence({"SCAN": "study\t/pa/A.dcm\nsop\t/pa/B.dcm\n", "FAILED_COUNT": "0"})

    assert built["prearchive_match_mode"] == "sop"
    assert built["prearchive_matches"] == ["/pa/B.dcm"]
    assert built["prearchive_new"] == ["/pa/A.dcm", "/pa/B.dcm"]


def test_build_evidence_reads_the_store_counts_and_the_timeout_flag() -> None:
    built = build_evidence({"FAILED_COUNT": "0", "INSTANCE_COUNT": "3", "TIMED_OUT": "1", "PREARCHIVE_TIMEOUT": "90"})

    assert built["store_failed_count"] == 0
    assert built["store_instance_count"] == 3
    assert built["timed_out"] is True
    assert built["timeout_seconds"] == 90


def test_build_evidence_reports_an_unparsable_store_count_as_unknown() -> None:
    built = build_evidence({"FAILED_COUNT": "", "PREARCHIVE_TIMEOUT": "not-a-number"})

    assert built["store_failed_count"] is None
    assert built["timeout_seconds"] == 0
    assert not decide(built).passed


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


def test_the_cli_reads_the_environment_the_shell_hands_it() -> None:
    """`--from-env` is the whole handoff — the shell does no parsing of its own."""
    result = subprocess.run(
        [sys.executable, str(VERDICT_PY), "--from-env"],
        env={
            "PATH": "/usr/bin:/bin",
            "FAILED_COUNT": "0",
            "INSTANCE_COUNT": "1",
            "NEW_RECEIVED": RECEIVED_LINE,
            "SENDER_AE": "ORTHANC",
            "PREARCHIVE_TIMEOUT": "90",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "received.log" in result.stdout


def test_the_cli_from_env_fails_a_run_with_no_receiver_evidence() -> None:
    result = subprocess.run(
        [sys.executable, str(VERDICT_PY), "--from-env"],
        env={"PATH": "/usr/bin:/bin", "FAILED_COUNT": "0", "TIMED_OUT": "1", "PREARCHIVE_TIMEOUT": "90"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "within 90s" in result.stderr


def test_the_cli_exits_two_on_the_inconclusive_case() -> None:
    """2 is the whole point: a caller reading `$?` must not see "passed"."""
    result = _run_cli(
        evidence(
            prearchive_matches=[],
            prearchive_match_mode="",
            prearchive_new=["/data/xnat/prearchive/x.dcm"],
            received_log_lines=[],
            received_log_exists=True,
        )
    )

    assert result.returncode == 2, result.stderr
    assert "INCONCLUSIVE" in result.stderr
    assert "C-STORE smoke passed" not in result.stdout


def test_the_cli_from_env_exits_two_when_the_probe_says_received_log_exists() -> None:
    """End to end over the handoff the shell actually uses."""
    result = subprocess.run(
        [sys.executable, str(VERDICT_PY), "--from-env"],
        env={
            "PATH": "/usr/bin:/bin",
            "FAILED_COUNT": "0",
            "INSTANCE_COUNT": "1",
            "SCAN": "time\t/data/xnat/prearchive/x.dcm\n",
            "RECEIVED_LOG_PRESENT": "1",
            "PREARCHIVE_TIMEOUT": "90",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2, result.stderr
    assert "INCONCLUSIVE" in result.stderr


def test_the_cli_from_env_still_exits_zero_when_received_log_is_absent() -> None:
    result = subprocess.run(
        [sys.executable, str(VERDICT_PY), "--from-env"],
        env={
            "PATH": "/usr/bin:/bin",
            "FAILED_COUNT": "0",
            "INSTANCE_COUNT": "1",
            "SCAN": "time\t/data/xnat/prearchive/x.dcm\n",
            "RECEIVED_LOG_PRESENT": "0",
            "PREARCHIVE_TIMEOUT": "90",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_the_smoke_probes_received_logs_existence_at_the_mark() -> None:
    """A line count cannot tell a missing received.log from an empty one.

    The whole exit-2 rule turns on that difference, so the mark must carry an explicit probe and
    hand it to the verdict.
    """
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    assert "RECEIVED_LOG_PRESENT" in script, "the smoke never probes whether received.log exists"
    assert '[ -f "$R" ]' in script, "existence is still inferred from wc, which reports 0 for missing and empty alike"
    assert 'RECEIVED_LOG_PRESENT="$RECEIVED_LOG_PRESENT"' in script, (
        "the probe is taken but never handed to cstore_verdict.py"
    )


def test_the_smoke_header_documents_the_three_exit_codes() -> None:
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    assert "2 = inconclusive" in script


def _smoke_recipe() -> str:
    makefile = (CHART_DIR / "Makefile").read_text()
    return makefile[makefile.index("smoke-cstore:") :].split("\n\n")[0]


@pytest.mark.parametrize(("script_exit", "make_should_fail"), [(0, False), (1, True), (2, False)])
def test_make_smoke_cstore_maps_exit_2_to_a_warning_and_passes_0_and_1_through(
    script_exit: int, make_should_fail: bool
) -> None:
    """The shipped recipe, run against a stub script, for each status it can receive.

    2 must not fail Make — an inconclusive run is not a reason to break a deploy — but it must
    not be silent either, and 1 must still fail. (Make reports a failed recipe with its own
    status 2, so a real failure is asserted as "non-zero", not as a specific code.)
    """
    make = shutil.which("make")
    if make is None:
        pytest.skip("make not installed")  # pragma: no cover - make is present in CI and on dev boxes
        raise AssertionError  # pragma: no cover - unreachable; narrows `make` for type checkers

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "scripts").mkdir()
        stub = root / "scripts" / "smoke-cstore.sh"
        stub.write_text(f"#!/bin/sh\nexit {script_exit}\n")
        stub.chmod(0o755)
        mk = root / "Makefile"
        mk.write_text(
            f"CHART_DIR := {root}\nNAMESPACE := ns\nRELEASE_NAME := rel\nKUBE_CONTEXT :=\n\n{_smoke_recipe()}\n"
        )

        result = subprocess.run(  # noqa: S603
            [make, "-f", str(mk), "smoke-cstore"],
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp,
        )

    assert (result.returncode != 0) is make_should_fail, result.stderr
    if script_exit == 2:
        assert "INCONCLUSIVE" in result.stderr, "an inconclusive run is swallowed silently"


# ── The shell script that gathers the evidence ───────────────────────────────────────


def test_the_smoke_script_checks_the_prearchive_and_no_longer_requires_log_growth() -> None:
    """The gatherer must look where the objects actually land, and defer the verdict."""
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    assert "prearchive" in script, "the smoke never looks at the prearchive, the only positive evidence"
    assert "cstore_verdict.py" in script, "the smoke does not hand its evidence to the tested decision function"
    assert "dicom.log gained no lines" not in script, (
        "the smoke still fails on a silent dicom.log — the false negative this fixes"
    )


def test_the_smoke_reads_received_log_from_a_line_count_mark() -> None:
    """The UID match cannot fire on an anonymising receiver; received.log is the evidence."""
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    assert "received.log" in script, "the smoke never reads the log XNAT writes successful receipts to"
    assert "RECEIVED_MARK" in script, "received.log is read whole, so a trust's months of history would pass every run"


def test_the_smoke_rereads_received_log_from_line_1_when_it_rotated() -> None:
    """A rotation between the mark and the read leaves `tail -n +N` reading nothing — a false fail.

    The snippet is lifted out of the script itself and run, so this covers the shipped shell.
    """
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()
    assert "RECEIVED_BYTES" in script, "the mark records no size, so a rotation cannot be detected"
    body = script.split('env R="${RECEIVED_LOG}" M="$RECEIVED_MARK" B="$RECEIVED_BYTES" sh -c \'')[1]
    body = body.split("'")[0]

    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "received.log"

        def run(mark: int, size: int) -> str:
            return subprocess.run(  # noqa: S603
                ["sh", "-c", body],
                env={**os.environ, "R": str(log), "M": str(mark), "B": str(size)},
                capture_output=True,
                text=True,
                check=True,
            ).stdout

        # Not rotated: only the lines after the mark come back.
        log.write_text("old-1\nold-2\nnew-1\n")
        assert run(2, 12) == "new-1\n"

        # Rotated: the file is smaller than the mark's size, so read it whole.
        log.write_text("fresh-1\n")
        assert run(2, 12) == "fresh-1\n"


def test_the_smoke_builds_no_evidence_of_its_own() -> None:
    """Precedence and parsing live in cstore_verdict.py, where tests cover them."""
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    assert "--from-env" in script, "the smoke does not use the tested evidence builder"
    assert "import json" not in script, "the smoke still carries inline Python that no test covers"


def test_the_container_log_window_starts_at_the_store_not_at_the_read() -> None:
    """An elapsed --since is measured when the log is READ — after a full poll, so it misses
    the first seconds, which is exactly where an importer AbstractMethodError is logged."""
    script = (CHART_DIR / "scripts" / "smoke-cstore.sh").read_text()

    assert "--since-time=" in script, "the container log window is relative to the read, not to the store"
    assert "RUN_START_RFC3339" in script


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


def test_the_receiver_really_is_configured_to_hash_the_uids() -> None:
    """The premise of the received.log evidence, asserted against the config that creates it.

    If anonymisation is ever turned off, or the hashUID lines leave anon_script.das, the UID
    match becomes the normal path again and this file's priorities should be revisited.
    """
    repo_root = CHART_DIR.parent.parent.parent
    configure = (repo_root / "trust" / "xnat" / "xnat" / "config" / "configure-xnat.sh").read_text()
    anon = (repo_root / "trust" / "xnat" / "xnat" / "config" / "anon_script.das").read_text()

    assert "anonymizationEnabled: true" in configure
    for tag in ("(0020,000D)", "(0020,000E)", "(0008,0018)"):
        assert f"{tag} := hashUID[{tag}]" in anon, f"{tag} is no longer hashed on receive"
