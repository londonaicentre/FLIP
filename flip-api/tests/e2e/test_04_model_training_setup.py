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

from flip_api.utils.constants import BASE_URL
from tests.e2e.helpers import create_and_approve_project


@pytest.mark.e2e
class TestModelTrainingSetup:
    """Test model creation and training file upload on an approved project."""

    def test_create_model_for_approved_project(self, authed_client, cohort_query_sql, trust_ids, cleanup_projects):
        """A model can be created for an approved project."""
        project_id, _ = create_and_approve_project(
            authed_client, cohort_query_sql, trust_ids, cleanup_projects,
            project_name="E2E Test - Model Setup",
        )

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
        project_id, _ = create_and_approve_project(
            authed_client, cohort_query_sql, trust_ids, cleanup_projects,
            project_name="E2E Test - File Upload",
        )

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

            # Upload file to S3 (presigned URLs don't need auth headers)
            upload_response = requests.put(upload_url, data=content, timeout=60)
            assert upload_response.status_code < 300, (
                f"Failed to upload {filename}: {upload_response.status_code}"
            )
            uploaded_count += 1

        assert uploaded_count == len(sample_files), (
            f"Expected {len(sample_files)} uploads, got {uploaded_count}"
        )

        # Verify files are listed for the model
        files_response = authed_client.get(f"{BASE_URL}/files/model/{model_id}/get/files", timeout=30)
        assert files_response.status_code < 300, (
            f"Failed to list model files: {files_response.status_code} {files_response.text}"
        )
