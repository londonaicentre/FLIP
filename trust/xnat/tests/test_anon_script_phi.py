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
"""
Tier 2 - exercise the parsed rules against synthetic DICOM datasets.

We construct a study populated with PHI in every tag the script references,
apply the parsed rules, and assert the resulting dataset is clean. This is the
"sign-off" piece called out by the security review (P2-07): proof that Patient
ID and related identifiers are actually scrubbed end-to-end on representative
sample studies.
"""

from __future__ import annotations

import pytest
from pydicom.dataset import Dataset

from anon_engine import Rule, XnatLabels, apply_rules

# Tags that should NOT remain in the output dataset after anonymization.
TAGS_THAT_MUST_BE_REMOVED: list[tuple[int, str]] = [
    (0x00100030, "Patient Birth Date"),
    (0x00100032, "Patient Birth Time"),
    (0x00101000, "Other Patient IDs"),
    (0x00101001, "Other Patient Names"),
    (0x00101002, "Other Patient IDs Sequence"),
    (0x00101005, "Patient Birth Name"),
    (0x00101040, "Patient Address"),
    (0x00102154, "Patient Telephone Numbers"),
    (0x00080080, "Institution Name"),
    (0x00080081, "Institution Address"),
    (0x00081040, "Institutional Department Name"),
    (0x00080090, "Referring Physician Name"),
    (0x00080092, "Referring Physician Address"),
    (0x00080094, "Referring Physician Telephone Numbers"),
    (0x00081048, "Physicians of Record"),
    (0x00081050, "Performing Physician Name"),
    (0x00081070, "Operators Name"),
    (0x00080050, "Accession Number"),
    (0x00101090, "Medical Record Locator"),
    (0x00102160, "Ethnic Group"),
    (0x00102180, "Occupation"),
    (0x001021B0, "Additional Patient History"),
    (0x00104000, "Patient Comments"),
    (0x00321032, "Requesting Physician"),
    (0x00400006, "Scheduled Performing Physician Name"),
    (0x00402016, "Placer Order Number"),
    (0x00402017, "Filler Order Number"),
    (0x0040A123, "Person Name (SR)"),
]

PHI_VALUES_THAT_MUST_NOT_LEAK = [
    "MRN-12345-PHI",
    "DOE^JANE^PHI",
    "19800101",
    "St Imaginary Hospital",
    "REF^PHI",
    "PERF^PHI",
    "OP^PHI",
    "ACC-PHI-001",
    "private comment with PHI",
    "PHI history of test",
    "OLD-MRN-99999",
    "SEQ-OLD-MRN-PHI",
]

ORIGINAL_UID_VALUES = [
    "1.2.3.4.5.6.7.8.9.1",  # SOP Instance
    "1.2.3.4.5.6.7.8.9.10",  # Study
    "1.2.3.4.5.6.7.8.9.11",  # Series
]


@pytest.fixture
def anonymized(phi_study: Dataset, anon_rules: list[Rule], xnat_labels: XnatLabels) -> Dataset:
    return apply_rules(phi_study, anon_rules, xnat_labels)


@pytest.mark.parametrize(
    ("tag", "description"),
    TAGS_THAT_MUST_BE_REMOVED,
    ids=[d for _, d in TAGS_THAT_MUST_BE_REMOVED],
)
def test_phi_tag_absent_after_anonymization(anonymized: Dataset, tag: int, description: str) -> None:
    assert tag not in anonymized, (
        f"{description} ({tag:08X}) is still present after anonymization: {anonymized.get(tag)!r}"
    )


def test_patient_id_replaced_with_subject_label(anonymized: Dataset, xnat_labels: XnatLabels) -> None:
    """P2-07 sign-off: Patient ID is the primary identifier and must be pseudonymised."""
    assert anonymized.PatientID == xnat_labels.subject, (
        f"Patient ID was {anonymized.PatientID!r}, expected XNAT subject label {xnat_labels.subject!r}"
    )


def test_patient_name_replaced_with_subject_label(anonymized: Dataset, xnat_labels: XnatLabels) -> None:
    assert str(anonymized.PatientName) == xnat_labels.subject


def test_study_description_replaced_with_session(anonymized: Dataset, xnat_labels: XnatLabels) -> None:
    assert anonymized.StudyDescription == xnat_labels.session


def test_uids_are_hashed(anonymized: Dataset) -> None:
    """SOP/Study/Series UIDs must change AND keep UID format."""
    assert anonymized.SOPInstanceUID not in ORIGINAL_UID_VALUES
    assert anonymized.StudyInstanceUID not in ORIGINAL_UID_VALUES
    assert anonymized.SeriesInstanceUID not in ORIGINAL_UID_VALUES
    for uid in (anonymized.SOPInstanceUID, anonymized.StudyInstanceUID, anonymized.SeriesInstanceUID):
        assert uid.startswith("2.25."), f"hashed UID {uid!r} should use the 2.25 OID arc"
        assert len(uid) <= 64, f"UID {uid!r} exceeds DICOM 64-char limit"


def test_uids_are_deterministic_when_rerun(phi_study: Dataset, anon_rules: list[Rule], xnat_labels: XnatLabels) -> None:
    """Hashing must be stable so the same study lands on the same pseudonym twice."""
    from copy import deepcopy

    first = apply_rules(deepcopy(phi_study), anon_rules, xnat_labels)
    second = apply_rules(deepcopy(phi_study), anon_rules, xnat_labels)
    assert first.SOPInstanceUID == second.SOPInstanceUID
    assert first.StudyInstanceUID == second.StudyInstanceUID
    assert first.SeriesInstanceUID == second.SeriesInstanceUID


def test_distinct_studies_get_distinct_uids(
    phi_study: Dataset, anon_rules: list[Rule], xnat_labels: XnatLabels
) -> None:
    from copy import deepcopy

    other = deepcopy(phi_study)
    other.StudyInstanceUID = "9.9.9.9.99"
    other.SOPInstanceUID = "9.9.9.9.100"

    a = apply_rules(deepcopy(phi_study), anon_rules, xnat_labels)
    b = apply_rules(other, anon_rules, xnat_labels)
    assert a.StudyInstanceUID != b.StudyInstanceUID
    assert a.SOPInstanceUID != b.SOPInstanceUID


def test_accession_number_tag_is_removed(anonymized: Dataset) -> None:
    """OMOP <-> XNAT linkage is via XNAT's session label, NOT the DICOM tag.

    imaging-api sets ``relabelMap.Session = accession_number`` at import time
    (see trust/imaging-api/imaging_api/routers/schemas.py:201-206), and
    downstream lookups use ``experiment_id_or_label=accession_id``. The DICOM
    Accession Number tag must therefore be stripped from the stored object so
    we don't leak it as PHI; the linkage continues to work via XNAT metadata.
    """
    assert 0x00080050 not in anonymized, (
        "Accession Number (0008,0050) must be removed from the stored DICOM. "
        "OMOP <-> XNAT linkage is preserved via XNAT's session label (set by "
        "imaging-api's relabelMap), not via this tag."
    )


def test_xnat_session_label_preserved_for_linkage(anonymized: Dataset, xnat_labels: XnatLabels) -> None:
    """Study Description holds the XNAT session label after anonymization.

    The script writes ``(0008,1030) := session``. In production the session
    label is the accession number, so this test pins the OMOP <-> XNAT join
    key into the anonymized payload's Study Description. If a future change
    drops this rule, downstream DICOM-only consumers lose the linkage.
    """
    assert anonymized.StudyDescription == xnat_labels.session


def test_deidentification_flags_set(anonymized: Dataset) -> None:
    assert anonymized[0x00120062].value == "YES", "Patient Identity Removed must be 'YES'"
    assert "FLIP" in anonymized[0x00120063].value, "De-identification Method must reference FLIP"


def test_no_phi_string_remains_anywhere(anonymized: Dataset) -> None:
    """Belt-and-braces: walk every top-level data element and assert no original PHI value survives."""
    leaked: list[tuple[str, str]] = []
    for elem in anonymized.iterall():
        if elem.VR in ("OB", "OW", "OF", "OD", "UN", "SQ"):
            continue  # skip binary / sequence content
        value = elem.value
        if value is None:
            continue
        text = str(value)
        for phi in PHI_VALUES_THAT_MUST_NOT_LEAK:
            if phi in text:
                leaked.append((str(elem.tag), phi))
    assert not leaked, f"PHI values leaked into anonymized dataset: {leaked}"
