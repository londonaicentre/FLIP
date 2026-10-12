# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

from uuid import uuid4

import pytest
from pydantic import ValidationError

from imaging_api.routers.schemas import (
    CentralHubProject,
    DownloadImagesRequestData,
    ImportStudy,
    ImportStudyRequest,
    UploadDataRequest,
)


def test_central_hub_project_defaults_dicom_to_nifti_true():
    """CentralHubProject should default dicom_to_nifti to True for backward compatibility."""
    project = CentralHubProject(
        project_id=uuid4(),
        trust_id=uuid4(),
        project_name="Test",
        query="SELECT 1",
        users=[],
    )
    assert project.dicom_to_nifti is True


@pytest.mark.parametrize(
    "bad_name",
    [
        "evil<script>",
        "name>tag",
        "amp&injection",
        "</name><name>spoof",
    ],
)
def test_central_hub_project_rejects_xml_control_chars_in_name(bad_name: str):
    """
    project_name must not carry XML control characters that could inject into
    the XNAT projectData payload built by imaging-api.
    """
    with pytest.raises(ValidationError, match="XML control characters"):
        CentralHubProject(
            project_id=uuid4(),
            trust_id=uuid4(),
            project_name=bad_name,
            query="SELECT 1",
            users=[],
        )


@pytest.mark.parametrize(
    "bad_accession_id",
    [
        "",
        "../../etc/passwd",
        "ACC/123",
        "ACC\\123",
        "ACC%2F123",
        "ACC 123",
        "ACC?format=json",
        "ACC#frag",
        "..",
        ".",
    ],
)
def test_accession_id_rejects_traversal_and_url_metacharacters(bad_accession_id: str):
    """Traversal/URL-injection payloads must fail validation before any XNAT call."""
    with pytest.raises(ValidationError, match="accession_id"):
        DownloadImagesRequestData(encrypted_central_hub_project_id="enc", accession_id=bad_accession_id)

    with pytest.raises(ValidationError, match="accession_id"):
        UploadDataRequest(
            encrypted_central_hub_project_id="enc",
            accession_id=bad_accession_id,
            scan_id="SCAN1",
            resource_id="NIFTI",
            files=["scan.nii"],
            exist_ok=False,
        )


@pytest.mark.parametrize(
    "good_accession_id",
    [
        "ACC123",
        "ACC-123",
        "ACC_123",
        "ACC.123",
        "ACC..123",  # embedded dots are fine — only an entire "." or ".." segment traverses
        "1.2.840.113619.2.55.3",
    ],
)
def test_accession_id_accepts_safe_charset(good_accession_id: str):
    """RFC 3986 §2.3 unreserved charset must be accepted."""
    request = DownloadImagesRequestData(encrypted_central_hub_project_id="enc", accession_id=good_accession_id)
    assert request.accession_id == good_accession_id


@pytest.mark.parametrize("bad_accession_number", ["", ".", "..", "../../OTHER", "ACC/123", "ACC 123"])
def test_import_study_rejects_unsafe_accession_number(bad_accession_number: str):
    """The accession number becomes the XNAT session label and later the download URL's
    accession_id, so the same path-segment rule applies at import time (#908)."""
    with pytest.raises(ValidationError, match="accessionNumber"):
        ImportStudy(studyInstanceUid="1.2.3", accessionNumber=bad_accession_number)


def test_import_study_accepts_safe_accession_number():
    study = ImportStudy(studyInstanceUid="1.2.3", accessionNumber="ACC..123")
    assert study.relabel_map["Session"] == "ACC..123"


def test_import_study_request_deduplicates_studies():
    """
    Check ImportStudyRequest deduplicates input studies.
    Check that the last study with the same StudyInstanceUID is kept.
    """
    data = {
        "projectId": "test",
        "studies": [
            {"studyInstanceUid": "1", "accessionNumber": "FirstAccessionNumber"},
            {"studyInstanceUid": "1", "accessionNumber": "SecondAccessionNumber"},
        ],
    }
    import_request = ImportStudyRequest(**data)
    assert len(import_request.studies) == 1
    assert import_request.studies[0].study_instance_uid == "1"
    assert import_request.studies[0].accession_number == "SecondAccessionNumber"
