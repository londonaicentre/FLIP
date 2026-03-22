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
E2E tests for model creation and training file upload.
Tests the model management workflow on an approved project.
"""

import pytest
import requests

from flip_api.domain.schemas.projects import ProjectDetails
from flip_api.utils.constants import BASE_URL
from tests.debug_prelaunch_task import (
    add_project_query,
    create_new_project,
    submit_query_to_trusts,
)
from tests.integration.utils import admin_authentication


@pytest.mark.e2e
class TestModelTrainingSetup:
    """Test model creation and training file upload on an approved project."""

    def _create_approved_project(self, authed_client, cohort_query_sql, trust_ids, cleanup_projects):
        """Helper to create a fully approved project."""
        project_data = ProjectDetails(
            name="E2E Test - Model Setup",
            description="Tests model creation and file upload",
            users=[],
        )
        response = create_new_project(authed_client, project_data)
        assert response is not None
        project_id = response.json()["id"]
        cleanup_projects.append(project_id)

        auth_token = admin_authentication()
        query_info = {
            "query": cohort_query_sql,
            "name": "E2E Model Test Query",
            "project_id": str(project_id),
        }
        query_response = add_project_query(authed_client, query_info)
        assert query_response is not None
        query_id = query_response.json()["query_id"]

        submit_info = {
            "authenticationToken": auth_token.get("authorization", ""),
            "query": cohort_query_sql,
            "name": "E2E Model Test Query",
            "project_id": str(project_id),
            "query_id": str(query_id),
        }
        submit_query_to_trusts(authed_client, submit_info)

        authed_client.post(
            f"{BASE_URL}/projects/{project_id}/stage/",
            json={"trusts": trust_ids},
            timeout=30,
        )

        approve_response = authed_client.post(
            f"{BASE_URL}/step/project/{project_id}/approve/",
            json={"trusts": trust_ids},
            timeout=60,
        )
        assert approve_response.status_code < 300
        return project_id

    def test_create_model_for_approved_project(self, authed_client, cohort_query_sql, trust_ids, cleanup_projects):
        """A model can be created for an approved project."""
        project_id = self._create_approved_project(authed_client, cohort_query_sql, trust_ids, cleanup_projects)

        model_payload = {
            "name": "E2E Test Classification Model",
            "description": "Automated E2E test model",
            "projectId": str(project_id),
        }

        response = authed_client.post(
            f"{BASE_URL}/model",
            json=model_payload,
            timeout=30,
        )

        assert response.status_code < 300, f"Failed to create model: {response.status_code} {response.text}"
        model_data = response.json()
        assert "id" in model_data, "Model response should contain ID"

    def test_upload_training_files(self, authed_client, cohort_query_sql, trust_ids, cleanup_projects):
        """Training files can be uploaded to a model via presigned S3 URLs."""
        project_id = self._create_approved_project(authed_client, cohort_query_sql, trust_ids, cleanup_projects)

        # Create model
        model_payload = {
            "name": "E2E Test Upload Model",
            "description": "Tests file upload via presigned URLs",
            "projectId": str(project_id),
        }
        model_response = authed_client.post(f"{BASE_URL}/model", json=model_payload, timeout=30)
        assert model_response.status_code < 300
        model_id = model_response.json()["id"]

        # Sample training files to upload
        sample_files = {
            "config.json": (b'{"model": "test", "epochs": 1}', "application/json"),
            "train.py": (b"import torch\nprint('training')\n", "text/x-python"),
        }

        uploaded_count = 0
        for filename, (content, content_type) in sample_files.items():
            # Get presigned URL
            presigned_response = authed_client.post(
                f"{BASE_URL}/files/preSignedUrl/model/{model_id}",
                json={"fileName": filename, "contentType": content_type},
                timeout=30,
            )
            assert presigned_response.status_code < 300, (
                f"Failed to get presigned URL for {filename}: {presigned_response.status_code}"
            )

            upload_url = presigned_response.json()
            assert upload_url, f"No presigned URL returned for {filename}"

            # Upload file to S3
            upload_response = requests.put(upload_url, data=content, timeout=60)
            assert upload_response.status_code < 300, (
                f"Failed to upload {filename}: {upload_response.status_code}"
            )
            uploaded_count += 1

        assert uploaded_count == len(sample_files), (
            f"Expected {len(sample_files)} uploads, got {uploaded_count}"
        )

        # Verify files are listed for the model
        files_response = authed_client.get(f"{BASE_URL}/files/model/{model_id}", timeout=30)
        assert files_response.status_code < 300, (
            f"Failed to list model files: {files_response.status_code} {files_response.text}"
        )
