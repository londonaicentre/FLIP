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

"""The shared NIfTI → DICOM writer: geometry, pixel encoding, determinism, and the tags the platform needs.

The writer replaces plastimatch (FLIP#1221): a pure pydicom + nibabel path that needs no root, no
binaries, and — the property the brain MRI dataset is built on — produces the same bytes on every run,
so a DICOM set can be regenerated from its source instead of being re-hosted. The geometry check
rebuilds voxel positions from ``ImageOrientationPatient`` / ``ImagePositionPatient`` /
``PixelSpacing`` and compares them with the NIfTI affine, numpy-only, because the dataset
environments have no MONAI.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pydicom
import pytest
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, MRImageStorage

DATASETS_DIR = Path(__file__).resolve().parents[3] / "datasets"
sys.path.insert(0, str(DATASETS_DIR))

from utils.dicom_writer import UID_PREFIX, SeriesTags, StudyTags, write_series  # noqa: E402

STUDY = StudyTags(
    patient_id="123 456 7890",
    patient_name="DOE^JANE",
    patient_sex="F",
    patient_birth_date="19800101",
    accession_number="FAK00000001",
    study_date="20150301",
    study_time="101500",
    study_description="MR Brain WO and W contrast IV",
    referring_physician_name="SMITH^JOHN",
    institution_name="KINGS_COLLEGE_HOSPITAL",
    department_name="Neuroradiology",
)
MR_SERIES = SeriesTags(
    modality="MR",
    series_number=1,
    series_description="FLAIR",
    protocol_name="FLAIR",
    body_part_examined="BRAIN",
    manufacturer="Siemens Healthineers",
    manufacturer_model_name="MAGNETOM Skyra",
    extra={
        "MagneticFieldStrength": 3.0,
        "EchoTime": 90.0,
        "RepetitionTime": 9000.0,
        "ScanningSequence": ["SE", "IR"],
        "SequenceVariant": "SK",
        "MRAcquisitionType": "3D",
    },
)
CT_SERIES = SeriesTags(
    modality="CT",
    series_number=1,
    series_description="CT Spleen",
    protocol_name="CT Spleen",
    body_part_examined="ABDOMEN",
    manufacturer="Canon Medical Systems",
    manufacturer_model_name="Aquilion",
    extra={"KVP": 120.0},
)
ENTROPY = ["test_project", "CASE_001"]


def _volume(shape=(8, 6, 3)) -> np.ndarray:
    """A float volume whose every voxel value is distinct, so a transposed write is caught."""
    return np.arange(np.prod(shape), dtype=np.float32).reshape(shape) * 3.0 - 20.0


# Flipped x, anisotropic spacing, and a 30° in-plane rotation: axis-aligned assumptions fail on it.
def _affine() -> np.ndarray:
    theta = np.deg2rad(30.0)
    rot = np.array([[np.cos(theta), -np.sin(theta), 0.0], [np.sin(theta), np.cos(theta), 0.0], [0.0, 0.0, 1.0]])
    scale = np.diag([-1.2, 0.8, 2.5])
    affine = np.eye(4)
    affine[:3, :3] = rot @ scale
    affine[:3, 3] = [10.0, -20.0, 5.0]
    return affine


def _read_all(files: list[Path]) -> list[pydicom.Dataset]:
    return [pydicom.dcmread(str(path)) for path in files]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ras_to_lps(point: np.ndarray) -> np.ndarray:
    return point * np.array([-1.0, -1.0, 1.0])


class TestGeometry:
    def test_one_instance_per_slice_with_rows_columns_and_pixels_transposed(self, tmp_path):
        volume, affine = _volume(), _affine()
        files = write_series(volume, affine, tmp_path, study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)

        assert len(files) == volume.shape[2]
        for k, ds in enumerate(_read_all(files)):
            assert (ds.Rows, ds.Columns) == (volume.shape[1], volume.shape[0])
            assert ds.InstanceNumber == k + 1
            expected = np.clip(np.rint(volume[:, :, k].T), 0, 65535).astype(np.uint16)
            np.testing.assert_array_equal(ds.pixel_array, expected)

    def test_positions_and_orientation_reproduce_the_affine(self, tmp_path):
        volume, affine = _volume(), _affine()
        files = write_series(volume, affine, tmp_path, study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)

        for k, ds in enumerate(_read_all(files)):
            iop = np.array(ds.ImageOrientationPatient, dtype=float)
            ipp = np.array(ds.ImagePositionPatient, dtype=float)
            row_spacing, column_spacing = (float(v) for v in ds.PixelSpacing)
            for i, j in ((0, 0), (7, 0), (0, 5), (3, 2)):
                expected = _ras_to_lps((affine @ np.array([i, j, k, 1.0]))[:3])
                actual = ipp + i * column_spacing * iop[:3] + j * row_spacing * iop[3:]
                np.testing.assert_allclose(actual, expected, atol=1e-4)
            assert float(ds.SliceThickness) == pytest.approx(2.5)
            assert float(ds.SpacingBetweenSlices) == pytest.approx(2.5)

    def test_slice_locations_increase_along_the_normal(self, tmp_path):
        files = write_series(_volume(), _affine(), tmp_path, study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)
        locations = [float(ds.SliceLocation) for ds in _read_all(files)]
        assert locations == sorted(locations)
        assert locations[1] - locations[0] == pytest.approx(2.5)


class TestPixelEncoding:
    def test_mr_is_unsigned_16_bit_with_negatives_clipped(self, tmp_path):
        volume = _volume()
        assert volume.min() < 0, "the fixture must exercise the clip"
        files = write_series(volume, _affine(), tmp_path, study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)

        ds = pydicom.dcmread(str(files[0]))
        assert (ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation) == (16, 16, 15, 0)
        assert (ds.SamplesPerPixel, ds.PhotometricInterpretation) == (1, "MONOCHROME2")
        assert ds.pixel_array.dtype == np.uint16
        assert ds.pixel_array.min() == 0
        assert "RescaleIntercept" not in ds
        assert ds.SOPClassUID == MRImageStorage

    def test_ct_is_signed_hounsfield_units(self, tmp_path):
        volume = _volume()
        files = write_series(volume, _affine(), tmp_path, study=STUDY, series=CT_SERIES, uid_entropy=ENTROPY)

        ds = pydicom.dcmread(str(files[0]))
        assert ds.PixelRepresentation == 1
        assert ds.pixel_array.dtype == np.int16
        assert ds.pixel_array.min() == int(np.rint(volume[:, :, 0].min()))
        assert (float(ds.RescaleIntercept), float(ds.RescaleSlope), ds.RescaleType) == (0.0, 1.0, "HU")
        assert ds.SOPClassUID == CTImageStorage
        assert float(ds.KVP) == 120.0


class TestDeterminism:
    def test_uids_are_the_same_on_every_run_and_distinct_by_kind(self, tmp_path):
        first = _read_all(
            write_series(_volume(), _affine(), tmp_path / "a", study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)
        )
        second = _read_all(
            write_series(_volume(), _affine(), tmp_path / "b", study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)
        )

        assert [ds.SOPInstanceUID for ds in first] == [ds.SOPInstanceUID for ds in second]
        assert first[0].StudyInstanceUID == second[0].StudyInstanceUID
        assert first[0].SeriesInstanceUID == second[0].SeriesInstanceUID
        assert first[0].FrameOfReferenceUID == second[0].FrameOfReferenceUID
        uids = {first[0].StudyInstanceUID, first[0].SeriesInstanceUID, first[0].FrameOfReferenceUID}
        uids |= {ds.SOPInstanceUID for ds in first}
        assert len(uids) == 3 + len(first), "study, series, frame of reference and every instance are all distinct"
        assert all(uid.startswith(UID_PREFIX) for uid in uids)
        assert max(len(uid) for uid in uids) <= 64, "the OMOP image_*_uid columns are varchar(64)"

    def test_a_second_series_shares_the_study_and_frame_of_reference(self, tmp_path):
        t1 = SeriesTags(
            **{**MR_SERIES.__dict__, "series_number": 2, "series_description": "T1w", "protocol_name": "T1w"}
        )
        flair = _read_all(
            write_series(_volume(), _affine(), tmp_path / "1", study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)
        )
        t1w = _read_all(write_series(_volume(), _affine(), tmp_path / "2", study=STUDY, series=t1, uid_entropy=ENTROPY))

        assert flair[0].StudyInstanceUID == t1w[0].StudyInstanceUID
        assert flair[0].FrameOfReferenceUID == t1w[0].FrameOfReferenceUID
        assert flair[0].SeriesInstanceUID != t1w[0].SeriesInstanceUID
        assert {ds.SOPInstanceUID for ds in flair}.isdisjoint({ds.SOPInstanceUID for ds in t1w})
        assert (flair[0].SeriesNumber, t1w[0].SeriesNumber) == (1, 2)

    def test_a_different_case_gets_different_uids(self, tmp_path):
        a = _read_all(
            write_series(_volume(), _affine(), tmp_path / "a", study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)
        )
        b = _read_all(
            write_series(
                _volume(),
                _affine(),
                tmp_path / "b",
                study=STUDY,
                series=MR_SERIES,
                uid_entropy=["test_project", "CASE_002"],
            )
        )
        assert a[0].StudyInstanceUID != b[0].StudyInstanceUID

    def test_files_are_byte_identical_across_runs(self, tmp_path):
        first = write_series(_volume(), _affine(), tmp_path / "a", study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)
        second = write_series(_volume(), _affine(), tmp_path / "b", study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)

        assert [p.name for p in first] == [p.name for p in second]
        assert [_sha256(p) for p in first] == [_sha256(p) for p in second]

    def test_nothing_is_stamped_with_the_wall_clock(self, tmp_path):
        files = write_series(_volume(), _affine(), tmp_path, study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)
        today = date.today().strftime("%Y%m%d")
        ds = pydicom.dcmread(str(files[0]))
        for element in ds.iterall():
            if element.VR == "DA":
                assert element.value != today, f"{element.keyword} carries today's date"
        assert ds.InstanceCreationDate == STUDY.study_date
        assert ds.ContentDate == STUDY.study_date
        assert ds.SeriesDate == STUDY.study_date

    def test_filenames_default_to_the_sop_instance_uid(self, tmp_path):
        files = write_series(_volume(), _affine(), tmp_path, study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)
        for path, ds in zip(files, _read_all(files)):
            assert path.name == f"{ds.SOPInstanceUID}.dcm"
            assert path.parent == tmp_path


class TestPlatformTags:
    def test_every_tag_the_pull_and_publish_paths_read_is_present(self, tmp_path):
        """imaging-api's DQR parse needs sex, dates, description and referrer; publish_dicom.py needs
        non-empty SeriesNumber/AcquisitionNumber; dcm2niix names the NIfTI from ProtocolName."""
        files = write_series(_volume(), _affine(), tmp_path, study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)
        ds = pydicom.dcmread(str(files[0]))

        assert ds.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian
        assert ds.file_meta.MediaStorageSOPInstanceUID == ds.SOPInstanceUID
        assert ds.SpecificCharacterSet == "ISO_IR 100"
        assert (ds.PatientName, ds.PatientID, ds.PatientSex, ds.PatientBirthDate) == (
            "DOE^JANE",
            "123 456 7890",
            "F",
            "19800101",
        )
        assert (ds.AccessionNumber, ds.StudyDate, ds.StudyTime) == ("FAK00000001", "20150301", "101500")
        assert ds.StudyDescription == STUDY.study_description
        assert ds.ReferringPhysicianName == "SMITH^JOHN"
        assert ds.StudyID
        assert ds.Modality == "MR"
        assert (ds.SeriesNumber, ds.AcquisitionNumber) == (1, 1)
        assert (ds.SeriesDescription, ds.ProtocolName) == ("FLAIR", "FLAIR")
        assert (ds.BodyPartExamined, ds.PatientPosition) == ("BRAIN", "HFS")
        assert (ds.Manufacturer, ds.ManufacturerModelName) == ("Siemens Healthineers", "MAGNETOM Skyra")
        assert ds.InstitutionName == "KINGS_COLLEGE_HOSPITAL"
        assert ds.InstitutionalDepartmentName == "Neuroradiology"
        assert float(ds.MagneticFieldStrength) == 3.0
        assert (float(ds.EchoTime), float(ds.RepetitionTime)) == (90.0, 9000.0)
        assert list(ds.ScanningSequence) == ["SE", "IR"]
        assert ds.MRAcquisitionType == "3D"
        assert list(ds.ImageType)[:2] == ["DERIVED", "SECONDARY"]
        assert "WindowCenter" in ds
        assert "WindowWidth" in ds

    def test_mr_type_one_attributes_get_defaults_when_not_supplied(self, tmp_path):
        bare = SeriesTags(**{**MR_SERIES.__dict__, "extra": {}})
        files = write_series(_volume(), _affine(), tmp_path, study=STUDY, series=bare, uid_entropy=ENTROPY)
        ds = pydicom.dcmread(str(files[0]))
        for keyword in (
            "ScanningSequence",
            "SequenceVariant",
            "ScanOptions",
            "MRAcquisitionType",
            "EchoTime",
            "RepetitionTime",
        ):
            assert keyword in ds, keyword

    def test_a_volume_that_is_not_3d_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="3-D"):
            write_series(np.zeros((4, 4)), _affine(), tmp_path, study=STUDY, series=MR_SERIES, uid_entropy=ENTROPY)

    def test_an_unknown_modality_is_refused(self, tmp_path):
        bad = SeriesTags(**{**MR_SERIES.__dict__, "modality": "US"})
        with pytest.raises(ValueError, match="modality"):
            write_series(_volume(), _affine(), tmp_path, study=STUDY, series=bad, uid_entropy=ENTROPY)
