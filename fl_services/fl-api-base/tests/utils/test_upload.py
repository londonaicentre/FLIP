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

from unittest.mock import MagicMock, patch

import pytest

from fl_api.utils.schemas import UploadAppRequest
from fl_api.utils.upload import upload_application

TEST_MODEL_ID = "c7f72374-0752-473f-a28f-592e4d8b7a47"


# Tests in upload_app.
@pytest.fixture
def mock_upload_correct_request():
    return UploadAppRequest(
        bundle_urls=[
            "path/to/bundle/app/config/config_fed_server.json",
            "path/to/bundle/app/config/config_fed_client.json",
            "path/to/bundle/app/custom/config.json",
            "path/to/bundle/app/custom/trainer.py",
            "path/to/bundle/app/custom/validator.py",
            "path/to/bundle/app/custom/models.py",
        ],
        project_id="123456789",
        cohort_query="SELECT * FROM patients WHERE age > 60",
        local_rounds=10,
        global_rounds=3,
        trusts=["Trust_1", "Trust_2"],
    )


@pytest.fixture
def mock_upload_incorrect_request():
    # Missing trainer.py
    return UploadAppRequest(
        bundle_urls=[
            "path/to/bundle/app/config/config_fed_server.json",
            "path/to/bundle/app/config/config_fed_client.json",
            "path/to/bundle/app/custom/config.json",
            "path/to/bundle/app/custom/validator.py",
            "path/to/bundle/app/custom/",
        ],
        project_id="123456789",
        cohort_query="SELECT * FROM patients WHERE age > 60",
        local_rounds=10,
        global_rounds=3,
        trusts=["Trust_1", "Trust_2"],
    )


#            "path/to/bundle/meta.json",


@pytest.fixture
def mock_upload_multiple_apps_request():
    return UploadAppRequest(
        bundle_urls=[
            "path/to/bundle/app/config/config_fed_server.json",
            "path/to/bundle/app/config/config_fed_client.json",
            "path/to/bundle/app/custom/config.json",
            "path/to/bundle/app/custom/validator.py",
            "path/to/bundle/app/custom/trainer.py",
            "path/to/bundle/app/custom/models.py",
            "path/to/bundle/app-trust1/config/config_fed_server.json",
            "path/to/bundle/app-trust1/config/config_fed_client.json",
            "path/to/bundle/app-trust1/config/config.json",
            "path/to/bundle/app-trust1/config/validator.py",
            "path/to/bundle/app-trust1/custom/trainer.py",
            "path/to/bundle/app-trust1/custom/models.py",
            "path/to/bundle/app-trust2/config/config_fed_server.json",
            "path/to/bundle/app-trust2/config/config_fed_client.json",
            "path/to/bundle/app-trust2/custom/config.json",
            "path/to/bundle/app-trust2/custom/validator.py",
            "path/to/bundle/app-trust2/custom/trainer.py",
            "path/to/bundle/app-trust2/custom/models.py",
        ],
        project_id="123456789",
        cohort_query="SELECT * FROM patients WHERE age > 60",
        local_rounds=10,
        global_rounds=3,
        trusts=["Trust_1", "Trust_2"],
    )


@patch("builtins.open", MagicMock())
@patch("requests.get")
@patch("urllib.parse.urlparse")
@patch("os.makedirs", MagicMock())
@patch("os.path.abspath")
def test_error_requests_get(
    mock_abspath,
    mock_urlparse,
    mock_get_url,
    mock_upload_correct_request,
):
    mock_urlparse.return_value = MagicMock(path="/path/to/trainer.py")
    mock_abspath.return_value = "/absolute/path"

    # Test when requests.get fails with an error
    mock_get_url.side_effect = Exception("Network error")

    with pytest.raises(Exception, match="Network error") as exc_info:
        _ = upload_application(TEST_MODEL_ID, mock_upload_correct_request, "/path/to/upload_dir")
    assert "Network error" in str(exc_info.value)


@patch("builtins.open", MagicMock())
@patch("fl_api.utils.upload.configure_environment", MagicMock())
@patch("fl_api.utils.upload.configure_meta", MagicMock())
@patch("fl_api.utils.upload.configure_server", MagicMock())
@patch("fl_api.utils.upload.configure_client", MagicMock())
@patch("fl_api.utils.upload.configure_config", MagicMock())
@patch("requests.get")
@patch("urllib.parse.urlparse")
@patch("os.makedirs", MagicMock())
@patch("os.path.abspath")
def test_upload_correct_app_structure(
    mock_abspath,
    mock_urlparse,
    mock_get_url,
    mock_upload_correct_request,
):
    mock_abspath.return_value = "/absolute/path"
    mock_urlparse.return_value = MagicMock(path="/path/to/trainer.py")
    mock_get_url.return_value = MagicMock(content=b"mock file content")

    response = upload_application(TEST_MODEL_ID, mock_upload_correct_request, "/path/to/upload_dir")
    print(response)
    assert "Application uploaded successfully" in response["message"]


@patch("builtins.open", MagicMock())
@patch("fl_api.utils.upload.configure_environment", MagicMock())
@patch("fl_api.utils.upload.configure_meta", MagicMock())
@patch("fl_api.utils.upload.configure_server", MagicMock())
@patch("fl_api.utils.upload.configure_client", MagicMock())
@patch("fl_api.utils.upload.configure_config", MagicMock())
@patch("requests.get")
@patch("fl_api.utils.upload.os.path.exists")
@patch("urllib.parse.urlparse")
@patch("os.makedirs", MagicMock())
@patch("os.path.abspath")
def test_upload_multiple_app_structure(
    mock_abspath,
    mock_urlparse,
    mock_exists,
    mock_get_url,
    mock_upload_multiple_apps_request,
):
    def mock_exists_side_effect(path):
        # Simulate that meta.json does not exist
        if path.endswith("meta.json"):
            return False
        return True

    mock_upload_multiple_apps_request.bundle_urls = [
        "path/to/app/trainer.py",
        "path/to/app/validator.py",
        "path/to/app/models.py",
        "path/to/app/config.json",
        "path/to/app1/trainer.py",
        "path/to/app1/validator.py",
        "path/to/app1/models.py",
        "path/to/app1/config.json",
        "path/to/app2/trainer.py",
        "path/to/app2/validator.py",
        "path/to/app2/models.py",
        "path/to/app2/config.json",
        "path/to/meta.json",
    ]

    mock_abspath.return_value = "/absolute/path"
    mock_urlparse.side_effect = [MagicMock(path=url) for url in mock_upload_multiple_apps_request.bundle_urls]
    mock_get_url.return_value = MagicMock(content=b"mock file content")
    mock_exists.side_effect = mock_exists_side_effect

    with pytest.raises(FileNotFoundError) as exc_info:
        _ = upload_application(TEST_MODEL_ID, mock_upload_multiple_apps_request, "/path/to/upload_dir")

    assert "Application must contain a meta.json file" in str(exc_info.value)
