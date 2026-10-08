#!/usr/bin/env python3
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

"""Decide whether a C-STORE smoke run passed, from the evidence the shell script gathered.

WHY THIS IS A SEPARATE, TESTABLE UNIT

The original check equated "the DICOM path works" with "``dicom.log`` gained lines". That logger
is not written by every XNAT deployment — on the k3s trust the file stays 0 bytes while stores
land in the prearchive exactly as they should — so a perfectly healthy ingest reported failure.
It is also the WRONG logger: XNAT records a successful receipt in ``received.log`` and writes
``dicom.log`` for errors only, so its growth is never evidence of success and is not treated as
such here.

WHAT COUNTS AS POSITIVE EVIDENCE, STRONGEST FIRST

1. A new prearchive object carrying the SOP/Study Instance UID that was sent. Specific to this
   transfer and unforgeable — but only available where the receiver stores the UIDs it was given.
2. A new ``received.log`` line since the mark taken before the store. This is the evidence that
   survives FLIP's own receiver configuration: ``configure-xnat.sh`` sets
   ``anonymizationEnabled: true`` and ``anon_script.das`` rewrites the Study, Series and SOP UIDs
   through ``hashUID``, so the stored object does NOT carry the UIDs read from Orthanc and (1)
   cannot match. ``received.log`` names the calling AE and the file it wrote, both after anon.
3. A new prearchive object by arrival time alone. A genuine last resort, reported as such: a
   concurrent DQR or C-MOVE import lands objects in the same window.

The regression this smoke exists for (FLIP#1228 — a plugin built against a different XNAT core)
is unchanged: any of ``AbstractMethodError`` / ``NoSuchMethodError`` / ``unable to read DICOM
object null`` in the receiver's logs fails the run outright, whatever else landed.

``decide()`` takes a plain dict of evidence and returns a verdict, and ``build_evidence()`` turns
the shell script's raw strings into that dict, so both the parsing and the decision are testable
with no cluster. The shell script owns collection and printing only.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field

# Importer failures, as they appear in dicom.log and in the xnat-web container log. The first
# two are the version-mismatch crash itself; the third is how the same association looks from
# the receiving end once the importer has already thrown.
FAILURE_PATTERNS = ("AbstractMethodError", "NoSuchMethodError", "unable to read DICOM object null")


@dataclass
class Verdict:
    """The outcome of one smoke run."""

    passed: bool
    reason: str
    # Positive evidence worth printing on a pass, or context worth printing on a failure.
    notes: list[str] = field(default_factory=list)
    # Operator-facing next step; empty on a pass.
    remedy: str = ""


_PLUGIN_REMEDY = (
    "the receiver logged an importer failure. An AbstractMethodError here means an XNAT plugin "
    "was built against a different core than the one running — compare the pod's "
    "/data/xnat/home/plugins against xnat.web.plugins.urls (make status), then redeploy with a "
    "HELM_TIMEOUT above the init job's real duration. See TROUBLESHOOTING.md §2.7."
)

_NO_EVIDENCE_REMEDY = (
    "Orthanc reported the association succeeded but XNAT shows no received object: no new "
    "prearchive file and no receiver log activity. Check that the SCP receiver Orthanc dialled "
    "is this XNAT (make status, TROUBLESHOOTING.md §2.1) and that the prearchive path is the "
    "one this check scanned."
)


def matching_failure_lines(lines: list[str]) -> list[str]:
    """Return the log lines carrying a known importer-failure signature."""
    return [line for line in lines if any(pat in line for pat in FAILURE_PATTERNS)]


def _as_int(value: object) -> int:
    """Best-effort int, 0 for anything unparsable.

    The shell hands these across as environment strings, and a field it failed to parse must
    produce a verdict, not a traceback — an operator reading ``ValueError: invalid literal``
    learns nothing about their DICOM path.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


# How the prearchive scan reports each new file, strongest match first. The shell emits one
# "mode<TAB>path" line per file; precedence lives here rather than in the shell so it is tested.
_SCAN_MODE_RANK = {"sop": 0, "study": 1, "time": 2}


@dataclass
class ScanResult:
    """What one prearchive scan found."""

    # New files carrying the sent UID, all at the SAME (strongest available) match mode.
    matches: list[str] = field(default_factory=list)
    # "sop" | "study" | "" — which UID `matches` matched on.
    mode: str = ""
    # Every new file in the window, UID-matched or not, in the order the scan listed them.
    new_files: list[str] = field(default_factory=list)


def parse_scan(scan: str) -> ScanResult:
    """Parse the shell's "mode<TAB>path" scan output, SOP beating Study beating time.

    Taking ``matches[0]``'s mode instead would let whichever file ``find`` happened to list
    first decide: a study-level match listed ahead of a SOP-level one would win, and the
    weaker evidence would be reported (and the poll stopped) over the stronger.
    """
    result = ScanResult()
    best = len(_SCAN_MODE_RANK)
    graded: list[tuple[int, str]] = []
    for line in (scan or "").splitlines():
        if "\t" not in line:
            continue
        mode, path = line.split("\t", 1)
        mode, path = mode.strip(), path.strip()
        rank = _SCAN_MODE_RANK.get(mode)
        if rank is None or not path:
            continue
        result.new_files.append(path)
        if mode != "time":
            graded.append((rank, path))
            best = min(best, rank)
    result.matches = [path for rank, path in graded if rank == best]
    if result.matches:
        result.mode = "sop" if best == _SCAN_MODE_RANK["sop"] else "study"
    return result


def _nonblank(text: str) -> list[str]:
    return [line for line in (text or "").splitlines() if line.strip()]


def build_evidence(env: Mapping[str, str]) -> dict:
    """Turn the shell script's raw environment strings into the dict ``decide()`` expects.

    Lives here, beside the decision it feeds, so the shell holds no logic that no test covers.
    """
    scan = parse_scan(env.get("SCAN", ""))
    dicom_log_lines = _nonblank(env.get("NEW_LOG", ""))
    pod_lines = _nonblank(env.get("POD_LOG", ""))
    received_lines = _nonblank(env.get("NEW_RECEIVED", ""))
    sender_ae = (env.get("SENDER_AE") or "").strip()
    # Prefer the lines this sender wrote where the AE is known and present: on a busy trust a
    # DQR/C-MOVE import writes received.log too, and those lines are not this transfer.
    if sender_ae:
        mine = [line for line in received_lines if f"{sender_ae}@" in line]
        if mine:
            received_lines = mine

    def num(name: str) -> int | None:
        raw = (env.get(name) or "").strip()
        return int(raw) if raw.isdigit() else None

    return {
        "store_failed_count": num("FAILED_COUNT"),
        "store_instance_count": num("INSTANCE_COUNT"),
        "receiver_log_lines": dicom_log_lines + pod_lines,
        "dicom_log_new_lines": len(dicom_log_lines),
        "received_log_lines": received_lines,
        "prearchive_matches": scan.matches,
        "prearchive_match_mode": scan.mode,
        "prearchive_new": scan.new_files,
        "sop_uid": env.get("SOP_UID", ""),
        "study_uid": env.get("STUDY_UID", ""),
        "timed_out": (env.get("TIMED_OUT") or "").strip() == "1",
        "timeout_seconds": _as_int(env.get("PREARCHIVE_TIMEOUT")),
    }


def decide(evidence: dict) -> Verdict:
    """Judge one C-STORE smoke run.

    Expected keys (all optional; missing is treated as "not observed"):

    ``store_failed_count``   int | None — Orthanc's FailedInstancesCount (None = unparsable).
    ``store_instance_count`` int | None — Orthanc's InstancesCount.
    ``receiver_log_lines``   list[str] — lines written during the run (dicom.log tail + pod log).
    ``dicom_log_new_lines``  int — how many lines dicom.log gained (errors-only logger: context,
                             never success evidence).
    ``received_log_lines``   list[str] — received.log lines written since the pre-store mark.
    ``prearchive_matches``   list[str] — new prearchive files matching the sent UID.
    ``prearchive_match_mode``str — "sop" | "study" | "" (which UID the match was on).
    ``prearchive_new``       list[str] — new prearchive files, UID-matched or not.
    ``sop_uid`` / ``study_uid`` str — the UIDs that were sent, for the message.
    ``timed_out``            bool — the poll expired without conclusive evidence.
    ``timeout_seconds``      int — the poll's time box, for the message.
    """
    if not isinstance(evidence, Mapping):
        # A list, a string or a bare number reaching here is a bug in the caller, not a DICOM
        # fault — say so rather than dying on .get() with a traceback.
        return Verdict(False, f"the smoke evidence was {type(evidence).__name__}, not an object")

    store_failed = evidence.get("store_failed_count")
    store_instances = evidence.get("store_instance_count")
    receiver_lines = list(evidence.get("receiver_log_lines") or [])
    dicom_log_new = _as_int(evidence.get("dicom_log_new_lines"))
    received_lines = list(evidence.get("received_log_lines") or [])
    matches = list(evidence.get("prearchive_matches") or [])
    match_mode = evidence.get("prearchive_match_mode") or ""
    new_files = list(evidence.get("prearchive_new") or [])
    sop_uid = evidence.get("sop_uid") or ""
    study_uid = evidence.get("study_uid") or ""
    timed_out = bool(evidence.get("timed_out"))
    timeout_seconds = _as_int(evidence.get("timeout_seconds"))

    # 1. The sender's own verdict. An unparsable report is a failure: it is indistinguishable
    #    from a store that never happened, and passing on it would restore the false confidence
    #    this smoke exists to remove.
    if store_failed is None:
        return Verdict(
            False,
            "Orthanc's store report had no FailedInstancesCount — cannot tell whether the transfer succeeded",
        )
    if store_failed != 0:
        return Verdict(
            False,
            f"Orthanc reports {store_failed} failed instance(s) — the receiver rejected or aborted the transfer",
        )

    # 2. The regression signature always wins, even over a prearchive file: a partially-imported
    #    object beside an AbstractMethodError is still the FLIP#1228 breakage.
    hits = matching_failure_lines(receiver_lines)
    if hits:
        return Verdict(False, "the receiver logged an importer failure", notes=hits[:10], remedy=_PLUGIN_REMEDY)

    counted = store_instances if store_instances is not None else "?"
    stored = f"✓ store reported success ({counted} instance(s), 0 failed)"

    def dicom_log_note() -> str:
        """dicom.log is the receiver's ERROR log. Report it, never count it as success."""
        if dicom_log_new > 0:
            return (
                f"ℹ dicom.log gained {dicom_log_new} line(s) carrying none of the known importer "
                "failures — that logger records errors only, so this is context, not evidence"
            )
        return "ℹ dicom.log gained no lines, as expected on a clean import (it is an error log)"

    def received_note() -> list[str]:
        if not received_lines:
            return []
        return [
            f"✓ received.log gained {len(received_lines)} line(s) since the pre-store mark",
            *received_lines[:5],
        ]

    # 3. Positive receiver-side evidence, strongest first.
    if matches:
        uid = sop_uid if match_mode == "sop" else study_uid
        notes = [
            stored,
            f"✓ prearchive gained {len(matches)} object(s) matching the sent "
            f"{'SOPInstanceUID' if match_mode == 'sop' else 'StudyInstanceUID'} {uid}",
            *matches[:5],
            *received_note(),
            dicom_log_note(),
        ]
        return Verdict(True, "the object reached XNAT's prearchive with no importer failure", notes=notes)

    if received_lines:
        # The normal path on a FLIP-configured receiver: anonymizationEnabled rewrites the
        # Study/Series/SOP UIDs through hashUID, so the stored object cannot carry the UIDs read
        # from Orthanc and (3) above never matches. received.log is written after that rewrite.
        notes = [
            stored,
            *received_note(),
            f"ℹ no prearchive object carried the sent UID ({sop_uid or study_uid or 'unknown'}) — "
            "expected on this receiver, whose anonymisation script hashes the Study/Series/SOP UIDs",
            dicom_log_note(),
        ]
        if new_files:
            notes.append(f"✓ prearchive also gained {len(new_files)} new object(s)")
            notes.extend(new_files[:5])
        return Verdict(True, "XNAT logged the receipt in received.log with no importer failure", notes=notes)

    if new_files:
        # Weakest real evidence: something landed in the window but the receipt was never logged
        # and no UID matched. A concurrent DQR/C-MOVE import lands objects in the same window, so
        # say plainly that this does not identify the object as the one that was sent.
        return Verdict(
            True,
            "a new prearchive object appeared during the run with no importer failure",
            notes=[
                stored,
                f"⚠ matched by arrival time only — none of the {len(new_files)} new object(s) "
                f"carried the sent UID ({sop_uid or study_uid or 'unknown'}) and received.log "
                "gained no line, so a concurrent import would look the same",
                *new_files[:5],
                dicom_log_note(),
            ],
        )

    if timed_out:
        return Verdict(
            False,
            f"the receiver showed no sign of the transfer within {timeout_seconds}s: "
            "no received.log line and no new prearchive object",
            notes=[stored, dicom_log_note()],
            remedy=_NO_EVIDENCE_REMEDY,
        )

    return Verdict(
        False,
        "no receiver-side evidence of the transfer: no received.log line and no new prearchive object",
        notes=[stored, dicom_log_note()],
        remedy=_NO_EVIDENCE_REMEDY,
    )


def main() -> int:
    """Print the verdict for one run; exit 0 (pass) or 1 (fail).

    Evidence comes from the environment with ``--from-env`` (how the shell script calls it, so
    no untested Python lives there) or as JSON on stdin.
    """
    if "--from-env" in sys.argv[1:]:
        evidence = build_evidence(os.environ)
    else:
        try:
            evidence = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"✗ could not parse the smoke evidence: {exc}", file=sys.stderr)
            return 1
    verdict = decide(evidence)
    red, green, reset = "\033[0;31m", "\033[0;32m", "\033[0m"
    for note in verdict.notes:
        print(note)
    if verdict.passed:
        print(f"{green}✅ C-STORE smoke passed — {verdict.reason}{reset}")
        return 0
    print(f"{red}✗ {verdict.reason}{reset}", file=sys.stderr)
    if verdict.remedy:
        print(f"{red}{verdict.remedy}{reset}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
