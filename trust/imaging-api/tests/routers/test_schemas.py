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

from imaging_api.routers.schemas import CentralHubProject, ImportStudyRequest


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
