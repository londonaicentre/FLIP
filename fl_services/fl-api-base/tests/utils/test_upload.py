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

from unittest.mock import MagicMock, patch

import pytest

from fl_api.utils.schemas import UploadAppRequest
from fl_api.utils.upload import upload_application, validate_config

TEST_MODEL_ID = "c7f72374-0752-473f-a28f-592e4d8b7a47"
TMP_PATH_UPLOAD_DIR = "/tmp/tests/_upload_dir"


# Tests in upload_app.
@pytest.fixture
def mock_upload_correct_request():
    base = f"https://test.local/bundles/{TEST_MODEL_ID}"
    return UploadAppRequest(
        bundle_urls=[
            f"{base}/app/config/config_fed_server.json",
            f"{base}/app/config/config_fed_client.json",
            f"{base}/app/custom/config.json",
            f"{base}/app/custom/trainer.py",
            f"{base}/app/custom/validator.py",
            f"{base}/app/custom/models.py",
        ],
        project_id="123456789",
        cohort_query="SELECT * FROM patients WHERE age > 60",
        trusts=["Trust_1", "Trust_2"],
    )


@pytest.fixture
def mock_upload_multiple_apps_request():
    base = f"https://test.local/bundles/{TEST_MODEL_ID}"

    return UploadAppRequest(
        bundle_urls=[
            # Default app
            f"{base}/app/config/config_fed_server.json",
            f"{base}/app/config/config_fed_client.json",
            f"{base}/app/custom/trainer.py",
            f"{base}/app/custom/models.py",
            # Per-trust app: Trust 1
            f"{base}/app-trust1/config/config_fed_server.json",
            f"{base}/app-trust1/config/config_fed_client.json",
            f"{base}/app-trust1/custom/trainer.py",
            f"{base}/app-trust1/custom/models.py",
            # Per-trust app: Trust 2
            f"{base}/app-trust2/config/config_fed_server.json",
            f"{base}/app-trust2/config/config_fed_client.json",
            f"{base}/app-trust2/custom/trainer.py",
            f"{base}/app-trust2/custom/models.py",
            # Meta file for multiple app folders
            f"{base}/meta.json",
        ],
        project_id="123456789",
        cohort_query="SELECT * FROM patients WHERE age > 60",
        trusts=["Trust_1", "Trust_2"],
    )


@pytest.fixture
def mock_upload_multiple_apps_request_missing_meta_json():
    base = f"https://test.local/bundles/{TEST_MODEL_ID}"

    return UploadAppRequest(
        bundle_urls=[
            # Default app
            f"{base}/app/config/config_fed_server.json",
            f"{base}/app/config/config_fed_client.json",
            f"{base}/app/custom/trainer.py",
            f"{base}/app/custom/models.py",
            # Per-trust app: Trust 1
            f"{base}/app-trust1/config/config_fed_server.json",
            f"{base}/app-trust1/config/config_fed_client.json",
            f"{base}/app-trust1/custom/trainer.py",
            f"{base}/app-trust1/custom/models.py",
            # Per-trust app: Trust 2
            f"{base}/app-trust2/config/config_fed_server.json",
            f"{base}/app-trust2/config/config_fed_client.json",
            f"{base}/app-trust2/custom/trainer.py",
            f"{base}/app-trust2/custom/models.py",
            # ❌ Intentionally missing:
            # f"{base}/meta.json"
        ],
        project_id="123456789",
        cohort_query="SELECT * FROM patients WHERE age > 60",
        trusts=["Trust_1", "Trust_2"],
    )


@pytest.fixture
def mock_requests_get_success():
    """
    Patches fl_api.utils.upload.requests.get so it always returns a response
    with `.content` (and optional `.raise_for_status`).
    """
    with patch("fl_api.utils.upload.requests.get") as mock_get:
        resp = MagicMock()
        resp.content = b"mock file content"
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        yield mock_get


def test_error_requests_get(
    mock_requests_get_success,
    mock_upload_correct_request,
):
    # Test when requests.get fails with an error
    mock_requests_get_success.side_effect = Exception("Network error")

    with pytest.raises(Exception, match="Network error") as exc_info:
        _ = upload_application(TEST_MODEL_ID, mock_upload_correct_request, TMP_PATH_UPLOAD_DIR)

    assert "Network error" in str(exc_info.value)


@patch("fl_api.utils.upload.configure_environment", MagicMock())
@patch("fl_api.utils.upload.configure_meta", MagicMock())
@patch("fl_api.utils.upload.configure_server", MagicMock())
@patch("fl_api.utils.upload.configure_client", MagicMock())
@patch("fl_api.utils.upload.configure_config", MagicMock())
@patch("fl_api.utils.upload.validate_config", MagicMock())
@patch("fl_api.utils.upload.read_config", MagicMock())
def test_upload_app_success(
    mock_requests_get_success,
    mock_upload_correct_request,
):
    response = upload_application(TEST_MODEL_ID, mock_upload_correct_request, TMP_PATH_UPLOAD_DIR)

    assert "Application uploaded successfully" in response["message"]


@patch("fl_api.utils.upload.configure_environment", MagicMock())
@patch("fl_api.utils.upload.configure_meta", MagicMock())
@patch("fl_api.utils.upload.configure_server", MagicMock())
@patch("fl_api.utils.upload.configure_client", MagicMock())
@patch("fl_api.utils.upload.configure_config", MagicMock())
@patch("fl_api.utils.upload.validate_config", MagicMock())
@patch("fl_api.utils.upload.read_config", MagicMock())
def test_upload_multiple_apps_success(
    mock_requests_get_success,
    mock_upload_multiple_apps_request,
):
    response = upload_application(TEST_MODEL_ID, mock_upload_multiple_apps_request, TMP_PATH_UPLOAD_DIR)

    assert "Application uploaded successfully" in response["message"]


def test_upload_multiple_apps_missing_meta_json(
    mock_requests_get_success,
    mock_upload_multiple_apps_request_missing_meta_json,
):
    with pytest.raises(FileNotFoundError) as exc_info:
        _ = upload_application(TEST_MODEL_ID, mock_upload_multiple_apps_request_missing_meta_json, TMP_PATH_UPLOAD_DIR)

    assert "Application must contain a meta.json file" in str(exc_info.value)


def test_validate_config_valid():
    valid_config = {
        "LOCAL_ROUNDS": 5,
        "GLOBAL_ROUNDS": 10,
        "IGNORE_RESULT_ERROR": True,
        "AGGREGATOR": "InTimeAccumulateWeightedAggregator",
        "AGGREGATION_WEIGHTS": {"client1": 1.0},
    }
    config = validate_config(valid_config)
    assert config.LOCAL_ROUNDS == 5
    assert config.GLOBAL_ROUNDS == 10
    assert config.IGNORE_RESULT_ERROR is True
    assert config.AGGREGATOR == "InTimeAccumulateWeightedAggregator"
    assert config.AGGREGATION_WEIGHTS == {"client1": 1.0}


def test_validate_config_invalid_type():
    with pytest.raises(ValueError, match="Provided config is not a valid dictionary"):
        validate_config("not-a-dict")


def test_validate_config_skips_invalid_rounds():
    config = {
        "LOCAL_ROUNDS": -1,
        "GLOBAL_ROUNDS": 0,
        "IGNORE_RESULT_ERROR": True,
        "AGGREGATOR": "InTimeAccumulateWeightedAggregator",
    }
    result = validate_config(config)
    assert result.LOCAL_ROUNDS is None
    assert result.GLOBAL_ROUNDS is None


def test_validate_config_invalid_aggregator():
    invalid_config = {
        "LOCAL_ROUNDS": 5,
        "GLOBAL_ROUNDS": 10,
        "AGGREGATOR": "invalid_agg",
    }
    with pytest.raises(ValueError, match="Unknown aggregator: invalid_agg"):
        validate_config(invalid_config)


def test_validate_config_invalid_weights():
    bad_weights = {
        "AGGREGATION_WEIGHTS": {"client1": "not-a-number"},
        "AGGREGATOR": "InTimeAccumulateWeightedAggregator",
    }
    with pytest.raises(ValueError, match="Invalid weight"):
        validate_config(bad_weights)
