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
from enum import Enum
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from flip_api.config import Settings
from flip_api.domain.interfaces import fl as fl_interfaces
from flip_api.domain.interfaces.fl import (
    IClientStatus,
    IJobMetaData,
    IServerStatus,
    IStartTrainingBody,
)
from flip_api.domain.schemas.status import ClientStatus
from flip_api.fl_services.services import fl_service


@pytest.fixture
def fake_session():
    return MagicMock()


@pytest.fixture
def model_id() -> UUID:
    return uuid4()


@pytest.fixture
def fl_job_id() -> UUID:
    return uuid4()


@pytest.fixture
def mocked_settings():
    mock = Settings(
        FL_APP_BASE_BUCKET="s3://mock-bucket-base-app/base_files",
        SCANNED_MODEL_FILES_BUCKET="s3://mock-bucket-scanned/model_files",
        FL_APP_DESTINATION_BUCKET="s3://mock-bucket-dest/dest_files",
    )
    with patch("flip_api.fl_services.services.fl_service.get_settings", return_value=mock):
        yield mock


@pytest.fixture
def mock_job_types_file(tmp_path, monkeypatch):
    """Mock the JSON job-types file and rebuild JobTypes from it."""
    job_types_config = {
        "standard": ["trainer.py", "validator.py", "models.py", "config.json"],
        "diffusion_model": ["trainer.py", "validator.py", "models.py", "config.json"],
        "fed_opt": ["trainer.py", "validator.py", "models.py", "config.json"],
        "evaluation": ["trainer.py", "validator.py", "models.py", "config.json"],
    }
    mock_file = tmp_path / "job_types_required_files.json"
    mock_file.write_text(json.dumps(job_types_config), encoding="utf-8")

    monkeypatch.setattr(fl_interfaces, "REQUIRED_JOB_TYPES_FILE", mock_file)
    monkeypatch.setattr(fl_interfaces, "_JOB_TYPES_CONFIG", fl_interfaces._load_job_types_config())
    monkeypatch.setattr(
        fl_interfaces,
        "JobTypes",
        Enum("JobTypes", {job_type: job_type for job_type in fl_interfaces._JOB_TYPES_CONFIG.keys()}),  # type: ignore[misc]
    )
    monkeypatch.setattr(fl_service, "JobTypes", fl_interfaces.JobTypes)

    return job_types_config


@patch("flip_api.fl_services.services.fl_service.http_post")
def test_upload_app_calls_http_post(mock_post, model_id):
    body = IStartTrainingBody(
        project_id="proj",
        cohort_query="query",
        trusts=["client1"],
        bundle_urls=["http://s3-presigned-url/file1", "http://s3-presigned-url/file2"],
    )
    mock_post.return_value = {"status": "ok"}
    result = fl_service.upload_app(model_id, body, "endpoint")
    assert result == {"status": "ok"}


def test_get_fl_backend_job_id_by_model_id(model_id, fake_session):
    result_proxy = MagicMock()
    result_proxy.one_or_none.return_value = "job-id"
    fake_session.exec.return_value = result_proxy
    job_id = fl_service.get_fl_backend_job_id_by_model_id(model_id, fake_session)
    assert job_id == "job-id"


def test_add_fl_backend_job_id_updates_db(fl_job_id, fake_session):
    job = MagicMock()
    result_proxy = MagicMock()
    result_proxy.scalar_one_or_none.return_value = job
    fake_session.execute.return_value = result_proxy

    fl_service.add_fl_backend_job_id(fl_job_id, str(uuid4()), fake_session)

    fake_session.commit.assert_called_once()


@patch("flip_api.fl_services.services.fl_service.http_post")
def test_submit_job_raises_when_no_job_id(mock_post, fl_job_id, model_id, fake_session):
    mock_post.return_value = ""
    with pytest.raises(ValueError, match="No backend job id returned"):
        fl_service.submit_job(fl_job_id, "endpoint", model_id, fake_session)


# TODO add tests for fetch_server_status, fetch_client_status
@patch("flip_api.fl_services.services.fl_service.check_server_status")
def test_fetch_server_status_success(mock_check_server):
    mock_check_server.return_value = IServerStatus(status="running")
    status = fl_service.fetch_server_status("endpoint")
    assert status.status == "running"


@patch("flip_api.fl_services.services.fl_service.check_client_status")
def test_fetch_client_status_success(mock_check_client):
    mock_check_client.return_value = [
        IClientStatus(name="Trust_1", status=ClientStatus.NO_JOBS.value),
        IClientStatus(name="Trust_2", status=ClientStatus.NO_REPLY.value),
    ]
    status = fl_service.fetch_client_status("endpoint")
    assert status[0].name == "Trust_1"
    assert status[0].status == ClientStatus.NO_JOBS.value
    assert status[1].name == "Trust_2"
    assert status[1].status == ClientStatus.NO_REPLY.value


def test_get_fl_backend_job_id_by_model_id_not_found(model_id, fake_session):
    result_proxy = MagicMock()
    result_proxy.one_or_none.return_value = None
    fake_session.exec.return_value = result_proxy
    with pytest.raises(ValueError, match=f"No backend job ID found for model_id {model_id}"):
        fl_service.get_fl_backend_job_id_by_model_id(model_id, fake_session)


def test_add_fl_backend_job_id_raises_if_job_missing(fl_job_id, fake_session):
    fake_session.get.return_value = None
    with pytest.raises(ValueError, match=f"FLJob with id {fl_job_id} not found"):
        fl_service.add_fl_backend_job_id(fl_job_id, str(uuid4()), fake_session)


@patch("flip_api.fl_services.services.fl_service.check_client_status")
def test_validate_client_availability_all_offline(mock_get_status):
    mock_get_status.return_value = [
        IClientStatus(name="Trust_2", status=ClientStatus.NO_REPLY.value),
        IClientStatus(name="Trust_1", status=ClientStatus.NO_JOBS.value),
    ]

    with pytest.raises(ValueError, match="Clients unavailable: trust-1"):
        fl_service.validate_client_availability(["trust-1"], "endpoint")


@patch("flip_api.fl_services.services.fl_service.check_client_status")
def test_validate_client_availability_some_online(mock_get_status):
    mock_get_status.return_value = [
        IClientStatus(name="Trust_2", status=ClientStatus.NO_REPLY.value),
        IClientStatus(name="Trust_1", status=ClientStatus.NO_JOBS.value),
    ]

    # This has to raise an error for Trust_2 only.
    with pytest.raises(ValueError, match="Clients unavailable: Trust_2"):
        fl_service.validate_client_availability(["Trust_2", "Trust_1"], "endpoint")


@patch("flip_api.fl_services.services.fl_service.check_client_status")
def test_validate_client_availability_empty_statuses(mock_get_status):
    mock_get_status.return_value = []

    with pytest.raises(ValueError, match="Unable to fetch client statuses"):
        fl_service.validate_client_availability(["trust-1"], "endpoint")


@patch("flip_api.fl_services.services.fl_service.http_delete")
def test_abort_job_success(mock_delete):
    mock_delete.return_value = {"status": "aborted"}
    result = fl_service.abort_job("endpoint", "job-id")
    assert result == {"status": "aborted"}


@patch("flip_api.fl_services.services.fl_service.submit_job")
@patch("flip_api.fl_services.services.fl_service.upload_app")
@patch("flip_api.fl_services.services.fl_service.encrypt")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_required_training_details")
def test_start_training_with_config(
    mock_get_required,
    mock_encrypt,
    mock_upload,
    mock_submit,
    model_id,
    fl_job_id,
    fake_session,
):
    mock_get_required.return_value = MagicMock(project_id="proj", cohort_query="query")
    mock_encrypt.return_value = "encrypted"

    fl_service.start_training(
        model_id=model_id,
        fl_job_id=fl_job_id,
        clients=["client1"],
        endpoint="endpoint",
        bundle_urls=["url"],
        session=fake_session,
    )
    mock_upload.assert_called_once()
    mock_submit.assert_called_once()


@patch("flip_api.fl_services.services.fl_service.verify_bundle_paths")
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_nvflare_application_success(mock_s3, mock_required, mock_verify, model_id, mocked_settings):
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
        [f"{base_bucket}/standard/app/file1.py"],
        [],  # Destination bucket
    ]
    mock_client.copy_object.return_value = None
    mock_client.object_exists.return_value = False  # No files exist yet
    mock_verify.return_value = None

    dest_bucket_s3_path = fl_service.bundle_nvflare_application(model_id)

    assert dest_bucket_s3_path == f"{dest_bucket}/{model_id}"

    # assert that the copy_object was called for each file including the bucket names
    mock_client.copy_object.assert_any_call(
        f"{base_bucket}/standard/app/file1.py",
        f"{dest_bucket}/{model_id}/app/file1.py",
    )
    mock_client.copy_object.assert_any_call(
        f"{model_bucket}/{model_id}/validator.py",
        f"{dest_bucket}/{model_id}/app/custom/validator.py",
    )


@patch("flip_api.fl_services.services.fl_service.verify_bundle_paths")
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
@patch("flip_api.fl_services.services.fl_service.logger")
def test_bundle_nvflare_application_model_files_overwrite(
    mock_logger, mock_s3, mock_required, mock_verify, model_id, mocked_settings
):
    """
    Test that if a file in the model files has the same name as a file in the base application, the model file is not
    copied and a warning is logged.
    """
    base_bucket = mocked_settings.FL_APP_BASE_BUCKET
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    mock_client = mock_s3.return_value
    # config.json with job_type standard
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps({"job_type": "standard"}).encode("utf-8")))
    }
    mock_required.return_value = ["trainer.py", "validator.py", "config.json"]
    # Model files and base files
    model_files = [
        f"{model_bucket}/{model_id}/trainer.py",
        f"{model_bucket}/{model_id}/validator.py",
        f"{model_bucket}/{model_id}/config.json",
        f"{model_bucket}/{model_id}/meta.json",
        f"{model_bucket}/{model_id}/flip.py",  # user trying to overwrite the flip.py in base with one in model files
    ]
    base_files = [
        f"{base_bucket}/standard/app/custom/flip.py",
        f"{base_bucket}/standard/app/config/config_fed_client.json",
    ]
    # Destination bucket is empty at first
    mock_client.list_objects.side_effect = [
        model_files,  # model bucket
        base_files,  # base bucket
        [],  # dest bucket (empty)
    ]
    mock_client.copy_object.return_value = None
    mock_verify.return_value = None

    # Simulate flip.py already exists when copying model files
    def object_exists_side_effect(key):
        if key.endswith("flip.py"):
            return True
        return False

    mock_client.object_exists.side_effect = object_exists_side_effect

    fl_service.bundle_nvflare_application(model_id)

    # Check that logger.warning was called with the message for the file
    mock_logger.warning.assert_any_call(
        "The file name flip.py is reserved for this base application, which contains a file with the same name. The "
        "researcher can't overwrite it. Skipping upload from model files."
    )

    # Verify flip.py was NOT copied from model files to destination
    model_flip_src_path = f"{model_bucket}/{model_id}/flip.py"
    model_flip_dst_path = f"{dest_bucket}/{model_id}/app/custom/flip.py"

    for call in mock_client.copy_object.call_args_list:
        args, _ = call
        if len(args) >= 2:
            assert not (args[0] == model_flip_src_path and args[1] == model_flip_dst_path), (
                "Model flip.py should not have been copied when destination already exists"
            )


@patch("flip_api.fl_services.services.fl_service.verify_bundle_paths")
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
def test_bundle_nvflare_application_file_wrong_job_type_in_config(
    mock_s3,
    mock_required,
    mock_verify,
    model_id,
    mocked_settings,
    job_type,
    mock_job_types_file,
):
    """
    Test that providing an invalid job type into the config.json raises an error while providing valid
    job types does not.

    Mocks the required files to be consistent with the job type provided in the config, so that the only reason for
    failure in the invalid case is the wrong job type.
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
        [f"{base_bucket}/{job_type}/app/file1.py"],
        [],  # Destination bucket
    ]
    mock_client.copy_object.return_value = None
    mock_client.object_exists.return_value = False  # No files exist yet
    mock_verify.return_value = None

    if job_type == "invalid":
        with pytest.raises(
            fl_service.UnknownJobTypeError, match=f"Unknown job_type argument found in config.json: {job_type}"
        ):
            _ = fl_service.bundle_nvflare_application(model_id)
    else:
        dest_bucket_s3_path = fl_service.bundle_nvflare_application(model_id)
        assert dest_bucket_s3_path == f"{mocked_settings.FL_APP_DESTINATION_BUCKET}/{model_id}"


@patch("flip_api.fl_services.services.fl_service.verify_bundle_paths")
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_nvflare_application_wrong_files(mock_s3, mock_required, mock_verify, mocked_settings, model_id):
    base_bucket = mocked_settings.FL_APP_BASE_BUCKET
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET

    mock_client = mock_s3.return_value
    # Provide an empty JSON config for tests that include config.json in model files
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps({}).encode("utf-8")))
    }
    mock_required.return_value = ["trainer.py", "validator.py", "models.py", "config.json"]
    mock_client.list_objects.side_effect = [
        [
            f"{model_bucket}/{model_id}/validator.py",
            f"{model_bucket}/{model_id}/models.py",
            f"{model_bucket}/{model_id}/config.json",
        ],  # Missing trainer.py
        [f"{base_bucket}/standard/app/file1.py"],
        [],  # Destination bucket
    ]
    mock_client.copy_object.return_value = None
    mock_verify.return_value = None

    with pytest.raises(FileNotFoundError, match="Missing required files for job type standard: trainer.py."):
        _ = fl_service.bundle_nvflare_application(model_id)


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_flower_application_success(mock_s3, mock_required, model_id, mocked_settings):
    base_bucket = mocked_settings.FL_APP_BASE_BUCKET
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    mock_client = mock_s3.return_value
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps({"job_type": "standard"}).encode("utf-8")))
    }
    mock_required.return_value = ["client_app.py", "models.py"]
    mock_client.list_objects.side_effect = [
        [
            f"{model_bucket}/{model_id}/client_app.py",
            f"{model_bucket}/{model_id}/models.py",
            f"{model_bucket}/{model_id}/config.json",
        ],
        [
            f"{base_bucket}/standard/app/server_app.py",
            f"{base_bucket}/standard/pyproject.toml",
        ],
        [],
    ]
    mock_client.copy_object.return_value = None
    mock_client.object_exists.return_value = False

    dest_bucket_s3_path = fl_service.bundle_flower_application(model_id)

    assert dest_bucket_s3_path == f"{dest_bucket}/{model_id}"
    mock_client.copy_object.assert_any_call(
        f"{base_bucket}/standard/app/server_app.py",
        f"{dest_bucket}/{model_id}/app/server_app.py",
    )
    mock_client.copy_object.assert_any_call(
        f"{model_bucket}/{model_id}/client_app.py",
        f"{dest_bucket}/{model_id}/app/client_app.py",
    )


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
@patch("flip_api.fl_services.services.fl_service.logger")
def test_bundle_flower_application_model_files_overwrite(
    mock_logger, mock_s3, mock_required, model_id, mocked_settings
):
    base_bucket = mocked_settings.FL_APP_BASE_BUCKET
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    mock_client = mock_s3.return_value
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps({"job_type": "standard"}).encode("utf-8")))
    }
    mock_required.return_value = ["client_app.py", "models.py"]
    model_files = [
        f"{model_bucket}/{model_id}/client_app.py",
        f"{model_bucket}/{model_id}/models.py",
        f"{model_bucket}/{model_id}/config.json",
        f"{model_bucket}/{model_id}/server_app.py",
    ]
    base_files = [
        f"{base_bucket}/standard/app/server_app.py",
        f"{base_bucket}/standard/pyproject.toml",
    ]
    mock_client.list_objects.side_effect = [
        model_files,
        base_files,
        [],
    ]
    mock_client.copy_object.return_value = None

    def object_exists_side_effect(key):
        if key.endswith("server_app.py"):
            return True
        return False

    mock_client.object_exists.side_effect = object_exists_side_effect

    fl_service.bundle_flower_application(model_id)

    mock_logger.warning.assert_any_call(
        "The file name server_app.py is reserved for this base application, which contains a file with the same "
        "name. The researcher can't overwrite it. Skipping upload from model files."
    )

    model_src_path = f"{model_bucket}/{model_id}/server_app.py"
    model_dst_path = f"{dest_bucket}/{model_id}/app/server_app.py"

    for call in mock_client.copy_object.call_args_list:
        args, _ = call
        if len(args) >= 2:
            assert not (args[0] == model_src_path and args[1] == model_dst_path), (
                "Model server_app.py should not have been copied when destination already exists"
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
def test_bundle_flower_application_file_wrong_job_type_in_config(
    mock_s3,
    mock_required,
    model_id,
    mocked_settings,
    job_type,
    mock_job_types_file,
):
    base_bucket = mocked_settings.FL_APP_BASE_BUCKET
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET

    mock_client = mock_s3.return_value
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps({"job_type": job_type}).encode("utf-8")))
    }
    mock_required.return_value = ["client_app.py", "models.py"]
    mock_client.list_objects.side_effect = [
        [
            f"{model_bucket}/{model_id}/client_app.py",
            f"{model_bucket}/{model_id}/models.py",
            f"{model_bucket}/{model_id}/config.json",
        ],
        [f"{base_bucket}/{job_type}/app/server_app.py"],
        [],
    ]
    mock_client.copy_object.return_value = None
    mock_client.object_exists.return_value = False

    if job_type == "invalid":
        with pytest.raises(
            fl_service.UnknownJobTypeError, match=f"Unknown job_type argument found in config.json: {job_type}"
        ):
            _ = fl_service.bundle_flower_application(model_id)
    else:
        dest_bucket_s3_path = fl_service.bundle_flower_application(model_id)
        assert dest_bucket_s3_path == f"{mocked_settings.FL_APP_DESTINATION_BUCKET}/{model_id}"


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_flower_application_wrong_files(mock_s3, mock_required, mocked_settings, model_id):
    base_bucket = mocked_settings.FL_APP_BASE_BUCKET
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET

    mock_client = mock_s3.return_value
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps({}).encode("utf-8")))
    }
    mock_required.return_value = ["client_app.py", "models.py"]
    mock_client.list_objects.side_effect = [
        [
            f"{model_bucket}/{model_id}/client_app.py",
            f"{model_bucket}/{model_id}/config.json",
        ],  # Missing models.py
        [f"{base_bucket}/standard/app/server_app.py"],
        [],
    ]
    mock_client.copy_object.return_value = None

    with pytest.raises(FileNotFoundError, match="Missing required files for job type standard: models.py."):
        _ = fl_service.bundle_flower_application(model_id)


def test_verify_bundle_paths_success(model_id, mocked_settings):
    base_bucket = mocked_settings.FL_APP_BASE_BUCKET
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    base_bucket_s3_path = f"{base_bucket}/standard"
    model_bucket_s3_path = f"{model_bucket}/{model_id}"
    dest_bucket_s3_path = f"{dest_bucket}/{model_id}"

    # Base files we copied 1:1 into destination
    base_files = [
        f"{base_bucket_s3_path}/app_site1/config/config_fed_client.json",
        f"{base_bucket_s3_path}/app_site1/custom/flip.py",
        f"{base_bucket_s3_path}/app_site2/config/config_fed_server.json",
        f"{base_bucket_s3_path}/app_site2/custom/flip.py",
    ]

    # Model files we copied into each app*/custom/, and meta.json once at root
    model_files = [
        f"{model_bucket_s3_path}/trainer.py",
        f"{model_bucket_s3_path}/validator.py",
        f"{model_bucket_s3_path}/config.json",
        f"{model_bucket_s3_path}/meta.json",
    ]

    app_folders = {"app_site1", "app_site2"}

    # What we expect in destination after bundling
    expected_dest_keys = set()

    # base mirrored
    for file in base_files:
        rel = file.replace(f"{base_bucket_s3_path}/", "", 1)
        expected_dest_keys.add(f"{dest_bucket_s3_path}/{rel}")

    # meta.json once
    expected_dest_keys.add(f"{dest_bucket_s3_path}/meta.json")

    # model files into each app/custom (skip meta.json)
    for file in model_files:
        rel = file.replace(f"{model_bucket_s3_path}/", "", 1)
        if rel == "meta.json":
            continue
        for app in app_folders:
            expected_dest_keys.add(f"{dest_bucket_s3_path}/{app}/custom/{rel}")

    mock_s3 = MagicMock()
    mock_s3.list_objects.return_value = list(expected_dest_keys)

    # Should not raise
    fl_service.verify_bundle_paths(
        s3=mock_s3,
        base_files=base_files,
        model_files=model_files,
        app_folders=app_folders,
        base_bucket_s3_path=base_bucket_s3_path,
        model_bucket_s3_path=model_bucket_s3_path,
        dest_bucket_s3_path=dest_bucket_s3_path,
    )

    mock_s3.list_objects.assert_called_once_with(dest_bucket_s3_path)


def test_verify_bundle_paths_raises_on_missing_file(model_id, mocked_settings):
    base_bucket = mocked_settings.FL_APP_BASE_BUCKET
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    base_bucket_s3_path = f"{base_bucket}/standard"
    model_bucket_s3_path = f"{model_bucket}/{model_id}"
    dest_bucket_s3_path = f"{dest_bucket}/{model_id}"

    base_files = [
        f"{base_bucket_s3_path}/app_site1/custom/flip.py",
    ]
    model_files = [
        f"{model_bucket_s3_path}/trainer.py",
        f"{model_bucket_s3_path}/meta.json",
    ]
    app_folders = {"app_site1"}

    # Build the full expected set (same logic as the helper)
    expected_dest_keys = set()

    for file in base_files:
        rel = file.replace(f"{base_bucket_s3_path}/", "", 1)
        expected_dest_keys.add(f"{dest_bucket_s3_path}/{rel}")

    expected_dest_keys.add(f"{dest_bucket_s3_path}/meta.json")

    for file in model_files:
        rel = file.replace(f"{model_bucket_s3_path}/", "", 1)
        if rel == "meta.json":
            continue
        for app in app_folders:
            expected_dest_keys.add(f"{dest_bucket_s3_path}/{app}/custom/{rel}")

    # Remove one expected key to simulate failed copy
    missing_key = next(iter(expected_dest_keys))
    actual_dest_keys = set(expected_dest_keys)
    actual_dest_keys.remove(missing_key)

    mock_s3 = MagicMock()
    mock_s3.list_objects.return_value = list(actual_dest_keys)

    with pytest.raises(RuntimeError, match=r"missing files"):
        fl_service.verify_bundle_paths(
            s3=mock_s3,
            base_files=base_files,
            model_files=model_files,
            app_folders=app_folders,
            base_bucket_s3_path=base_bucket_s3_path,
            model_bucket_s3_path=model_bucket_s3_path,
            dest_bucket_s3_path=dest_bucket_s3_path,
        )


@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_get_bundle_urls_success(mock_s3, mocked_settings, model_id):
    mock_client = mock_s3.return_value

    # build the expected path exactly like prod code
    expected_s3_path = f"{mocked_settings.FL_APP_DESTINATION_BUCKET}/{model_id}"

    files = [
        f"s3://dest/{model_id}/file1.csv",
        f"s3://dest/{model_id}/file2.csv",
    ]
    mock_client.list_objects.return_value = files
    mock_client.get_presigned_url.side_effect = [
        "https://dest/file1.csv",
        "https://dest/file2.csv",
    ]

    urls = fl_service.get_bundle_urls(expected_s3_path)

    assert urls == ["https://dest/file1.csv", "https://dest/file2.csv"]
    mock_client.list_objects.assert_called_once_with(expected_s3_path)
    mock_client.get_presigned_url.assert_any_call(files[0])
    mock_client.get_presigned_url.assert_any_call(files[1])


@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_get_bundle_urls_list_objects_failure(mock_s3, mocked_settings, model_id):
    mock_client = mock_s3.return_value
    mock_client.list_objects.side_effect = Exception("boom")

    # build the expected path exactly like prod code
    expected_s3_path = f"{mocked_settings.FL_APP_DESTINATION_BUCKET}/{model_id}"

    with pytest.raises(RuntimeError) as exc:
        fl_service.get_bundle_urls(expected_s3_path)

    # message contains context
    assert "Failed to list objects in S3 bucket" in str(exc.value)
    assert str(model_id) in str(exc.value)

    # presigning never attempted
    mock_client.get_presigned_url.assert_not_called()


@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_get_bundle_urls_presign_failure(mock_s3, mocked_settings, model_id):
    mock_client = mock_s3.return_value
    files = [
        f"s3://dest/{model_id}/file1.csv",
        f"s3://dest/{model_id}/file2.csv",
    ]
    mock_client.list_objects.return_value = files
    mock_client.get_presigned_url.side_effect = Exception("presign exploded")

    # build the expected path exactly like prod code
    expected_s3_path = f"{mocked_settings.FL_APP_DESTINATION_BUCKET}/{model_id}"

    with pytest.raises(RuntimeError) as exc:
        fl_service.get_bundle_urls(expected_s3_path)

    assert "Failed to generate presigned URLs" in str(exc.value)

    # list called once, presign attempted (it will stop on first exception)
    mock_client.list_objects.assert_called_once()
    mock_client.get_presigned_url.assert_called_once_with(files[0])


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_extract_current_job_data_success(mock_http_get):
    from flip_api.fl_services.services.fl_service import extract_current_job_data

    net_endpoint = "http://fl-api-endpoint"
    fl_backend_job_id = "job123"

    # Mock backend job list response
    mock_http_get.return_value = [
        {"job_id": "job123", "status": "RUNNING", "job_name": "myjob"},
        {"job_id": "job999", "status": "FINISHED", "job_name": "oldjob"},
    ]

    result = extract_current_job_data(net_endpoint, fl_backend_job_id)

    # Ensure the correct job was returned
    assert result.job_id == "job123"
    assert result.status == "RUNNING"

    # Verify correct HTTP endpoint called
    mock_http_get.assert_called_once_with(f"{net_endpoint}/list_jobs")


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_extract_current_job_data_not_found(mock_http_get):
    from flip_api.fl_services.services.fl_service import extract_current_job_data

    mock_http_get.return_value = [{"job_id": "other", "status": "RUNNING", "job_name": "otherjob"}]
    net_endpoint = "http://fl-api-endpoint"
    fl_backend_job_id = "missing-job"

    with pytest.raises(ValueError, match=f"Could not find job ID {fl_backend_job_id}"):
        extract_current_job_data(net_endpoint, fl_backend_job_id)


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_extract_current_job_data_multiple_found(mock_http_get):
    from flip_api.fl_services.services.fl_service import extract_current_job_data

    net_endpoint = "http://fl-api-endpoint"
    fl_backend_job_id = "duplicate-job"

    mock_http_get.return_value = [
        {"job_id": "duplicate-job", "status": "RUNNING", "job_name": "duplicate-job"},
        {"job_id": "duplicate-job", "status": "RUNNING", "job_name": "duplicate-job"},
    ]

    with pytest.raises(ValueError, match="Multiple running jobs found"):
        extract_current_job_data(net_endpoint, fl_backend_job_id)


@patch("flip_api.fl_services.services.fl_service.extract_current_job_data")
@patch("flip_api.fl_services.services.fl_service.get_fl_backend_job_id_by_model_id")
@patch("flip_api.fl_services.services.fl_service.fetch_server_status")
@patch("flip_api.fl_services.services.fl_service.abort_job")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_net_by_model_id")
@patch("flip_api.fl_services.services.fl_scheduler_service.remove_job_from_queue")
def test_abort_model_training_success(
    mock_remove,
    mock_get_net,
    mock_abort,
    mock_fetch_server_status,
    mock_get_fl_backend_job_id_by_model_id,
    mock_extract_current_job_data,
    model_id,
    fake_session,
):
    mock_get_fl_backend_job_id_by_model_id.return_value = "job123"
    mock_get_net.return_value = MagicMock(endpoint="http://fl-api-endpoint", name="net1")
    mock_fetch_server_status.return_value = {"status": "stopped"}
    mock_extract_current_job_data.return_value = IJobMetaData(job_id="job123", job_name=str(model_id), status="RUNNING")

    request = MagicMock()
    request.scope = {"request_id": "req-id"}
    request.path_params = {"target": "server", "clients": None}

    fl_service.abort_model_training(request, model_id, fake_session)
    mock_abort.assert_called_once_with("http://fl-api-endpoint", "job123")


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
