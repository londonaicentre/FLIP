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

"""The machine-readable half of a classification result, for a deepcOS engine report.

A classifier already produces exactly what a deepcOS *finding* carries — a per-label probability and
a present/absent decision against a threshold — so this is a formatting step, not a change to how
inference works. Two things make it a real one rather than a rename:

* **Every label is reported, with an explicit present/absent state**, not only those above
  threshold. That is what lets a downstream system accept or reject each finding individually,
  generate free-text, and run performance analytics.
* **Each label is assigned a coded clinical concept.** FLIP uses SNOMED CT terms here; where a
  label has no standard term, the vendor documents an escape hatch for a locally-defined concept,
  so a missing term is not a blocker — the label is still reported, under a ``LOCAL`` scheme.

This module is deliberately free of the MONAI Deploy SDK so it can be unit-tested on its own; the
classifier operator is the adapter that feeds it probabilities and writes the result out.

**This report shape is a scaffold, not the vendor schema.** deepc's engine-report schema is
access-controlled and is not reproduced here; the field names below match only what the FLIP
packaging guide records as *required* — a job status, coded messages, and an engine result
following the DICOM hierarchy, with the regulatory ``allNegative`` field left null. Anyone
integrating against deepcOS must validate against the vendor's own tooling.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The report's default filename. deepcOS discovers the report by the case-insensitive substring
# ``deepcreport`` anywhere in the filename, so this qualifies.
DEFAULT_REPORT_FILENAME = "chest-xray-deepcReport.json"


@dataclass(frozen=True)
class Coding:
    """A coded clinical concept.

    Attributes:
        scheme (str): Coding scheme, e.g. ``"SCT"`` for SNOMED CT or ``"LOCAL"`` for a
            locally-defined concept.
        code (str): The concept's code within the scheme.
        meaning (str): Human-readable term for the concept.
    """

    scheme: str
    code: str
    meaning: str

    def as_dict(self) -> dict[str, str]:
        """Return the coding as a plain, JSON-serialisable mapping.

        Returns:
            dict[str, str]: The scheme, code and meaning.
        """
        return {"scheme": self.scheme, "code": self.code, "meaning": self.meaning}


# SNOMED CT concepts for the FLIP xray tutorial's labels. The two NIH ChestX-ray labels this model
# emits map to their SNOMED CT clinical findings. Replace these for your own model's labels; a code
# here must be a real SNOMED CT concept.
LABEL_CODINGS: dict[str, Coding] = {
    "Effusion": Coding("SCT", "60046008", "Pleural effusion"),
    "Edema": Coding("SCT", "19242006", "Pulmonary edema"),
}


def coding_for_label(label: str) -> Coding:
    """Return the coded concept for a label, falling back to a local concept when unmapped.

    Args:
        label (str): The model's label name.

    Returns:
        Coding: The SNOMED CT concept for a known label, else a ``LOCAL``-scheme concept carrying
        the label name so an unmapped label is still reported rather than dropped.
    """
    if label in LABEL_CODINGS:
        return LABEL_CODINGS[label]
    return Coding("LOCAL", label, label)


def build_findings(probabilities: dict[str, float], threshold: float) -> list[dict[str, Any]]:
    """Build a finding for every label, with its coded concept, confidence and present/absent state.

    Args:
        probabilities (dict[str, float]): Per-label probability from the classifier.
        threshold (float): Reporting threshold; a probability at or above it is ``present``.

    Returns:
        list[dict[str, Any]]: One finding per label, in the input's iteration order.
    """
    findings = []
    for label, probability in probabilities.items():
        findings.append(
            {
                "concept": coding_for_label(label).as_dict(),
                "confidence": float(probability),
                "present": float(probability) >= threshold,
            }
        )
    return findings


def coded_message(code: str, message: str) -> dict[str, str]:
    """Build a coded message entry.

    Args:
        code (str): A short stable code identifying the condition.
        message (str): Human-readable description of the condition.

    Returns:
        dict[str, str]: The coded message.
    """
    return {"code": code, "message": message}


def build_engine_report(
    *,
    findings: list[dict[str, Any]] | None = None,
    messages: list[dict[str, str]] | None = None,
    status: str | None = None,
    study_uid: str | None = None,
    series_uid: str | None = None,
) -> dict[str, Any]:
    """Assemble the engine report from a classification run's findings.

    A classifier that ran and found nothing above threshold has a valid negative result, so an
    all-absent finding set is still ``SUCCESS`` — unlike an empty segmentation, absence here is the
    analysis, not a missing output. Status is still derived rather than hardcoded so an explicit
    failure can be surfaced by the caller.

    Args:
        findings (list[dict[str, Any]] | None): Findings, e.g. from :func:`build_findings`.
        messages (list[dict[str, str]] | None): Coded messages to include.
        status (str | None): Explicit job status; defaults to ``SUCCESS`` when omitted.
        study_uid (str | None): StudyInstanceUID of the analysed study, for the result hierarchy.
        series_uid (str | None): SeriesInstanceUID of the analysed series, for the result hierarchy.

    Returns:
        dict[str, Any]: The engine report, carrying the required ``jobStatus``, ``codedMessages``
        and ``engineResult`` fields.
    """
    findings = findings or []
    derived_messages = list(messages or [])
    if status is None:
        status = "SUCCESS"

    series_entry: dict[str, Any] = {"findings": findings, "quantities": []}
    if series_uid is not None:
        series_entry["seriesInstanceUID"] = series_uid
    study_entry: dict[str, Any] = {"series": [series_entry]}
    if study_uid is not None:
        study_entry["studyInstanceUID"] = study_uid

    return {
        "jobStatus": {"status": status},
        "codedMessages": derived_messages,
        "engineResult": {
            # Reserved by the vendor for regulatory-cleared engines. A FLIP research model is
            # neither CE-marked nor FDA-cleared, so it is left null — reporting a finding's
            # present/absent state is the supported way to express a negative.
            "allNegative": None,
            "studies": [study_entry],
        },
    }


def write_engine_report(
    output_dir: str | Path, report: dict[str, Any], filename: str = DEFAULT_REPORT_FILENAME
) -> Path:
    """Write the engine report into the output directory as JSON.

    Args:
        output_dir (str | Path): Directory the DICOM SR is written to; the report goes alongside it
            so deepcOS collects both.
        report (dict[str, Any]): The report to serialise.
        filename (str): Report filename; must contain ``deepcreport`` (case-insensitive).

    Returns:
        Path: The path the report was written to.
    """
    output_path = Path(output_dir) / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    return output_path
