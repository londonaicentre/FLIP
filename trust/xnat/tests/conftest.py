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
#

from __future__ import annotations

from pathlib import Path

import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import UID, CTImageStorage, ExplicitVRLittleEndian

from anon_engine import Rule, XnatLabels, parse_script

ANON_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "xnat" / "config" / "anon_script.das"


@pytest.fixture(scope="session")
def anon_script_path() -> Path:
    assert ANON_SCRIPT_PATH.is_file(), f"anon_script.das not found at {ANON_SCRIPT_PATH}"
    return ANON_SCRIPT_PATH


@pytest.fixture(scope="session")
def anon_rules(anon_script_path: Path) -> list[Rule]:
    return parse_script(anon_script_path)


@pytest.fixture
def xnat_labels() -> XnatLabels:
    # In production the XNAT session label is set by imaging-api's
    # `relabelMap.Session = accession_number` (see
    # trust/imaging-api/imaging_api/routers/schemas.py:201-206), so the
    # session label IS the accession number. Encode that here so tests
    # exercise the same invariant the OMOP <-> XNAT linkage relies on.
    return XnatLabels(project="FLIP_TEST_PROJECT", subject="FLIP_SUBJ_001", session="ACC-FAK-77115197")


def _phi_study() -> Dataset:
    """A synthetic CT study populated with PHI in every tag the script touches.

    The values are obviously fake but recognisable in test failure output so
    a regression makes it clear which PHI leaked through.
    """
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = CTImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = UID("1.2.3.4.5.6.7.8.9.1")
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    # Direct patient identifiers
    ds.PatientID = "MRN-12345-PHI"
    ds.PatientName = "DOE^JANE^PHI"
    ds.PatientBirthDate = "19800101"
    ds.PatientBirthTime = "120000"
    ds.PatientBirthName = "SMITH^JANE"
    ds.PatientAddress = "10 Downing Street, London"
    ds.PatientTelephoneNumbers = "+44-20-7946-0000"
    ds.OtherPatientIDs = "OLD-MRN-99999"
    ds.OtherPatientNames = "DOE^J^PHI"

    # Other Patient IDs Sequence (SQ): nested item carrying PHI. The whole
    # sequence must be stripped by the `- (0010,1002)` rule. We embed a
    # recognisable PHI string inside the sequence item so iterall()-based
    # leak-scans flag it if the sequence ever survives anonymization.
    other_id_item = Dataset()
    other_id_item.PatientID = "SEQ-OLD-MRN-PHI"
    other_id_item.add_new(0x00100022, "CS", "TEXT")  # Type of Patient ID
    ds.OtherPatientIDsSequence = [other_id_item]

    # Institutional identifiers
    ds.InstitutionName = "St Imaginary Hospital"
    ds.InstitutionAddress = "1 Hospital Way, London"
    ds.InstitutionalDepartmentName = "Radiology PHI"

    # Physician/operator identifiers
    ds.ReferringPhysicianName = "REF^PHI"
    ds.ReferringPhysicianAddress = "1 GP Surgery"
    ds.ReferringPhysicianTelephoneNumbers = "+44-20-0000-0000"
    ds.PhysiciansOfRecord = "PHY^OF^RECORD"
    ds.PerformingPhysicianName = "PERF^PHI"
    ds.OperatorsName = "OP^PHI"

    # Other identifying tags
    ds.AccessionNumber = "ACC-PHI-001"
    ds.MedicalRecordLocator = "LOC-PHI"
    ds.EthnicGroup = "PHI-ETH"
    ds.Occupation = "PHI-OCC"
    ds.AdditionalPatientHistory = "PHI history of test"
    ds.PatientComments = "private comment with PHI"
    ds.RequestingPhysician = "REQ^PHI"
    ds.ScheduledPerformingPhysicianName = "SCHED^PHI"

    # Order numbers (Service Request Module)
    ds.add_new(0x00402016, "LO", "PLACER-PHI")  # Placer Order Number
    ds.add_new(0x00402017, "LO", "FILLER-PHI")  # Filler Order Number

    # UIDs we expect to have hashed
    ds.SOPInstanceUID = "1.2.3.4.5.6.7.8.9.1"
    ds.StudyInstanceUID = "1.2.3.4.5.6.7.8.9.10"
    ds.SeriesInstanceUID = "1.2.3.4.5.6.7.8.9.11"

    # Optional: a structured-report-style Person Name (we don't put it in a
    # sequence; the rule targets the top-level tag). DicomEdit traversal of
    # nested sequences is out of scope for this lightweight interpreter.
    ds.add_new(0x0040A123, "PN", "PERSON^IN^SR")

    return ds


@pytest.fixture
def phi_study() -> Dataset:
    return _phi_study()
