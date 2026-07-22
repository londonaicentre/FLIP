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

"""The machine-readable half of a segmentation result, for a deepcOS engine report.

A DICOM SEG carries the pixels; deepcOS additionally requires whatever a mask encodes to appear in
machine-readable form. A mask has no dense representation in the engine report model, so what a
segmentation result carries is its *derived clinical content*: the segmented volume, as a
:func:`volume_quantity` with coded units and an anatomical attribute. Computing it is the piece the
DICOM SEG writer does not — it emits pixels and stops.

This module is deliberately free of the MONAI Deploy SDK so it can be unit-tested on its own; the
operator in ``deepc_report_operator.py`` is the thin adapter that feeds it the mask and spacing and
writes the result out.

**This report shape is a scaffold, not the vendor schema.** deepc's engine-report schema is
access-controlled and is not reproduced here; the field names below match only what the FLIP
packaging guide records as *required* — a derived job status, coded messages, and an engine result
following the DICOM hierarchy, with the regulatory ``allNegative`` field left null. Anyone
integrating against deepcOS must validate against the vendor's own tooling.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# The report's default filename. deepcOS discovers the report by the case-insensitive substring
# ``deepcreport`` anywhere in the filename, so this qualifies while e.g. ``report_deepc.json`` would
# not.
DEFAULT_REPORT_FILENAME = "spleen-deepcReport.json"


@dataclass(frozen=True)
class Coding:
    """A coded clinical concept.

    Attributes:
        scheme (str): Coding scheme, e.g. ``"SCT"`` for SNOMED CT or ``"UCUM"`` for units.
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


# The anatomical attribute the spleen template's quantity is measured on. Swap this for the organ
# your model segments; the code must be a real SNOMED CT concept.
SPLEEN = Coding("SCT", "78961009", "Spleen structure")

# What the quantity measures, and the unit it is expressed in.
VOLUME = Coding("SCT", "118565006", "Volume")
MILLILITRE = Coding("UCUM", "mL", "milliliter")


def segmented_volume_ml(mask: Any, spacing_mm: tuple[float, ...] | list[float]) -> float:
    """Compute the segmented volume of a mask in millilitres.

    Foreground is every non-zero voxel, so a multi-label mask contributes its whole segmented
    region. The volume is the foreground voxel count scaled by the physical volume of one voxel
    (the product of the per-axis spacings), converted from cubic millimetres to millilitres.

    Args:
        mask (Any): The segmentation mask as an array-like of any dimensionality.
        spacing_mm (tuple[float, ...] | list[float]): Physical voxel spacing in millimetres, one
            value per mask axis.

    Returns:
        float: The segmented volume in millilitres.

    Raises:
        ValueError: If ``spacing_mm`` does not give one spacing per mask dimension — guarding the
            silent mis-scaling that a mismatched spacing would otherwise produce.
    """
    array = np.asarray(mask)
    if len(spacing_mm) != array.ndim:
        raise ValueError(
            f"spacing_mm must give one size per mask dimension: got {len(spacing_mm)} "
            f"for a {array.ndim}-D mask"
        )
    voxel_volume_mm3 = float(np.prod(np.asarray(spacing_mm, dtype=float)))
    foreground_voxels = int(np.count_nonzero(array))
    return foreground_voxels * voxel_volume_mm3 / 1000.0


# Candidate metadata keys for each voxel dimension, in the order MONAI Deploy's
# DICOMSeriesToVolumeOperator populates them. Only the three magnitudes are needed — their pairing
# to array axes does not affect a volume, which is their product.
_SPACING_KEYS: tuple[tuple[str, ...], ...] = (
    ("row_pixel_spacing",),
    ("col_pixel_spacing",),
    ("depth_pixel_spacing", "slice_thickness"),
)


def voxel_spacing(metadata: dict[str, Any]) -> tuple[float, float, float]:
    """Extract the three voxel spacings (in millimetres) from a MONAI Deploy image's metadata.

    Args:
        metadata (dict[str, Any]): The ``Image`` metadata from the DICOM-to-volume conversion.

    Returns:
        tuple[float, float, float]: The row, column and slice spacings in millimetres.

    Raises:
        ValueError: If any of the three spacings is absent. An assumed default would silently
            corrupt the reported volume, so a missing spacing is surfaced rather than guessed.
    """
    spacing = []
    for candidates in _SPACING_KEYS:
        value = next((metadata[key] for key in candidates if key in metadata), None)
        if value is None:
            raise ValueError(
                f"voxel spacing is incomplete: none of {candidates} is present in the image "
                f"metadata, so the segmented volume cannot be computed"
            )
        spacing.append(float(value))
    return spacing[0], spacing[1], spacing[2]


def volume_quantity(volume_ml: float, anatomy: Coding = SPLEEN) -> dict[str, Any]:
    """Build a segmented-volume quantity entry.

    Args:
        volume_ml (float): The segmented volume in millilitres.
        anatomy (Coding): The anatomical structure the volume is measured on.

    Returns:
        dict[str, Any]: A quantity carrying the measured concept, value, coded unit and anatomy.
    """
    return {
        "concept": VOLUME.as_dict(),
        "value": float(volume_ml),
        "unit": MILLILITRE.as_dict(),
        "anatomy": anatomy.as_dict(),
    }


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
    quantities: list[dict[str, Any]] | None = None,
    messages: list[dict[str, str]] | None = None,
    status: str | None = None,
    study_uid: str | None = None,
    series_uid: str | None = None,
) -> dict[str, Any]:
    """Assemble the engine report from a segmentation run's derived quantities.

    Job status is *derived* rather than assumed successful: a process exit code does not indicate
    whether the analysis succeeded, and a segmentation that produced no foreground voxels is exactly
    the silent-empty-output failure mode. A zero total volume therefore degrades the status and
    adds a coded message, instead of reporting a clean success over an empty result.

    Args:
        quantities (list[dict[str, Any]] | None): Derived quantities, e.g. from
            :func:`volume_quantity`.
        messages (list[dict[str, str]] | None): Coded messages to include, e.g. from
            :func:`coded_message`.
        status (str | None): Explicit job status; when omitted it is derived from the quantities.
        study_uid (str | None): StudyInstanceUID of the analysed study, for the result hierarchy.
        series_uid (str | None): SeriesInstanceUID of the analysed series, for the result hierarchy.

    Returns:
        dict[str, Any]: The engine report, carrying the required ``jobStatus``, ``codedMessages``
        and ``engineResult`` fields.
    """
    quantities = quantities or []
    derived_messages = list(messages or [])

    if status is None:
        segmented_nothing = len(quantities) > 0 and all(q.get("value", 0) <= 0 for q in quantities)
        if segmented_nothing:
            status = "DEGRADED"
            derived_messages.append(
                coded_message(
                    "SEG_EMPTY",
                    "Segmentation is empty; no foreground voxels were produced. Check series "
                    "selection and preprocessing rather than treating this as a clean result.",
                )
            )
        else:
            status = "SUCCESS"

    series_entry: dict[str, Any] = {"quantities": quantities, "findings": []}
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
        output_dir (str | Path): Directory the DICOM artefacts are written to; the report goes
            alongside them so deepcOS collects both.
        report (dict[str, Any]): The report to serialise.
        filename (str): Report filename; must contain ``deepcreport`` (case-insensitive).

    Returns:
        Path: The path the report was written to.
    """
    output_path = Path(output_dir) / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    return output_path
