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

import uuid

import pytest

from flip_api.db.models.main_models import UploadedFiles


@pytest.fixture
def db_files():
    """Create sample files for testing."""
    model_id = uuid.uuid4()
    file1 = UploadedFiles(
        id=uuid.uuid4(),
        name="test_file1.txt",
        size=1024,
        type="text/plain",
        status="PROCESSED",
        model_id=model_id,
    )
    file2 = UploadedFiles(
        id=uuid.uuid4(),
        name="test_file2.csv",
        size=2048,
        type="text/csv",
        status="PROCESSED",
        model_id=model_id,
    )
    return [file1, file2], model_id


@pytest.mark.skip
def test_get_model_files_list_integration(client, db_files):
    """Integration test for the model files list endpoint."""
    files, model_id = db_files

    # Setup the mock to return some test files
    for file in files:
        UploadedFiles(
            id=file.id,
            name=file.name,
            size=file.size,
            type=file.type,
            status=file.status,
            model_id=file.model_id,
        )
    # Ensure the model ID is valid
    assert model_id is not None

    response = client.get(f"/model/{model_id}/files")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "integration_test_file.txt"
    assert data[0]["modelId"] == str(model_id)
