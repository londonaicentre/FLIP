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

import os
from pathlib import Path

import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import UID, CTImageStorage, ExplicitVRLittleEndian

from anon_engine import Rule, XnatLabels, parse_script

ANON_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "xnat" / "config" / "anon_script.das"

# These tests run make themselves. Run from a make (`make unit_test XNAT_TAG=…`), they would inherit its
# command-line variables, both through MAKEFLAGS and as plain environment variables, and render recipes for
# that caller instead of their own inputs. Drop both.
for _token in os.environ.get("MAKEFLAGS", "").split():
    if "=" in _token and not _token.startswith("-"):
        os.environ.pop(_token.split("=", 1)[0], None)
for _var in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL"):
    os.environ.pop(_var, None)


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
    # `relabelMap.Session = accession_number` (`ImportStudy.set_relabel_map`
    # in trust/imaging-api/imaging_api/routers/schemas.py), so the
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
    # sequence must be stripped by the `- */(0010,1002)` rule. We embed a
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

    # A structured-report Person Name, at the top level and where real reports
    # carry it: inside Content Sequence
    ds.add_new(0x0040A123, "PN", "PERSON^IN^SR")
    sr_item = Dataset()
    sr_item.ValueType = "PNAME"
    sr_item.PersonName = "SR^NESTED^PHI"
    ds.ContentSequence = [sr_item]

    # Nested copies of identifiers the script removes or replaces at the top level
    request = Dataset()
    request.AccessionNumber = "NESTED-ACC-PHI"
    request.add_new(0x00110010, "LO", "ACME PRIVATE")
    request.add_new(0x00111001, "LO", "NESTED-PRIV-PHI")
    ds.RequestAttributesSequence = [request]
    # What a PACS keeps after overwriting values, two sequence levels down
    original = Dataset()
    original.PatientName = "ORIG^NAME^PHI"
    original.PatientID = "ORIG-MRN-PHI"
    original.PatientBirthDate = "19800101"
    modification = Dataset()
    modification.ModifiedAttributesSequence = [original]
    ds.OriginalAttributesSequence = [modification]

    # Vendor private block
    ds.add_new(0x00090010, "LO", "GEMS_IDEN_01")
    ds.add_new(0x00091002, "LO", "PRIV-MRN-PHI")

    # Dates, times and patient characteristics: OMOP holds these, so the script drops them
    ds.StudyDate = "20240315"
    ds.SeriesDate = "20240315"
    ds.AcquisitionDateTime = "20240315101512"
    ds.StudyTime = "101512"
    ds.PatientAge = "043Y"
    ds.PatientSex = "F"
    ds.PatientSize = "1.68"
    ds.PatientWeight = "61.5"

    # Free text and other IDs a PACS fills in
    ds.StudyDescription = "CT CHEST FOR MRN-12345-PHI"
    ds.SeriesDescription = "SERIES-DESC-PHI"
    ds.StudyID = "STUDY-ID-PHI"
    ds.add_new(0x60004000, "LT", "OVERLAY-PHI")  # Overlay Comments, group 6000
    ds.add_new(0x60024000, "LT", "OVERLAY-PHI")  # ...and the next overlay group
    ds.add_new(0x50000022, "LO", "CURVE-PHI")  # Curve Description

    # Other UIDs: hashed at the top level, removed where nested (DicomEdit cannot assign there).
    # Referenced Series Sequence is not in Table E.1-1, so it survives with its UIDs gone.
    ds.FrameOfReferenceUID = "1.2.3.4.5.6.7.8.9.12"
    instance = Dataset()
    instance.ReferencedSOPClassUID = CTImageStorage
    instance.ReferencedSOPInstanceUID = "1.2.3.4.5.6.7.8.9.13"
    series = Dataset()
    series.SeriesInstanceUID = "1.2.3.4.5.6.7.8.9.11"
    series.ReferencedInstanceSequence = [instance]
    ds.ReferencedSeriesSequence = [series]

    return ds


@pytest.fixture
def phi_study() -> Dataset:
    return _phi_study()
