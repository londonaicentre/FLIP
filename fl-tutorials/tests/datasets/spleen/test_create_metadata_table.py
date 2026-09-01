"""The metadata table is the sole input to the OMOP conversion, so its columns are a contract."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pydicom
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "datasets" / "spleen" / "create_metadata_table.py"


@pytest.fixture(scope="module")
def metadata_module() -> ModuleType:
    module_name = "fl_tutorials_under_test.create_metadata_table"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


def _write_dicom(path: Path, subject: str, accession: str) -> None:
    """Write a minimal CT instance carrying the tags the OMOP conversion reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = CTImageStorage
    file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = file_meta
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.PatientID = accession
    ds.PatientName = subject
    ds.PatientSex = "M"
    ds.AccessionNumber = accession
    ds.Modality = "CT"
    ds.StudyDate = "20200101"
    ds.StudyInstanceUID = pydicom.uid.generate_uid()
    ds.SeriesInstanceUID = pydicom.uid.generate_uid()
    ds.Manufacturer = "ACME"
    ds.ManufacturerModelName = "Scanner9000"
    ds.SliceThickness = "5.0"
    ds.Rows = 4
    ds.Columns = 4
    ds.save_as(path, enforce_file_format=True)


def test_one_row_per_subject_with_the_contract_columns(metadata_module: ModuleType, tmp_path: Path) -> None:
    _write_dicom(tmp_path / "spleen_2" / "0000.dcm", "spleen_2", "111 222 333")
    _write_dicom(tmp_path / "spleen_2" / "0001.dcm", "spleen_2", "111 222 333")
    _write_dicom(tmp_path / "spleen_3" / "0000.dcm", "spleen_3", "444 555 666")

    frame = metadata_module.extract_dicom_metadata(str(tmp_path))

    assert len(frame) == 2, "one row per subject, not per instance"
    assert set(frame["Subject"]) == {"spleen_2", "spleen_3"}
    for column in ("PatientID", "AccessionNumber", "Manufacturer", "ManufacturerModelName", "Modality"):
        assert column in frame.columns, f"{column} is consumed by the OMOP conversion"
    assert set(frame["PatientID"]) == {"111 222 333", "444 555 666"}


def test_quality_checks_reject_duplicate_patient_ids(metadata_module: ModuleType, tmp_path: Path) -> None:
    """Duplicate PatientIDs would collapse two subjects onto one OMOP person."""
    _write_dicom(tmp_path / "spleen_2" / "0000.dcm", "spleen_2", "111 222 333")
    _write_dicom(tmp_path / "spleen_3" / "0000.dcm", "spleen_3", "111 222 333")

    frame = metadata_module.extract_dicom_metadata(str(tmp_path))

    with pytest.raises(AssertionError, match="Duplicate IDs found"):
        metadata_module.quality_checks(frame)


def test_directories_without_dicoms_are_skipped(metadata_module: ModuleType, tmp_path: Path) -> None:
    _write_dicom(tmp_path / "spleen_2" / "0000.dcm", "spleen_2", "111 222 333")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "readme.txt").write_text("not a dicom")

    frame = metadata_module.extract_dicom_metadata(str(tmp_path))

    assert set(frame["Subject"]) == {"spleen_2"}
