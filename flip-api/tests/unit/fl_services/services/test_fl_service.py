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

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from flip_api.config import Settings
from flip_api.domain.interfaces.fl import (
    IStartTrainingBody,
)
from flip_api.domain.schemas.status import ClientStatus
from flip_api.fl_services.services import fl_service


@pytest.fixture
def fake_session():
    return MagicMock()


@pytest.fixture
def model_id():
    return str(uuid4())


@pytest.fixture
def fl_job_id():
    return str(uuid4())


@pytest.fixture
def mocked_settings():
    mock = Settings(
        FL_APP_BASE_BUCKET="s3://mock-bucket-base-app/base_files",
        SCANNED_MODEL_FILES_BUCKET="s3://mock-bucket-scanned/model_files",
        FL_APP_DESTINATION_BUCKET="s3://mock-bucket-dest/dest_files",
    )
    with patch("flip_api.fl_services.services.fl_service.get_settings", return_value=mock):
        yield mock


def test_validate_config_valid():
    valid_config = {
        "LOCAL_ROUNDS": 5,
        "GLOBAL_ROUNDS": 10,
        "IGNORE_RESULT_ERROR": True,
        "AGGREGATOR": "InTimeAccumulateWeightedAggregator",
        "AGGREGATION_WEIGHTS": {"client1": 1.0},
    }
    config = fl_service.validate_config(valid_config)
    assert config.LOCAL_ROUNDS == 5
    assert config.GLOBAL_ROUNDS == 10
    assert config.IGNORE_RESULT_ERROR is True
    assert config.AGGREGATOR == "InTimeAccumulateWeightedAggregator"
    assert config.AGGREGATION_WEIGHTS == {"client1": 1.0}


def test_validate_config_invalid_type():
    with pytest.raises(ValueError, match="Provided config is not a valid dictionary"):
        fl_service.validate_config("not-a-dict")


def test_validate_config_skips_invalid_rounds():
    config = {
        "LOCAL_ROUNDS": -1,
        "GLOBAL_ROUNDS": 0,
        "IGNORE_RESULT_ERROR": True,
        "AGGREGATOR": "InTimeAccumulateWeightedAggregator",
    }
    result = fl_service.validate_config(config)
    assert result.LOCAL_ROUNDS is None
    assert result.GLOBAL_ROUNDS is None


def test_validate_config_invalid_aggregator():
    invalid_config = {
        "LOCAL_ROUNDS": 5,
        "GLOBAL_ROUNDS": 10,
        "AGGREGATOR": "invalid_agg",
    }
    with pytest.raises(ValueError, match="Unknown aggregator: invalid_agg"):
        fl_service.validate_config(invalid_config)


def test_validate_config_invalid_weights():
    bad_weights = {
        "AGGREGATION_WEIGHTS": {"client1": "not-a-number"},
        "AGGREGATOR": "InTimeAccumulateWeightedAggregator",
    }
    with pytest.raises(ValueError, match="Invalid weight"):
        fl_service.validate_config(bad_weights)


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_download_config_returns_config(mock_get, model_id):
    mock_get.return_value = {"LOCAL_ROUNDS": 1, "GLOBAL_ROUNDS": 1}
    with patch("flip_api.fl_services.services.fl_service.validate_config") as mock_validate:
        mock_validate.return_value = "validated_config"
        urls = [f"http://host/{model_id}/custom/config.json"]
        config = fl_service.download_config(urls, model_id)
        assert config == "validated_config"


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_download_config_no_config_found(mock_get, model_id):
    urls = [f"http://host/{model_id}/other/file.txt"]
    result = fl_service.download_config(urls, model_id)
    assert result is None


@patch("flip_api.fl_services.services.fl_service.http_post")
def test_upload_app_calls_http_post(mock_post, model_id):
    body = IStartTrainingBody(
        project_id="proj",
        cohort_query="query",
        local_rounds=1,
        global_rounds=2,
        trusts=["client1"],
        bundle_urls=["http://s3-presigned-url/file1", "http://s3-presigned-url/file2"],
        ignore_result_error=True,
        aggregator="default",
        aggregation_weights={"client1": 1.0},
    )
    mock_post.return_value = {"status": "ok"}
    result = fl_service.upload_app(model_id, body, "req-id", "endpoint")
    assert result == {"status": "ok"}


def test_get_nvflare_job_id_by_model_id(model_id, fake_session):
    result_proxy = MagicMock()
    result_proxy.one_or_none.return_value = "job-id"
    fake_session.exec.return_value = result_proxy
    job_id = fl_service.get_nvflare_job_id_by_model_id(model_id, fake_session)
    assert job_id == "job-id"


def test_add_nvflare_job_id_updates_db(fl_job_id, fake_session):
    job = MagicMock()
    result_proxy = MagicMock()
    result_proxy.scalar_one_or_none.return_value = job
    fake_session.execute.return_value = result_proxy

    fl_service.add_nvflare_job_id(fl_job_id, str(uuid4()), fake_session)

    fake_session.commit.assert_called_once()


@patch("flip_api.fl_services.services.fl_service.http_post")
def test_submit_job_raises_when_no_job_id(mock_post, fl_job_id, model_id, fake_session):
    mock_post.return_value = ""
    with pytest.raises(ValueError, match="No nvflare job id returned"):
        fl_service.submit_job("req-id", fl_job_id, "endpoint", model_id, fake_session)


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_get_status_with_clients(mock_get):
    details = MagicMock()
    details.target.value = "SERVER"
    details.clients = "client1"
    fl_service.get_status(details, "req-id", "endpoint")
    mock_get.assert_called_with("endpoint/check_status/SERVER/client1", "req-id")


def test_get_nvflare_job_id_by_model_id_not_found(model_id, fake_session):
    result_proxy = MagicMock()
    result_proxy.one_or_none.return_value = None
    fake_session.exec.return_value = result_proxy
    with pytest.raises(ValueError, match=f"No nvflare_job_id found for modelId: {model_id}"):
        fl_service.get_nvflare_job_id_by_model_id(model_id, fake_session)


def test_add_nvflare_job_id_raises_if_job_missing(fl_job_id, fake_session):
    fake_session.get.return_value = None
    with pytest.raises(ValueError, match=f"FLJob with id {fl_job_id} not found"):
        fl_service.add_nvflare_job_id(fl_job_id, str(uuid4()), fake_session)


@patch("flip_api.fl_services.services.fl_service.get_status")
def test_validate_client_availability_all_offline(mock_get_status):
    mock_get_status.return_value = [
        {"name": "Trust_2", "last_connect_time": "12345", "status": ClientStatus.NO_REPLY},
        {"name": "Trust_1", "last_connect_time": "12456", "status": ClientStatus.NO_JOBS},
    ]

    with pytest.raises(ValueError, match="Clients unavailable: trust-1"):
        fl_service.validate_client_availability(["trust-1"], "endpoint", "req-id")


@patch("flip_api.fl_services.services.fl_service.get_status")
def test_validate_client_availability_some_online(mock_get_status):
    mock_get_status.return_value = [
        {"name": "Trust_2", "last_connect_time": "12345", "status": ClientStatus.NO_REPLY},
        {"name": "Trust_1", "last_connect_time": "12456", "status": ClientStatus.NO_JOBS},
    ]

    # This has to raise an error for Trust_2 only.
    with pytest.raises(ValueError, match="Clients unavailable: Trust_2"):
        fl_service.validate_client_availability(["Trust_2", "Trust_1"], "endpoint", "req-id")


@patch("flip_api.fl_services.services.fl_service.get_status")
def test_validate_client_availability_empty_statuses(mock_get_status):
    mock_get_status.return_value = []

    with pytest.raises(ValueError, match="No clients are available"):
        fl_service.validate_client_availability(["trust-1"], "endpoint", "req-id")


@patch("flip_api.fl_services.services.fl_service.http_delete")
def test_abort_job_success(mock_delete):
    mock_delete.return_value = {"status": "aborted"}
    result = fl_service.abort_job("req-id", "endpoint", "job-id")
    assert result == {"status": "aborted"}


@patch("flip_api.fl_services.services.fl_service.submit_job")
@patch("flip_api.fl_services.services.fl_service.upload_app")
@patch("flip_api.fl_services.services.fl_service.download_config")
@patch("flip_api.fl_services.services.fl_service.add_log")
@patch("flip_api.fl_services.services.fl_service.encrypt")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_required_training_details")
def test_start_training_with_config(
    mock_get_required,
    mock_encrypt,
    mock_add_log,
    mock_download,
    mock_upload,
    mock_submit,
    model_id,
    fl_job_id,
    fake_session,
):
    mock_get_required.return_value = MagicMock(project_id="proj", cohort_query="query")
    mock_encrypt.return_value = "encrypted"
    mock_download.return_value = MagicMock(
        LOCAL_ROUNDS=2,
        GLOBAL_ROUNDS=2,
        IGNORE_RESULT_ERROR=True,
        AGGREGATOR=None,
        AGGREGATION_WEIGHTS=None,
    )

    fl_service.start_training(model_id, fl_job_id, ["client1"], "endpoint", ["url"], "req-id", fake_session)
    mock_upload.assert_called_once()
    mock_submit.assert_called_once()
    mock_add_log.assert_called()


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_application_success(mock_s3, mock_required, model_id, mocked_settings):
    base_bucket = mocked_settings.FL_APP_BASE_BUCKET
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    mock_client = mock_s3.return_value
    # Ensure get_object returns a body whose read() yields the config.json bytes
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps({"job_type": "standard"}).encode("utf-8")))
    }
    mock_required.return_value = ["trainer.py", "validator.py", "models.py", "config.json"]
    mock_client.list_objects.side_effect = [
        [
            f"{model_bucket}/{model_id}/validator.py",
            f"{model_bucket}/{model_id}/trainer.py",
            f"{model_bucket}/{model_id}/models.py",
            f"{model_bucket}/{model_id}/config.json",
        ],
        [f"{base_bucket}/src/standard/app/file1.py"],
        [],  # Destination bucket
    ]
    mock_client.copy_object.return_value = None
    count, job_type = fl_service.bundle_application(model_id)
    # base file + 4 model files => 5 unique filenames
    assert count == 5
    assert job_type.value == "standard"
    # assert that the copy_object was called for each file including the bucket names
    mock_client.copy_object.assert_any_call(
        f"{base_bucket}/src/standard/app/file1.py",
        f"{dest_bucket}/{model_id}/app/file1.py",
    )
    mock_client.copy_object.assert_any_call(
        f"{model_bucket}/{model_id}/validator.py",
        f"{dest_bucket}/{model_id}/app/custom/validator.py",
    )


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
@pytest.mark.parametrize(
    "job_type",
    [
        "standard",
        "diffusion_model",
        "fed_opt",
        "evaluation",
        "invalid",
    ],
)
def test_bundle_application_file_wrong_job_type_in_config(mock_s3, mock_required, model_id, mocked_settings, job_type):
    """Test that providing an invalid job type into the config.json raises an error while providing valid
    job types does not.
    """
    base_bucket = mocked_settings.FL_APP_BASE_BUCKET
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET

    mock_client = mock_s3.return_value
    # Return a config.json containing the job_type string for this parametrized run
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps({"job_type": job_type}).encode("utf-8")))
    }
    mock_required.return_value = ["trainer.py", "validator.py", "models.py", "config.json"]
    mock_client.list_objects.side_effect = [
        [
            f"{model_bucket}/{model_id}/validator.py",
            f"{model_bucket}/{model_id}/trainer.py",
            f"{model_bucket}/{model_id}/models.py",
            f"{model_bucket}/{model_id}/config.json",
        ],
        [f"{base_bucket}/src/{job_type}/app/file1.py"],
        [],  # Destination bucket
    ]
    mock_client.copy_object.return_value = None

    if job_type == "invalid":
        with pytest.raises(
            fl_service.UnknownJobTypeError, match=f"Unknown job_type argument found in config.json: {job_type}"
        ):
            _ = fl_service.bundle_application(model_id)
    else:
        count, returned_job_type = fl_service.bundle_application(model_id)
        # base file + 4 model files => 5 unique filenames
        assert count == 5
        assert returned_job_type.value == job_type


@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_application_wrong_files(mock_s3, model_id, mocked_settings):
    mock_client = mock_s3.return_value
    # Provide an empty JSON config for tests that include config.json in model files
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps({}).encode("utf-8")))
    }
    mock_client.list_objects.side_effect = [
        [f"s3://base/{model_id}/src/standard/file1.py"],
        [f"s3://model/{model_id}/validator.py", f"s3://model/{model_id}/config.json"],
        [],  # Destination bucket
    ]

    mock_client.copy_object.return_value = None
    with pytest.raises(FileNotFoundError, match="Missing required files for job type standard: trainer.py."):
        _ = fl_service.bundle_application(model_id)


@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_get_bundle_urls_retry_success(mock_s3, mocked_settings, model_id):
    mock_client = mock_s3.return_value
    mock_client.list_objects.return_value = [
        f"s3://dest/{model_id}/file1.csv",
        f"s3://dest/{model_id}/file2.csv",
    ]
    mock_client.get_presigned_url.side_effect = [
        "https://dest/file1.csv",
        "https://dest/file2.csv",
    ]
    urls = fl_service.get_bundle_urls(model_id, expected_count=2)
    assert len(urls) == 2
    # check they contain the FL_APP_DESTINATION_BUCKET
    assert all(url.startswith("https://dest/") for url in urls)


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_extract_current_job_data_success(mock_http_get):
    from flip_api.fl_services.services.fl_service import extract_current_job_data

    net_endpoint = "http://flare-endpoint"
    nvflare_job_id = "job123"

    # Mock NVFlare job list response
    mock_http_get.return_value = [
        {"job_id": "job123", "status": "RUNNING", "job_name": "myjob"},
        {"job_id": "job999", "status": "FINISHED", "job_name": "oldjob"},
    ]

    result = extract_current_job_data(net_endpoint, nvflare_job_id)

    # Ensure the correct job was returned
    assert result["job_id"] == "job123"
    assert result["status"] == "RUNNING"

    # Verify correct HTTP endpoint called
    mock_http_get.assert_called_once_with(f"{net_endpoint}/list_jobs")


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_extract_current_job_data_not_found(mock_http_get):
    from flip_api.fl_services.services.fl_service import extract_current_job_data

    mock_http_get.return_value = [{"job_id": "other", "status": "RUNNING"}]
    net_endpoint = "http://flare-endpoint"
    nvflare_job_id = "missing-job"

    with pytest.raises(ValueError, match=f"Could not find job ID {nvflare_job_id}"):
        extract_current_job_data(net_endpoint, nvflare_job_id)


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_extract_current_job_data_multiple_found(mock_http_get):
    from flip_api.fl_services.services.fl_service import extract_current_job_data

    net_endpoint = "http://flare-endpoint"
    nvflare_job_id = "duplicate-job"

    mock_http_get.return_value = [
        {"job_id": "duplicate-job", "status": "RUNNING"},
        {"job_id": "duplicate-job", "status": "RUNNING"},
    ]

    with pytest.raises(ValueError, match="Multiple running jobs found"):
        extract_current_job_data(net_endpoint, nvflare_job_id)


@patch("flip_api.fl_services.services.fl_service.extract_current_job_data")
@patch("flip_api.fl_services.services.fl_service.get_nvflare_job_id_by_model_id")
@patch("flip_api.fl_services.services.fl_service.fetch_server_status")
@patch("flip_api.fl_services.services.fl_service.abort_job")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_net_by_model_id")
@patch("flip_api.fl_services.services.fl_scheduler_service.remove_job_from_queue")
def test_abort_model_training_success(
    mock_remove,
    mock_get_net,
    mock_abort,
    mock_fetch_server_status,
    mock_get_nvflare_job_id_by_model_id,
    mock_extract_current_job_data,
    model_id,
    fake_session,
):
    mock_get_nvflare_job_id_by_model_id.return_value = "job123"
    mock_get_net.return_value = MagicMock(endpoint="http://endpoint", name="net1")
    mock_fetch_server_status.return_value = {"status": "stopped", "start_time": 1760009291.0072687}
    mock_extract_current_job_data.return_value = {
        "job_id": "job123",
        "job_name": str(model_id),
        "status": "RUNNING",
        "submit_time": "2025-10-08T14:25:47.119547+00:00",
        "duration": "2:38:26.597705",
    }

    request = MagicMock()
    request.scope = {"request_id": "req-id"}
    request.path_params = {"target": "server", "clients": None}

    fl_service.abort_model_training(request, model_id, fake_session)
    mock_abort.assert_called_once_with("req-id", "http://endpoint", "job123")


def test_add_fl_job_creates_job(model_id, fake_session):
    clients = ["client1", "client2"]
    fl_service.add_fl_job(model_id, clients, fake_session)
    fake_session.add.assert_called_once()
    fake_session.commit.assert_called_once()
    fake_session.refresh.assert_called_once()


def test_add_fl_job_rollback_on_exception(model_id):
    fake_session = MagicMock()
    fake_session.add.side_effect = Exception("DB Error")
    with pytest.raises(Exception, match="DB Error"):
        fl_service.add_fl_job(model_id, ["client1"], fake_session)
    fake_session.rollback.assert_called_once()
