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

"""Write a 3-D volume as a DICOM series, deterministically, with pydicom alone (FLIP#1221).

The dataset converters turn public NIfTI volumes into the DICOM studies a mock trust PACS serves.
This is the one writer they share, and it replaces plastimatch, which the spleen converter used for
historical reasons: plastimatch needs root to install its binary (so the conversion was never in CI),
mints fresh UIDs on every run, and converts one 3-D volume into one CT series. None of that suits a
DICOM set that is *regenerated* rather than re-hosted — the brain MRI set is never uploaded, so it
must reproduce byte-for-byte from its public source, four MR series to a study.

Determinism is the contract. Every UID is ``generate_uid(prefix, entropy_srcs=…)`` over the caller's
stable identifiers plus a literal naming the UID's kind (``frame-of-reference``, ``series``,
``instance``), so a study, its series and every instance get distinct, reproducible UIDs; every date
and time comes from the caller's :class:`StudyTags`, never the clock; the file meta is fixed. Two
runs on the same input write the same bytes.

Geometry follows the NIfTI affine. nibabel hands back a RAS affine and an ``(i, j, k)`` array; DICOM
wants LPS positions and a ``(row, column)`` pixel plane. Rows run along ``j`` and columns along
``i``, so slice ``k`` is written as ``volume[:, :, k].T``, ``ImageOrientationPatient`` is the
LPS-flipped direction of the ``i`` axis then of the ``j`` axis, ``ImagePositionPatient`` is the LPS
position of voxel ``(0, 0, k)``, and ``PixelSpacing`` is ``[row spacing (along j), column spacing
(along i)]``. ``dcm2niix`` — which XNAT runs on every pulled series — rebuilds the same affine from
those tags.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, MRImageStorage, generate_uid

# The organisation root the published prostate set already uses. pydicom truncates a prefixed UID to 64
# characters — exactly the OMOP `image_study_uid` / `image_series_uid` column width — so never lengthen it.
UID_PREFIX = "1.2.826.0.1.3680043.2.1125."
IMPLEMENTATION_CLASS_UID = UID_PREFIX + "1"
IMPLEMENTATION_VERSION_NAME = "FLIP_DICOM_W1"

SOP_CLASS_UIDS = {"MR": MRImageStorage, "CT": CTImageStorage}
_RAS_TO_LPS = np.array([-1.0, -1.0, 1.0])

# Type 1/2 attributes of the MR and CT Image modules a caller may not care about; `SeriesTags.extra`
# overrides any of them. Empty strings are the type-2 "present, no value".
_MODALITY_DEFAULTS: dict[str, dict[str, Any]] = {
    "MR": {
        "ScanningSequence": "SE",
        "SequenceVariant": "NONE",
        "ScanOptions": "",
        "MRAcquisitionType": "3D",
        "RepetitionTime": "",
        "EchoTime": "",
        "EchoTrainLength": "",
    },
    "CT": {"KVP": ""},
}
# CT soft-tissue window; MR windows come from the volume itself.
_CT_WINDOW = (40.0, 400.0)


@dataclass(frozen=True)
class StudyTags:
    """The patient and study identity, shared by every series of a study.

    Dates are ``YYYYMMDD`` and times ``HHMMSS`` strings, exactly as written; person names are DICOM PN
    (``LAST^FIRST``). ``extra`` maps pydicom keywords to values set on every instance of the study
    (e.g. ``ClinicalTrialSubjectID``).
    """

    patient_id: str
    patient_name: str
    patient_sex: str
    patient_birth_date: str
    accession_number: str
    study_date: str
    study_time: str
    study_description: str
    referring_physician_name: str
    institution_name: str
    department_name: str = ""
    study_id: str = "1"
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SeriesTags:
    """One series' identity and acquisition parameters.

    ``extra`` maps pydicom keywords to values — the MR type-1 attributes (``EchoTime``,
    ``RepetitionTime``, ``MagneticFieldStrength``, ``ScanningSequence`` …), CT's ``KVP``, or anything
    else — and overrides the modality defaults.
    """

    modality: str
    series_number: int
    series_description: str
    protocol_name: str
    body_part_examined: str
    manufacturer: str
    manufacturer_model_name: str
    extra: Mapping[str, Any] = field(default_factory=dict)
    image_type: Sequence[str] = ("DERIVED", "SECONDARY", "AXIAL")
    patient_position: str = "HFS"
    acquisition_number: int = 1


@dataclass(frozen=True)
class _Geometry:
    """The per-series DICOM geometry of a NIfTI affine, in LPS."""

    affine: np.ndarray
    orientation: list[float]  # ImageOrientationPatient: direction of i (row direction), then of j
    pixel_spacing: list[float]  # [row spacing (along j), column spacing (along i)]
    slice_spacing: float
    slice_direction: np.ndarray  # unit LPS direction of increasing k

    @classmethod
    def from_affine(cls, affine: np.ndarray) -> _Geometry:
        axes = affine[:3, :3]
        spacing = np.linalg.norm(axes, axis=0)
        if np.any(spacing == 0):
            raise ValueError(f"degenerate affine: a voxel axis has zero length ({affine.tolist()})")
        direction = axes / spacing * _RAS_TO_LPS[:, None]
        return cls(
            affine=affine,
            orientation=[float(v) for v in (*direction[:, 0], *direction[:, 1])],
            pixel_spacing=[float(spacing[1]), float(spacing[0])],
            slice_spacing=float(spacing[2]),
            slice_direction=direction[:, 2],
        )

    def position(self, k: int) -> np.ndarray:
        """LPS position of voxel ``(0, 0, k)``, the first pixel of slice ``k``."""
        return (self.affine @ np.array([0.0, 0.0, float(k), 1.0]))[:3] * _RAS_TO_LPS

    def slice_location(self, k: int) -> float:
        """Signed distance of slice ``k`` along the slice direction, so locations increase with ``k``."""
        return float(np.dot(self.position(k), self.slice_direction))


def _encode(volume: np.ndarray, modality: str) -> tuple[np.ndarray, dict[str, Any]]:
    """The stored pixel array (little-endian 16-bit) and the pixel-module tags that describe it.

    MR intensities are arbitrary non-negative units, stored unsigned. CT volumes are Hounsfield units,
    stored signed with an identity rescale so a reader gets HU back without interpretation.
    """
    rounded = np.rint(volume)
    if modality == "MR":
        pixels = np.clip(rounded, 0, np.iinfo(np.uint16).max).astype("<u2")
        nonzero = pixels[pixels > 0]
        if nonzero.size:
            low, high = np.percentile(nonzero, [1, 99])
            window = (float((low + high) / 2), float(max(high - low, 1.0)))
        else:
            window = (0.0, 1.0)
        tags: dict[str, Any] = {"PixelRepresentation": 0}
    else:
        info = np.iinfo(np.int16)
        pixels = np.clip(rounded, info.min, info.max).astype("<i2")
        window = _CT_WINDOW
        tags = {"PixelRepresentation": 1, "RescaleIntercept": 0.0, "RescaleSlope": 1.0, "RescaleType": "HU"}
    tags.update({"WindowCenter": window[0], "WindowWidth": window[1]})
    return pixels, tags


def write_series(
    volume: np.ndarray,
    affine: np.ndarray,
    out_dir: Path,
    *,
    study: StudyTags,
    series: SeriesTags,
    uid_entropy: Sequence[str],
    filename: Callable[[Dataset], str] | None = None,
) -> list[Path]:
    """Write ``volume`` as one DICOM instance per slice, in slice order.

    Args:
        volume: A 3-D array indexed ``(i, j, k)`` as nibabel loads a NIfTI. Values are rounded to the
            stored integer type (unsigned for MR, signed HU for CT).
        affine: The 4×4 RAS voxel-to-world affine of the volume.
        out_dir: Directory the instances are written into (created if needed).
        study: Patient and study tags, shared by every series of the study.
        series: This series' tags. Its ``series_number`` is part of the series UID's entropy, so two
            series of one study must carry different numbers.
        uid_entropy: The stable identifiers of the *study* (e.g. ``["brain_mri_project", "BRATS_001"]``).
            The study, frame-of-reference, series and instance UIDs are all derived from it, so the same
            entropy yields the same UIDs on every run.
        filename: Names each file from its dataset; the default is ``<SOPInstanceUID>.dcm``.

    Returns:
        list[Path]: The written files, one per slice, ``k`` ascending.

    Raises:
        ValueError: For a volume that is not 3-D, an affine that is not 4×4 or is degenerate, or a
            modality this writer has no image module for.
    """
    volume = np.asarray(volume)
    if volume.ndim != 3:
        raise ValueError(f"expected a 3-D volume (i, j, k), got shape {volume.shape}")
    affine = np.asarray(affine, dtype=float)
    if affine.shape != (4, 4):
        raise ValueError(f"expected a 4x4 affine, got shape {affine.shape}")
    if series.modality not in SOP_CLASS_UIDS:
        raise ValueError(f"unsupported modality {series.modality!r}; this writer knows {sorted(SOP_CLASS_UIDS)}")

    entropy = [str(item) for item in uid_entropy]
    study_uid = generate_uid(prefix=UID_PREFIX, entropy_srcs=entropy)
    frame_of_reference_uid = generate_uid(prefix=UID_PREFIX, entropy_srcs=[*entropy, "frame-of-reference"])
    series_entropy = [*entropy, "series", str(series.series_number)]
    series_uid = generate_uid(prefix=UID_PREFIX, entropy_srcs=series_entropy)

    geometry = _Geometry.from_affine(affine)
    pixels, pixel_tags = _encode(volume, series.modality)
    sop_class_uid = SOP_CLASS_UIDS[series.modality]

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for k in range(volume.shape[2]):
        plane = np.ascontiguousarray(pixels[:, :, k].T)  # (row, column) = (j, i)
        sop_uid = generate_uid(prefix=UID_PREFIX, entropy_srcs=[*series_entropy, "instance", str(k)])

        ds = Dataset()
        ds.file_meta = FileMetaDataset()
        ds.file_meta.MediaStorageSOPClassUID = sop_class_uid
        ds.file_meta.MediaStorageSOPInstanceUID = sop_uid
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.file_meta.ImplementationClassUID = IMPLEMENTATION_CLASS_UID
        ds.file_meta.ImplementationVersionName = IMPLEMENTATION_VERSION_NAME

        # SOP common + image type. Creation and content dates are the study's: nothing here is the clock.
        ds.SpecificCharacterSet = "ISO_IR 100"
        ds.ImageType = list(series.image_type)
        ds.SOPClassUID = sop_class_uid
        ds.SOPInstanceUID = sop_uid
        ds.InstanceCreationDate = study.study_date
        ds.InstanceCreationTime = study.study_time
        ds.ContentDate = study.study_date
        ds.ContentTime = study.study_time

        # Patient.
        ds.PatientName = study.patient_name
        ds.PatientID = study.patient_id
        ds.PatientBirthDate = study.patient_birth_date
        ds.PatientSex = study.patient_sex

        # General study.
        ds.StudyInstanceUID = study_uid
        ds.StudyDate = study.study_date
        ds.StudyTime = study.study_time
        ds.ReferringPhysicianName = study.referring_physician_name
        ds.StudyID = study.study_id
        ds.AccessionNumber = study.accession_number
        ds.StudyDescription = study.study_description

        # General series.
        ds.Modality = series.modality
        ds.SeriesInstanceUID = series_uid
        ds.SeriesNumber = series.series_number
        ds.SeriesDate = study.study_date
        ds.SeriesTime = study.study_time
        ds.SeriesDescription = series.series_description
        ds.ProtocolName = series.protocol_name
        ds.BodyPartExamined = series.body_part_examined
        ds.PatientPosition = series.patient_position

        # General equipment.
        ds.Manufacturer = series.manufacturer
        ds.ManufacturerModelName = series.manufacturer_model_name
        ds.InstitutionName = study.institution_name
        if study.department_name:
            ds.InstitutionalDepartmentName = study.department_name

        # Frame of reference + image plane.
        ds.FrameOfReferenceUID = frame_of_reference_uid
        ds.PositionReferenceIndicator = ""
        ds.ImagePositionPatient = [float(v) for v in geometry.position(k)]
        ds.ImageOrientationPatient = geometry.orientation
        ds.PixelSpacing = geometry.pixel_spacing
        ds.SliceThickness = geometry.slice_spacing
        ds.SpacingBetweenSlices = geometry.slice_spacing
        ds.SliceLocation = geometry.slice_location(k)

        # General image + image pixel.
        ds.InstanceNumber = k + 1
        ds.AcquisitionNumber = series.acquisition_number
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.Rows, ds.Columns = int(plane.shape[0]), int(plane.shape[1])
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        for keyword, value in pixel_tags.items():
            setattr(ds, keyword, value)
        ds.PixelData = plane.tobytes()

        # Modality image module defaults, then the caller's per-study and per-series overrides.
        for keyword, value in {**_MODALITY_DEFAULTS[series.modality], **study.extra, **series.extra}.items():
            setattr(ds, keyword, value)

        path = out_dir / (filename(ds) if filename else f"{sop_uid}.dcm")
        ds.save_as(str(path), enforce_file_format=True)
        written.append(path)
    return written
