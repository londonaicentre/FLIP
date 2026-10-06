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
The real positive evidence is the one the importer cannot fake: a new DICOM object in XNAT's
prearchive carrying the SOP/Study Instance UID that was just sent. ``dicom.log`` growth remains
an *extra* positive signal where the logger is active, never a requirement.

The regression this smoke exists for (FLIP#1228 — a plugin built against a different XNAT core)
is unchanged: any of ``AbstractMethodError`` / ``NoSuchMethodError`` / ``unable to read DICOM
object null`` in the receiver's logs fails the run outright, whatever else landed.

``decide()`` takes a plain dict of evidence and returns a verdict, so the decision is testable
with no cluster. The shell script owns collection and printing only.
"""

from __future__ import annotations

import json
import sys
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


def decide(evidence: dict) -> Verdict:
    """Judge one C-STORE smoke run.

    Expected keys (all optional; missing is treated as "not observed"):

    ``store_failed_count``   int | None — Orthanc's FailedInstancesCount (None = unparsable).
    ``store_instance_count`` int | None — Orthanc's InstancesCount.
    ``receiver_log_lines``   list[str] — lines written during the run (dicom.log tail + pod log).
    ``dicom_log_new_lines``  int — how many lines dicom.log gained.
    ``prearchive_matches``   list[str] — new prearchive files matching the sent UID.
    ``prearchive_match_mode``str — "sop" | "study" | "" (which UID the match was on).
    ``prearchive_new``       list[str] — new prearchive files, UID-matched or not.
    ``sop_uid`` / ``study_uid`` str — the UIDs that were sent, for the message.
    """
    store_failed = evidence.get("store_failed_count")
    store_instances = evidence.get("store_instance_count")
    receiver_lines = list(evidence.get("receiver_log_lines") or [])
    dicom_log_new = int(evidence.get("dicom_log_new_lines") or 0)
    matches = list(evidence.get("prearchive_matches") or [])
    match_mode = evidence.get("prearchive_match_mode") or ""
    new_files = list(evidence.get("prearchive_new") or [])
    sop_uid = evidence.get("sop_uid") or ""
    study_uid = evidence.get("study_uid") or ""

    # 1. The sender's own verdict. An unparsable report is a failure: it is indistinguishable
    #    from a store that never happened, and passing on it would restore the false confidence
    #    this smoke exists to remove.
    if store_failed is None:
        return Verdict(
            False,
            "Orthanc's store report had no FailedInstancesCount — cannot tell whether the "
            "transfer succeeded",
        )
    if store_failed != 0:
        return Verdict(
            False,
            f"Orthanc reports {store_failed} failed instance(s) — the receiver rejected or "
            "aborted the transfer",
        )

    # 2. The regression signature always wins, even over a prearchive file: a partially-imported
    #    object beside an AbstractMethodError is still the FLIP#1228 breakage.
    hits = matching_failure_lines(receiver_lines)
    if hits:
        return Verdict(False, "the receiver logged an importer failure", notes=hits[:10], remedy=_PLUGIN_REMEDY)

    counted = store_instances if store_instances is not None else "?"
    stored = f"✓ store reported success ({counted} instance(s), 0 failed)"

    # 3. Positive receiver-side evidence, strongest first.
    if matches:
        uid = sop_uid if match_mode == "sop" else study_uid
        notes = [
            stored,
            f"✓ prearchive gained {len(matches)} object(s) matching the sent "
            f"{'SOPInstanceUID' if match_mode == 'sop' else 'StudyInstanceUID'} {uid}",
            *matches[:5],
        ]
        if dicom_log_new > 0:
            notes.append(f"✓ dicom.log also gained {dicom_log_new} line(s) with no importer failure")
        else:
            notes.append(
                "ℹ dicom.log gained no lines — this deployment does not write that logger; "
                "the prearchive object above is the evidence"
            )
        return Verdict(True, "the object reached XNAT's prearchive with no importer failure", notes=notes)

    if new_files:
        # Weaker: something landed in the window but carried neither UID (XNAT can rewrite UIDs
        # on receive, e.g. under an anonymisation script). Pass, and say the match was by time.
        return Verdict(
            True,
            "a new prearchive object appeared during the run with no importer failure",
            notes=[
                stored,
                f"⚠ matched by arrival time only — none of the {len(new_files)} new object(s) "
                f"carried the sent UID ({sop_uid or study_uid or 'unknown'}); XNAT may be "
                "rewriting UIDs on receive",
                *new_files[:5],
            ],
        )

    if dicom_log_new > 0:
        # No prearchive object, but the importer logged the association cleanly — e.g. routed
        # straight into the archive, or a prearchive path this check did not scan.
        return Verdict(
            True,
            "the receiver logged the transfer with no importer failure",
            notes=[
                stored,
                f"⚠ no new prearchive object found, but dicom.log gained {dicom_log_new} "
                "line(s) and none carried an importer failure",
            ],
        )

    return Verdict(
        False,
        "no receiver-side evidence of the transfer: no new prearchive object and no receiver "
        "log activity",
        notes=[stored],
        remedy=_NO_EVIDENCE_REMEDY,
    )


def main() -> int:
    """Read the evidence JSON on stdin, print the verdict, exit 0 (pass) or 1 (fail)."""
    try:
        evidence = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as exc:  # pragma: no cover - defensive
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
