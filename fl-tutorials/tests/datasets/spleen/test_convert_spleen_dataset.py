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

"""The spleen NIfTI → DICOM conversion, on the shared writer instead of plastimatch (FLIP#1221).

Before this the conversion needed root (plastimatch's binary install) and was never tested; now it is
a pure-Python, deterministic step. What must hold: the output layout the rest of the chain reads
(``dicom_output/<subject>/*.dcm``), the tags the OMOP converter keys on (``StudyDescription`` is looked
up in ``MAPPING_PROCEDURE_TYPE``; ``StudyTime`` is ``int()``-coerced), the columns
``create_metadata_table.py`` extracts, and that re-running writes the same bytes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import nibabel as nib
import numpy as np
import pydicom
import pytest

DATASETS_DIR = Path(__file__).resolve().parents[3] / "datasets"
CONVERTER_PATH = DATASETS_DIR / "spleen" / "convert_spleen_dataset.py"
METADATA_PATH = DATASETS_DIR / "spleen" / "create_metadata_table.py"

# The subset of the metadata table the OMOP converter reads (tests/datasets/spleen/test_omop_convert_spleen.py).
OMOP_METADATA_COLUMNS = [
    "Subject", "FileName", "FilePath", "PatientID", "PatientName", "PatientSex",
    "PatientBirthDate", "AccessionNumber", "Modality", "StudyDate", "StudyTime",
    "StudyDescription", "StudyInstanceUID", "SeriesInstanceUID",
    "Manufacturer", "ManufacturerModelName", "SliceThickness", "Rows", "Columns",
]  # fmt: skip


def _load(name: str, path: Path) -> ModuleType:
    sys.path.insert(0, str(DATASETS_DIR))
    module_name = f"fl_tutorials_under_test.{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


@pytest.fixture(scope="module")
def converter() -> ModuleType:
    return _load("convert_spleen_dataset", CONVERTER_PATH)


@pytest.fixture(scope="module")
def metadata() -> ModuleType:
    return _load("create_metadata_table", METADATA_PATH)


def _write_nifti(path: Path, seed: int) -> None:
    """A tiny CT-like volume in Hounsfield units, with a non-unit spacing."""
    rng = np.random.default_rng(seed)
    volume = rng.integers(-1000, 400, size=(6, 5, 3)).astype(np.int16)
    affine = np.diag([-0.8, 0.8, 5.0, 1.0])
    affine[:3, 3] = [100.0, -80.0, -300.0]
    path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(volume, affine), str(path))


@pytest.fixture
def raw_msd(tmp_path: Path) -> Path:
    """``data/Task09_Spleen/imagesTr`` with two subjects and one macOS resource fork to ignore."""
    images = tmp_path / "data" / "Task09_Spleen" / "imagesTr"
    _write_nifti(images / "spleen_2.nii.gz", 2)
    _write_nifti(images / "spleen_10.nii.gz", 10)
    (images / "._spleen_2.nii.gz").write_bytes(b"\x00\x05\x16\x07 not a nifti")
    return images


def _sha256s(directory: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.glob("*.dcm"))}


class TestConvertDataset:
    def test_writes_one_series_per_subject_in_the_chain_layout(self, converter, raw_msd, tmp_path):
        out = tmp_path / "dicom_output"
        written = converter.convert_dataset(raw_msd, out)

        assert sorted(written) == ["spleen_10", "spleen_2"]
        assert not (out / "._spleen_2").exists(), "resource forks are not subjects"
        for subject in written:
            files = sorted((out / subject).glob("*.dcm"))
            assert len(files) == 3, "one instance per slice"
            datasets = [pydicom.dcmread(str(f)) for f in files]
            assert {ds.SeriesInstanceUID for ds in datasets} == {datasets[0].SeriesInstanceUID}
            assert datasets[0].Modality == "CT"
            assert datasets[0].StudyDescription == "Spleen CT"
            assert datasets[0].SeriesDescription == "CT Spleen"
            assert datasets[0].ProtocolName == "CT Spleen"
            assert datasets[0].ClinicalTrialSubjectID == subject
            assert datasets[0].StudyTime.isdigit(), "omop_convert_spleen int()-coerces StudyTime"
            assert datasets[0].pixel_array.dtype == np.int16, "Hounsfield units stay signed"
            assert float(datasets[0].KVP) == 120.0

    def test_subjects_get_distinct_identities(self, converter, raw_msd, tmp_path):
        out = tmp_path / "dicom_output"
        converter.convert_dataset(raw_msd, out)
        first = pydicom.dcmread(str(next((out / "spleen_2").glob("*.dcm"))))
        second = pydicom.dcmread(str(next((out / "spleen_10").glob("*.dcm"))))

        assert first.PatientID != second.PatientID
        assert first.AccessionNumber != second.AccessionNumber
        assert first.StudyInstanceUID != second.StudyInstanceUID

    def test_reconversion_is_byte_identical(self, converter, raw_msd, tmp_path):
        converter.convert_dataset(raw_msd, tmp_path / "a")
        converter.convert_dataset(raw_msd, tmp_path / "b")
        assert _sha256s(tmp_path / "a" / "spleen_2") == _sha256s(tmp_path / "b" / "spleen_2")

    def test_reconversion_replaces_a_subjects_previous_output(self, converter, raw_msd, tmp_path):
        out = tmp_path / "dicom_output"
        (out / "spleen_2").mkdir(parents=True)
        stale = out / "spleen_2" / "stale.dcm"
        stale.write_bytes(b"old")

        converter.convert_dataset(raw_msd, out)

        assert not stale.exists()

    def test_an_empty_input_dir_is_an_error_not_a_silent_no_op(self, converter, tmp_path):
        empty = tmp_path / "imagesTr"
        empty.mkdir()
        with pytest.raises(SystemExit, match="no .nii.gz"):
            converter.convert_dataset(empty, tmp_path / "dicom_output")


class TestMetadataTableCompatibility:
    def test_the_extractor_still_finds_every_column_the_omop_converter_reads(
        self, converter, metadata, raw_msd, tmp_path
    ):
        out = tmp_path / "dicom_output"
        converter.convert_dataset(raw_msd, out)

        table = metadata.extract_dicom_metadata(str(out))

        assert len(table) == 2, "one row per subject"
        missing = [c for c in OMOP_METADATA_COLUMNS if c not in table.columns]
        assert not missing, f"metadata table lost columns the OMOP converter reads: {missing}"
        assert metadata.quality_checks(table)
        assert set(table["StudyDescription"].str.lower()) == {"spleen ct"}, "the MAPPING_PROCEDURE_TYPE key"
