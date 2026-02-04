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

import pytest

from flip_api.config import get_settings


@pytest.mark.skip
def test_retrieve_model_files_integration(real_client, create_project_data, create_model_data):
    """Integration test for retrieving model files list."""

    response = real_client.get(f"{get_settings().FLIP_API_URL}files/model/{create_model_data.id}/files")

    assert response.status_code == 200
    data = response.json()
    assert "files" in data
    assert data["files"]["algo"] == f"{create_model_data.id}/algo/monaialgo.py"
    assert data["files"]["opener"] == f"{create_model_data.id}/opener/monaiopener.py"
    assert data["files"]["model"] == f"{create_model_data.id}/model/monai-test.pth.tar"
