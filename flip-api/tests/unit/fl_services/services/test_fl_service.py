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
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from flip_api.config import Settings
from flip_api.db.models.main_models import Trust
from flip_api.domain.interfaces.fl import (
    IClientStatus,
    IJobMetaData,
    IServerStatus,
    IStartTrainingBody,
)
from flip_api.domain.schemas.status import ClientStatus, JobStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.fl_services.services import fl_service
from flip_api.utils.exceptions import DatabaseError, JobAbortedError, NotFoundError


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
def mocked_settings(tmp_path):
    # FL_APP_BASE_DIR points at a per-test temp tree; individual bundler tests populate it via
    # write_base_tree(). The base application templates are read from local disk (FLIP#724), not S3.
    mock = Settings(
        FL_APP_BASE_DIR=str(tmp_path / "fl-apps"),
        SCANNED_MODEL_FILES_BUCKET="s3://mock-bucket-scanned/model_files",
        FL_APP_DESTINATION_BUCKET="s3://mock-bucket-dest/dest_files",
    )
    with patch("flip_api.fl_services.services.fl_service.get_settings", return_value=mock):
        yield mock


def write_base_tree(base_dir: str, backend: str, job_type: str, rel_files: list[str]) -> None:
    """Create a local base-application tree at <base_dir>/<backend>/<job_type>/ with stub files.

    Mirrors how the repo's fl-apps/ templates are laid out on disk, so the bundler's local walk
    has real files to enumerate and upload.

    Args:
        base_dir (str): The FL_APP_BASE_DIR root.
        backend (str): Backend segment (``nvflare`` or ``flower``).
        job_type (str): Job-type segment (``standard``, ``evaluation``, ...).
        rel_files (list[str]): Relative file paths to create under the job-type root.
    """
    root = Path(base_dir) / backend / job_type
    for rel in rel_files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# stub for {rel}\n")


@pytest.fixture
def mock_job_types_file():
    """Provide the set of valid job types for the parametrized bundler tests.

    Job types are data (the per-backend manifest keys), not an enum. The bundler tests drive
    ``JobRequiredFiles.is_valid_job_type`` from this mapping so a name absent from it (e.g.
    "invalid") is rejected with ``UnknownJobTypeError``.
    """
    return {
        "standard": ["trainer.py", "validator.py", "models.py", "config.json"],
        "diffusion_model": ["trainer.py", "validator.py", "models.py", "config.json"],
        "fed_opt": ["trainer.py", "validator.py", "models.py", "config.json"],
        "evaluation": ["trainer.py", "validator.py", "models.py", "config.json"],
    }


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
    fake_session.get.return_value = job

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
        IClientStatus(name="Trust_1", status=ClientStatus.NO_JOBS),
        IClientStatus(name="Trust_2", status=ClientStatus.NO_REPLY),
    ]
    status = fl_service.fetch_client_status("endpoint")
    assert status[0].name == "Trust_1"
    assert status[0].status == ClientStatus.NO_JOBS
    assert status[1].name == "Trust_2"
    assert status[1].status == ClientStatus.NO_REPLY


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
    # The backend is passed in explicitly now (resolved per-net at runtime), not read from settings.
    mock_get_status.return_value = [
        IClientStatus(name="Trust_2", status=ClientStatus.NO_REPLY),
        IClientStatus(name="Trust_1", status=ClientStatus.NO_JOBS),
    ]

    with pytest.raises(ValueError, match="Clients unavailable: trust-1"):
        fl_service.validate_client_availability(["trust-1"], "endpoint", FLBackend.NVFLARE)


@patch("flip_api.fl_services.services.fl_service.check_client_status")
def test_validate_client_availability_some_online(mock_get_status):
    mock_get_status.return_value = [
        IClientStatus(name="Trust_2", status=ClientStatus.NO_REPLY),
        IClientStatus(name="Trust_1", status=ClientStatus.NO_JOBS),
    ]

    # This has to raise an error for Trust_2 only.
    with pytest.raises(ValueError, match="Clients unavailable: Trust_2"):
        fl_service.validate_client_availability(["Trust_2", "Trust_1"], "endpoint", FLBackend.NVFLARE)


@patch("flip_api.fl_services.services.fl_service.check_client_status")
def test_validate_client_availability_empty_statuses(mock_get_status):
    mock_get_status.return_value = []

    with pytest.raises(ValueError, match="Unable to fetch client statuses"):
        fl_service.validate_client_availability(["trust-1"], "endpoint", FLBackend.NVFLARE)


@patch("flip_api.fl_services.services.fl_service.check_client_status")
def test_validate_client_availability_flower_soft_on_empty(mock_get_status):
    """Flower backend: empty client statuses logs warning instead of raising."""
    mock_get_status.return_value = []

    # Should NOT raise — Flower degrades gracefully
    fl_service.validate_client_availability(["Trust_1"], "endpoint", FLBackend.FLOWER)


@patch("flip_api.fl_services.services.fl_service.check_client_status")
def test_validate_client_availability_flower_soft_on_unavailable(mock_get_status):
    """Flower backend: unavailable clients logs warning instead of raising."""
    mock_get_status.return_value = [
        IClientStatus(name="Trust_1", status=ClientStatus.DISCONNECTED),
    ]

    # Should NOT raise — Flower degrades gracefully
    fl_service.validate_client_availability(["Trust_1"], "endpoint", FLBackend.FLOWER)


@patch("flip_api.fl_services.services.fl_service.check_client_status")
def test_validate_client_availability_nvflare_still_raises(mock_get_status):
    """NVFLARE backend: empty client statuses still raises ValueError."""
    mock_get_status.return_value = []

    with pytest.raises(ValueError, match="Unable to fetch client statuses"):
        fl_service.validate_client_availability(["Trust_1"], "endpoint", FLBackend.NVFLARE)


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


@patch("flip_api.fl_services.services.fl_service.submit_job")
@patch("flip_api.fl_services.services.fl_service.upload_app")
@patch("flip_api.fl_services.services.fl_service.encrypt")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_required_training_details")
def test_start_training_skips_upload_when_job_already_deleted(
    mock_get_required,
    mock_encrypt,
    mock_upload,
    mock_submit,
    model_id,
    fl_job_id,
    fake_session,
):
    # A concurrent abort DELETEd the job before the prepare thread reached the upload: the gate
    # must fire before the (up to 900s) app transfer even starts.
    mock_get_required.return_value = MagicMock(project_id="proj", cohort_query="query")
    mock_encrypt.return_value = "encrypted"
    fake_session.exec.return_value.one_or_none.return_value = JobStatus.DELETED

    with pytest.raises(JobAbortedError):
        fl_service.start_training(
            model_id=model_id,
            fl_job_id=fl_job_id,
            clients=["client1"],
            endpoint="endpoint",
            bundle_urls=["url"],
            session=fake_session,
        )

    mock_upload.assert_not_called()
    mock_submit.assert_not_called()


@patch("flip_api.fl_services.services.fl_service.submit_job")
@patch("flip_api.fl_services.services.fl_service.upload_app")
@patch("flip_api.fl_services.services.fl_service.encrypt")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_required_training_details")
def test_start_training_skips_submit_when_job_deleted_during_upload(
    mock_get_required,
    mock_encrypt,
    mock_upload,
    mock_submit,
    model_id,
    fl_job_id,
    fake_session,
):
    # The abort landed while upload_app was in flight: the second gate must stop the job from
    # being submitted to the fl-server (submit_job is the side effect that creates the backend
    # run and stamps fl_backend_job_id onto the DELETED row).
    mock_get_required.return_value = MagicMock(project_id="proj", cohort_query="query")
    mock_encrypt.return_value = "encrypted"
    fake_session.exec.return_value.one_or_none.side_effect = [JobStatus.IN_PROGRESS, JobStatus.DELETED]

    with pytest.raises(JobAbortedError):
        fl_service.start_training(
            model_id=model_id,
            fl_job_id=fl_job_id,
            clients=["client1"],
            endpoint="endpoint",
            bundle_urls=["url"],
            session=fake_session,
        )

    mock_upload.assert_called_once()
    mock_submit.assert_not_called()


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.is_valid_job_type", return_value=True)
@patch("flip_api.fl_services.services.fl_service.verify_bundle_paths")
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_nvflare_application_success(
    mock_s3, mock_required, mock_verify, mock_is_valid, model_id, mocked_settings
):
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    # Base application template on the local FL_APP_BASE_DIR tree
    write_base_tree(base_dir, "nvflare", "standard", ["app/file1.py"])

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
        [],  # Destination bucket (clear check)
    ]
    mock_client.copy_object.return_value = None
    mock_client.object_exists.return_value = False  # No files exist yet
    mock_verify.return_value = None

    dest_bucket_s3_path = fl_service.bundle_nvflare_application(model_id)

    assert dest_bucket_s3_path == f"{dest_bucket}/{model_id}"

    # Base files are uploaded from the local tree into the destination bundle
    mock_client.upload_file.assert_any_call(
        str(Path(base_dir) / "nvflare/standard/app/file1.py"),
        f"{dest_bucket}/{model_id}/app/file1.py",
    )
    # User model files are copied (S3 -> S3) into each app*/custom/
    mock_client.copy_object.assert_any_call(
        f"{model_bucket}/{model_id}/validator.py",
        f"{dest_bucket}/{model_id}/app/custom/validator.py",
    )


@pytest.mark.parametrize("job_type", ["evaluation", "evaluation_client_api"])
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.is_valid_job_type", return_value=True)
@patch("flip_api.fl_services.services.fl_service.verify_bundle_paths")
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_nvflare_application_diverts_eval_checkpoint(
    mock_s3, mock_required, mock_verify, mock_is_valid, job_type, model_id, mocked_settings
):
    """Evaluation checkpoints (legacy and Client-API job types) are copied once to a server-only
    ``server_checkpoints/`` prefix, NOT into any ``app*/custom/`` — so NVFLARE's deploy_map never
    ships them to clients."""
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    write_base_tree(base_dir, "nvflare", job_type, ["app/custom/flip.py"])

    eval_config = {
        "job_type": job_type,
        "models": {"arkplus": {"checkpoint": "weights.pt", "path": "ArkPlus"}},
    }
    mock_client = mock_s3.return_value
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps(eval_config).encode("utf-8")))
    }
    mock_required.return_value = ["config.json", "evaluator.py"]
    mock_client.list_objects.side_effect = [
        [
            f"{model_bucket}/{model_id}/config.json",
            f"{model_bucket}/{model_id}/evaluator.py",
            f"{model_bucket}/{model_id}/weights.pt",
        ],
        [],  # destination bucket empty (clear check)
    ]
    mock_client.copy_object.return_value = None
    mock_client.object_exists.return_value = False
    mock_verify.return_value = None

    fl_service.bundle_nvflare_application(model_id)

    # Checkpoint diverted to the server-only prefix (copied once, not per app folder).
    mock_client.copy_object.assert_any_call(
        f"{model_bucket}/{model_id}/weights.pt",
        f"{dest_bucket}/{model_id}/server_checkpoints/weights.pt",
    )
    # Checkpoint must NOT be copied into any app*/custom/.
    for call in mock_client.copy_object.call_args_list:
        args, _ = call
        if len(args) >= 2:
            assert not args[1].endswith("/custom/weights.pt"), "checkpoint must not be bundled into custom/"
    # Non-checkpoint model files still land in app/custom/.
    mock_client.copy_object.assert_any_call(
        f"{model_bucket}/{model_id}/evaluator.py",
        f"{dest_bucket}/{model_id}/app/custom/evaluator.py",
    )
    # verify_bundle_paths is told which files were diverted.
    _, verify_kwargs = mock_verify.call_args
    assert verify_kwargs["server_checkpoints"] == {"weights.pt"}


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.is_valid_job_type", return_value=True)
@patch("flip_api.fl_services.services.fl_service.verify_bundle_paths")
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_nvflare_application_diverts_standard_server_checkpoint(
    mock_s3, mock_required, mock_verify, mock_is_valid, model_id, mocked_settings
):
    """A training (standard) job declaring top-level SERVER_CHECKPOINT diverts that file to the
    server-only ``server_checkpoints/`` prefix (loaded server-side, broadcast round-0), NOT into
    any ``app*/custom/`` — so the large backbone never ships to clients via deploy_map."""
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    write_base_tree(base_dir, "nvflare", "standard", ["app/custom/flip.py"])

    std_config = {"job_type": "standard", "SERVER_CHECKPOINT": "pretrained_weights.pt"}
    mock_client = mock_s3.return_value
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps(std_config).encode("utf-8")))
    }
    mock_required.return_value = ["trainer.py", "validator.py", "config.json", "models.py"]
    mock_client.list_objects.side_effect = [
        [
            f"{model_bucket}/{model_id}/trainer.py",
            f"{model_bucket}/{model_id}/validator.py",
            f"{model_bucket}/{model_id}/config.json",
            f"{model_bucket}/{model_id}/models.py",
            f"{model_bucket}/{model_id}/pretrained_weights.pt",
        ],
        [],  # destination empty (clear check)
    ]
    mock_client.copy_object.return_value = None
    mock_client.object_exists.return_value = False
    mock_verify.return_value = None

    fl_service.bundle_nvflare_application(model_id)

    # Backbone diverted to the server-only prefix (once), not into app/custom/.
    mock_client.copy_object.assert_any_call(
        f"{model_bucket}/{model_id}/pretrained_weights.pt",
        f"{dest_bucket}/{model_id}/server_checkpoints/pretrained_weights.pt",
    )
    for call in mock_client.copy_object.call_args_list:
        args, _ = call
        if len(args) >= 2:
            assert not args[1].endswith("/custom/pretrained_weights.pt"), "backbone must not be bundled into custom/"
    # Ordinary training files still land in app/custom/.
    mock_client.copy_object.assert_any_call(
        f"{model_bucket}/{model_id}/trainer.py",
        f"{dest_bucket}/{model_id}/app/custom/trainer.py",
    )
    _, verify_kwargs = mock_verify.call_args
    assert verify_kwargs["server_checkpoints"] == {"pretrained_weights.pt"}


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.is_valid_job_type", return_value=True)
@patch("flip_api.fl_services.services.fl_service.verify_bundle_paths")
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
@patch("flip_api.fl_services.services.fl_service.logger")
def test_bundle_nvflare_application_model_files_overwrite(
    mock_logger, mock_s3, mock_required, mock_verify, mock_is_valid, model_id, mocked_settings
):
    """
    Test that if a file in the model files has the same name as a file in the base application, the model file is not
    copied and a warning is logged.
    """
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    # Base template contains flip.py under app/custom — a name the researcher must not overwrite
    write_base_tree(base_dir, "nvflare", "standard", ["app/custom/flip.py", "app/config/config_fed_client.json"])

    mock_client = mock_s3.return_value
    # config.json with job_type standard
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps({"job_type": "standard"}).encode("utf-8")))
    }
    mock_required.return_value = ["trainer.py", "validator.py", "config.json"]
    # Model files
    model_files = [
        f"{model_bucket}/{model_id}/trainer.py",
        f"{model_bucket}/{model_id}/validator.py",
        f"{model_bucket}/{model_id}/config.json",
        f"{model_bucket}/{model_id}/meta.json",
        f"{model_bucket}/{model_id}/flip.py",  # user trying to overwrite the flip.py in base with one in model files
    ]
    # Destination bucket is empty at first
    mock_client.list_objects.side_effect = [
        model_files,  # model bucket
        [],  # dest bucket (empty, clear check)
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


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.is_valid_job_type")
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
    mock_is_valid,
    model_id,
    mocked_settings,
    job_type,
    mock_job_types_file,
):
    """
    Test that providing an invalid job type into the config.json raises an error while providing valid
    job types does not.

    Mocks the required files to be consistent with the job type provided in the config, so that the only reason for
    failure in the invalid case is the wrong job type. Validity is driven by the manifest keys
    (mock_job_types_file), so "invalid" is rejected.
    """
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET

    # A base template exists for the parametrized job_type (unused for the "invalid" run, which is
    # rejected before the base directory is ever walked).
    write_base_tree(base_dir, "nvflare", job_type, ["app/file1.py"])

    mock_is_valid.side_effect = lambda jt, backend: jt in mock_job_types_file
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
        [],  # Destination bucket (clear check)
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
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET

    write_base_tree(base_dir, "nvflare", "standard", ["app/file1.py"])

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
        [],  # Destination bucket (clear check)
    ]
    mock_client.copy_object.return_value = None
    mock_verify.return_value = None

    with pytest.raises(FileNotFoundError, match="Missing required files for job type standard: trainer.py."):
        _ = fl_service.bundle_nvflare_application(model_id)


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.is_valid_job_type", return_value=True)
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_flower_application_success(mock_s3, mock_required, mock_is_valid, model_id, mocked_settings):
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    write_base_tree(base_dir, "flower", "standard", ["app/server_app.py", "pyproject.toml"])

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
        [],  # Destination bucket (clear check)
    ]
    mock_client.copy_object.return_value = None
    mock_client.object_exists.return_value = False

    dest_bucket_s3_path = fl_service.bundle_flower_application(model_id)

    assert dest_bucket_s3_path == f"{dest_bucket}/{model_id}"
    # Base files are uploaded from the local tree (nested app/ and root pyproject.toml both mirror 1:1)
    mock_client.upload_file.assert_any_call(
        str(Path(base_dir) / "flower/standard/app/server_app.py"),
        f"{dest_bucket}/{model_id}/app/server_app.py",
    )
    mock_client.upload_file.assert_any_call(
        str(Path(base_dir) / "flower/standard/pyproject.toml"),
        f"{dest_bucket}/{model_id}/pyproject.toml",
    )
    # User model files are copied (S3 -> S3) into app/
    mock_client.copy_object.assert_any_call(
        f"{model_bucket}/{model_id}/client_app.py",
        f"{dest_bucket}/{model_id}/app/client_app.py",
    )


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.is_valid_job_type", return_value=True)
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
@patch("flip_api.fl_services.services.fl_service.logger")
def test_bundle_flower_application_model_files_overwrite(
    mock_logger, mock_s3, mock_required, mock_is_valid, model_id, mocked_settings
):
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    # Base template contains server_app.py — a name the researcher must not overwrite
    write_base_tree(base_dir, "flower", "standard", ["app/server_app.py", "pyproject.toml"])

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
    mock_client.list_objects.side_effect = [
        model_files,
        [],  # Destination bucket (clear check)
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


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.is_valid_job_type")
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
def test_bundle_flower_application_file_wrong_job_type_in_config(
    mock_s3,
    mock_required,
    mock_verify,
    mock_is_valid,
    model_id,
    mocked_settings,
    job_type,
    mock_job_types_file,
):
    """
    Test that providing an invalid job type into the config.json raises an error while providing valid
    job types does not.

    Mocks the required files to be consistent with the job type provided in the config, so that the only reason for
    failure in the invalid case is the wrong job type. Validity is driven by the manifest keys
    (mock_job_types_file), so "invalid" is rejected.
    """
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET

    # A base template exists for the parametrized job_type (unused for the "invalid" run, which is
    # rejected before the base directory is ever walked).
    write_base_tree(base_dir, "flower", job_type, ["app/file1.py"])

    mock_is_valid.side_effect = lambda jt, backend: jt in mock_job_types_file
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
        [],  # Destination bucket (clear check)
    ]
    mock_client.copy_object.return_value = None
    mock_client.object_exists.return_value = False  # No files exist yet
    mock_verify.return_value = None

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
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET

    write_base_tree(base_dir, "flower", "standard", ["app/server_app.py"])

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
        [],  # Destination bucket (clear check)
    ]
    mock_client.copy_object.return_value = None

    with pytest.raises(FileNotFoundError, match="Missing required files for job type standard: models.py."):
        _ = fl_service.bundle_flower_application(model_id)


def test_verify_bundle_paths_success(model_id, mocked_settings):
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    model_bucket_s3_path = f"{model_bucket}/{model_id}"
    dest_bucket_s3_path = f"{dest_bucket}/{model_id}"

    # Relative paths of the base files we uploaded 1:1 into destination
    base_rel_paths = [
        "app_site1/config/config_fed_client.json",
        "app_site1/custom/flip.py",
        "app_site2/config/config_fed_server.json",
        "app_site2/custom/flip.py",
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
    for rel in base_rel_paths:
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
        base_rel_paths=base_rel_paths,
        model_files=model_files,
        app_folders=app_folders,
        model_bucket_s3_path=model_bucket_s3_path,
        dest_bucket_s3_path=dest_bucket_s3_path,
    )

    mock_s3.list_objects.assert_called_once_with(dest_bucket_s3_path)


def test_verify_bundle_paths_raises_on_missing_file(model_id, mocked_settings):
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    model_bucket_s3_path = f"{model_bucket}/{model_id}"
    dest_bucket_s3_path = f"{dest_bucket}/{model_id}"

    base_rel_paths = [
        "app_site1/custom/flip.py",
    ]
    model_files = [
        f"{model_bucket_s3_path}/trainer.py",
        f"{model_bucket_s3_path}/meta.json",
    ]
    app_folders = {"app_site1"}

    # Build the full expected set (same logic as the helper)
    expected_dest_keys = set()

    for rel in base_rel_paths:
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
            base_rel_paths=base_rel_paths,
            model_files=model_files,
            app_folders=app_folders,
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
        {"job_id": "job123", "status": "RUNNING"},
        {"job_id": "job999", "status": "FINISHED"},
    ]

    result = extract_current_job_data(net_endpoint, fl_backend_job_id)

    # Ensure the correct job was returned
    assert result.job_id == "job123"
    assert result.status == "RUNNING"

    # Verify correct HTTP endpoint called (with the generous Flower-aware timeout)
    mock_http_get.assert_called_once_with(f"{net_endpoint}/list_jobs", timeout=30)


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_extract_current_job_data_not_found_returns_none(mock_http_get):
    from flip_api.fl_services.services.fl_service import extract_current_job_data

    mock_http_get.return_value = [{"job_id": "other", "status": "RUNNING"}]
    net_endpoint = "http://fl-api-endpoint"
    fl_backend_job_id = "missing-job"

    assert extract_current_job_data(net_endpoint, fl_backend_job_id) is None


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_extract_current_job_data_multiple_found(mock_http_get):
    from flip_api.fl_services.services.fl_service import extract_current_job_data

    net_endpoint = "http://fl-api-endpoint"
    fl_backend_job_id = "duplicate-job"

    mock_http_get.return_value = [
        {"job_id": "duplicate-job", "status": "RUNNING"},
        {"job_id": "duplicate-job", "status": "RUNNING"},
    ]

    with pytest.raises(ValueError, match="Multiple running jobs found"):
        extract_current_job_data(net_endpoint, fl_backend_job_id)


@patch("flip_api.fl_services.services.fl_service.extract_current_job_data")
@patch("flip_api.fl_services.services.fl_service.get_fl_backend_job_id_by_model_id")
@patch("flip_api.fl_services.services.fl_service.fetch_server_status")
@patch("flip_api.fl_services.services.fl_service.abort_job")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_net_by_model_id")
@patch("flip_api.fl_services.services.fl_scheduler_service.remove_job_from_queue")
@patch("flip_api.fl_services.services.fl_scheduler_service.release_scheduler_for_model")
def test_abort_model_training_success(
    mock_release,
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
    mock_extract_current_job_data.return_value = IJobMetaData(job_id="job123", status="RUNNING")

    request = MagicMock()
    request.scope = {"request_id": "req-id"}
    request.path_params = {"target": "server", "clients": None}

    fl_service.abort_model_training(request, model_id, fake_session)
    mock_abort.assert_called_once_with("http://fl-api-endpoint", "job123")
    # The dequeue DELETEs the job, so update_fl_scheduler can no longer free the net —
    # the abort path must release it itself once the abort has been delivered.
    mock_release.assert_called_once_with(model_id, fake_session)


@patch("flip_api.fl_services.services.fl_service.extract_current_job_data")
@patch("flip_api.fl_services.services.fl_service.get_fl_backend_job_id_by_model_id")
@patch("flip_api.fl_services.services.fl_service.fetch_server_status")
@patch("flip_api.fl_services.services.fl_service.abort_job")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_net_by_model_id")
@patch("flip_api.fl_services.services.fl_scheduler_service.remove_job_from_queue")
@patch("flip_api.fl_services.services.fl_scheduler_service.release_scheduler_for_model")
def test_abort_model_training_idempotent_when_no_running_job(
    mock_release,
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
    mock_extract_current_job_data.return_value = None

    request = MagicMock()
    request.scope = {"request_id": "req-id"}
    request.path_params = {"target": "server", "clients": None}

    # No running job -> idempotent no-op: must not raise and must not call abort_job.
    fl_service.abort_model_training(request, model_id, fake_session)
    mock_abort.assert_not_called()
    mock_release.assert_called_once_with(model_id, fake_session)


def test_add_fl_job_creates_job(model_id, fake_session):
    trusts = [Trust(id=uuid4(), name="client1"), Trust(id=uuid4(), name="client2")]
    fl_service.add_fl_job(model_id, trusts, fake_session)

    fake_session.add.assert_called_once()
    fake_session.commit.assert_called_once()
    fake_session.refresh.assert_called_once()

    # The persisted FLJob attaches the Trust rows via the `fl_job_trust` link relationship.
    persisted_job = fake_session.add.call_args.args[0]
    assert persisted_job.trusts == trusts


def test_add_fl_job_rollback_on_exception(model_id):
    fake_session = MagicMock()
    fake_session.add.side_effect = Exception("DB Error")
    with pytest.raises(Exception, match="DB Error"):
        fl_service.add_fl_job(model_id, [Trust(id=uuid4(), name="client1")], fake_session)
    fake_session.rollback.assert_called_once()


@patch("flip_api.fl_services.get_status.fetch_server_status")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_nets")
@patch("flip_api.fl_services.services.fl_service.Session")
def test_keep_fl_api_session_alive_pings_each_net(mock_session, mock_get_nets, mock_fetch):
    # The keep-alive poll pings every net's check_server_status; it no longer reconciles any backend.
    mock_session.return_value.__enter__.return_value = MagicMock()
    mock_get_nets.return_value = [
        MagicMock(endpoint="http://net1:8000"),
        MagicMock(endpoint="http://net2:8000"),
    ]

    fl_service.keep_fl_api_session_alive()

    assert mock_fetch.call_count == 2
    mock_fetch.assert_any_call("http://net1:8000")
    mock_fetch.assert_any_call("http://net2:8000")


@patch("flip_api.fl_services.get_status.fetch_server_status", side_effect=Exception("boom"))
@patch("flip_api.fl_services.services.fl_scheduler_service.get_nets")
@patch("flip_api.fl_services.services.fl_service.Session")
def test_keep_fl_api_session_alive_swallows_errors(mock_session, mock_get_nets, mock_fetch):
    # A failed status check for one net must not propagate (it is logged and ignored).
    mock_session.return_value.__enter__.return_value = MagicMock()
    mock_get_nets.return_value = [MagicMock(endpoint="http://net1:8000")]

    fl_service.keep_fl_api_session_alive()

    mock_fetch.assert_called_once_with("http://net1:8000")


# --- submit_job success path ---------------------------------------------------------------------


@patch("flip_api.fl_services.services.fl_service.add_fl_backend_job_id")
@patch("flip_api.fl_services.services.fl_service.http_post")
def test_submit_job_persists_backend_id_on_success(mock_post, mock_add, fl_job_id, model_id, fake_session):
    """A valid backend job id from the FL API is persisted against the FLJob row."""
    mock_post.return_value = "backend-job-99"

    fl_service.submit_job(fl_job_id, "endpoint", model_id, fake_session)

    mock_post.assert_called_once_with(f"endpoint/submit_job/{model_id}", timeout=30)
    mock_add.assert_called_once_with(fl_job_id, "backend-job-99", fake_session)


# --- check_server_status / check_client_status (the raw FL-API calls) ----------------------------


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_check_server_status_success(mock_http_get):
    mock_http_get.return_value = {"status": "running"}

    result = fl_service.check_server_status("endpoint")

    assert isinstance(result, IServerStatus)
    assert result.status == "running"
    mock_http_get.assert_called_once_with("endpoint/check_server_status", timeout=30)


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_check_server_status_returns_none_when_no_response(mock_http_get):
    mock_http_get.return_value = None

    assert fl_service.check_server_status("endpoint") is None


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_check_client_status_success(mock_http_get):
    mock_http_get.return_value = [
        {"name": "Trust_1", "status": "no_jobs"},
        {"name": "Trust_2", "status": "no_reply"},
    ]

    result = fl_service.check_client_status("endpoint")

    assert [c.name for c in result] == ["Trust_1", "Trust_2"]
    # online is derived from the status: no_jobs -> online, no_reply -> offline.
    assert result[0].online is True
    assert result[1].online is False
    mock_http_get.assert_called_once_with("endpoint/check_client_status", timeout=30)


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_check_client_status_returns_none_when_no_response(mock_http_get):
    mock_http_get.return_value = []

    assert fl_service.check_client_status("endpoint") is None


# --- fetch_* unavailable branches ----------------------------------------------------------------


@patch("flip_api.fl_services.services.fl_service.check_server_status")
def test_fetch_server_status_returns_none_when_unavailable(mock_check_server):
    mock_check_server.return_value = None

    assert fl_service.fetch_server_status("endpoint") is None


@patch("flip_api.fl_services.services.fl_service.check_client_status")
def test_fetch_client_status_returns_none_when_unavailable(mock_check_client):
    mock_check_client.return_value = None

    assert fl_service.fetch_client_status("endpoint") is None


# --- bundle_nvflare_application error / edge paths ------------------------------------------------


@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_nvflare_application_no_model_files(mock_s3, mocked_settings, model_id):
    mock_s3.return_value.list_objects.return_value = []

    with pytest.raises(FileNotFoundError, match="Model files missing on the S3 bucket"):
        fl_service.bundle_nvflare_application(model_id)


@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_nvflare_application_no_base_files(mock_s3, mocked_settings, model_id):
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET

    mock_client = mock_s3.return_value
    # No base tree written -> the local FL_APP_BASE_DIR/nvflare/standard directory is absent
    mock_client.list_objects.side_effect = [
        [f"{model_bucket}/{model_id}/trainer.py"],  # model files (no config.json)
    ]

    with pytest.raises(FileNotFoundError, match="Base application files missing in the local base directory"):
        fl_service.bundle_nvflare_application(model_id)


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_nvflare_application_no_app_folders(mock_s3, mock_required, mocked_settings, model_id):
    """Base tree without any top-level ``app*`` folder is rejected."""
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET

    write_base_tree(base_dir, "nvflare", "standard", ["notapp/file1.py"])  # no app* folder

    mock_client = mock_s3.return_value
    mock_client.list_objects.side_effect = [
        [f"{model_bucket}/{model_id}/trainer.py"],  # model files (no config.json)
        [],  # destination bucket empty (clear check)
    ]
    mock_client.copy_object.return_value = None

    with pytest.raises(FileNotFoundError, match="No app folders found under base application"):
        fl_service.bundle_nvflare_application(model_id)


@patch("flip_api.fl_services.services.fl_service.verify_bundle_paths")
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_nvflare_application_no_config_clears_existing_dest(
    mock_s3, mock_required, mock_verify, mocked_settings, model_id
):
    """No config.json falls back to job_type=standard, and stale destination files are cleared first."""
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    write_base_tree(base_dir, "nvflare", "standard", ["app/file1.py"])

    mock_client = mock_s3.return_value
    mock_required.return_value = ["trainer.py", "validator.py"]
    model_files = [
        f"{model_bucket}/{model_id}/trainer.py",
        f"{model_bucket}/{model_id}/validator.py",
    ]  # no config.json -> job_type stays "standard"
    stale_dest_files = [f"{dest_bucket}/{model_id}/stale.py"]
    mock_client.list_objects.side_effect = [model_files, stale_dest_files]
    mock_client.object_exists.return_value = False
    mock_verify.return_value = None

    result = fl_service.bundle_nvflare_application(model_id)

    assert result == f"{dest_bucket}/{model_id}"
    mock_client.delete_objects.assert_called_once_with(stale_dest_files)


# --- bundle_flower_application error / edge paths -------------------------------------------------


@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_flower_application_no_model_files(mock_s3, mocked_settings, model_id):
    mock_s3.return_value.list_objects.return_value = []

    with pytest.raises(FileNotFoundError, match="Model files missing on the S3 bucket"):
        fl_service.bundle_flower_application(model_id)


@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_flower_application_no_base_files(mock_s3, mocked_settings, model_id):
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET

    mock_client = mock_s3.return_value
    # No base tree written -> the local FL_APP_BASE_DIR/flower/standard directory is absent
    mock_client.list_objects.side_effect = [
        [f"{model_bucket}/{model_id}/client_app.py"],  # model files (no config.json)
    ]

    with pytest.raises(FileNotFoundError, match="Base application files missing in the local base directory"):
        fl_service.bundle_flower_application(model_id)


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_flower_application_no_config_clears_existing_dest(
    mock_s3, mock_required, mocked_settings, model_id
):
    """No config.json falls back to job_type=standard, and stale destination files are cleared first."""
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    write_base_tree(base_dir, "flower", "standard", ["app/server_app.py", "pyproject.toml"])

    mock_client = mock_s3.return_value
    mock_required.return_value = ["client_app.py", "models.py"]
    model_files = [
        f"{model_bucket}/{model_id}/client_app.py",
        f"{model_bucket}/{model_id}/models.py",
    ]  # no config.json -> job_type stays "standard"
    stale_dest_files = [f"{dest_bucket}/{model_id}/stale.py"]
    mock_client.list_objects.side_effect = [model_files, stale_dest_files]
    mock_client.object_exists.return_value = False

    result = fl_service.bundle_flower_application(model_id)

    assert result == f"{dest_bucket}/{model_id}"
    mock_client.delete_objects.assert_called_once_with(stale_dest_files)


# --- extract_current_job_data malformed response -------------------------------------------------


@patch("flip_api.fl_services.services.fl_service.http_get")
def test_extract_current_job_data_non_list_raises(mock_http_get):
    # FL API returning a single object instead of a list is a contract violation -> fail loudly.
    mock_http_get.return_value = {"job_id": "job123", "status": "RUNNING"}

    with pytest.raises(ValueError, match="Unexpected response format"):
        fl_service.extract_current_job_data("http://fl-api-endpoint", "job123")


# --- abort_model_training guard clauses ----------------------------------------------------------


@patch("flip_api.fl_services.services.fl_service.extract_current_job_data")
@patch("flip_api.fl_services.services.fl_service.get_fl_backend_job_id_by_model_id")
@patch("flip_api.fl_services.services.fl_service.fetch_server_status")
@patch("flip_api.fl_services.services.fl_service.abort_job")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_net_by_model_id")
@patch("flip_api.fl_services.services.fl_scheduler_service.remove_job_from_queue")
@patch("flip_api.fl_services.services.fl_scheduler_service.release_scheduler_for_model")
def test_abort_model_training_pre_submit_frees_net(
    mock_release,
    mock_remove,
    mock_get_net,
    mock_abort,
    mock_fetch_server_status,
    mock_get_fl_backend_job_id_by_model_id,
    mock_extract_current_job_data,
    model_id,
    fake_session,
):
    # Pre-running window (#787): the job was scheduled (net BUSY) but never submitted to the
    # fl-server, so fl_backend_job_id is still NULL and the backend-job-id lookup raises.
    mock_get_fl_backend_job_id_by_model_id.side_effect = ValueError("No FL backend job ID found")

    request = MagicMock()
    request.path_params = {}

    fl_service.abort_model_training(request, model_id, fake_session)

    # The job is dequeued and the BUSY net released immediately — not left for the watchdog.
    mock_remove.assert_called_once_with(model_id, fake_session)
    mock_release.assert_called_once_with(model_id, fake_session)
    # There is nothing to abort on the fl-server side.
    mock_fetch_server_status.assert_not_called()
    mock_extract_current_job_data.assert_not_called()
    mock_abort.assert_not_called()


@patch("flip_api.fl_services.services.fl_service.extract_current_job_data")
@patch("flip_api.fl_services.services.fl_service.get_fl_backend_job_id_by_model_id")
@patch("flip_api.fl_services.services.fl_service.fetch_server_status")
@patch("flip_api.fl_services.services.fl_service.abort_job")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_net_by_model_id")
@patch("flip_api.fl_services.services.fl_scheduler_service.remove_job_from_queue")
@patch("flip_api.fl_services.services.fl_scheduler_service.release_scheduler_for_model")
def test_abort_model_training_pre_pickup_net_not_found_frees_net(
    mock_release,
    mock_remove,
    mock_get_net,
    mock_abort,
    mock_fetch_server_status,
    mock_get_fl_backend_job_id_by_model_id,
    mock_extract_current_job_data,
    model_id,
    fake_session,
):
    # Queued-behind-another-job window: the job was dequeued but no scheduler picked it up yet,
    # so the net lookup raises NotFoundError — the other expected pre-running signal.
    mock_get_fl_backend_job_id_by_model_id.return_value = "job-1"
    mock_get_net.side_effect = NotFoundError(f"Net not found for model ID: {model_id}")

    request = MagicMock()
    request.path_params = {}

    fl_service.abort_model_training(request, model_id, fake_session)

    mock_remove.assert_called_once_with(model_id, fake_session)
    mock_release.assert_called_once_with(model_id, fake_session)
    mock_fetch_server_status.assert_not_called()
    mock_extract_current_job_data.assert_not_called()
    mock_abort.assert_not_called()


@patch("flip_api.fl_services.services.fl_service.extract_current_job_data")
@patch("flip_api.fl_services.services.fl_service.get_fl_backend_job_id_by_model_id")
@patch("flip_api.fl_services.services.fl_service.fetch_server_status")
@patch("flip_api.fl_services.services.fl_service.abort_job")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_net_by_model_id")
@patch("flip_api.fl_services.services.fl_scheduler_service.remove_job_from_queue")
@patch("flip_api.fl_services.services.fl_scheduler_service.release_scheduler_for_model")
def test_abort_model_training_db_error_propagates(
    mock_release,
    mock_remove,
    mock_get_net,
    mock_abort,
    mock_fetch_server_status,
    mock_get_fl_backend_job_id_by_model_id,
    mock_extract_current_job_data,
    model_id,
    fake_session,
):
    # A DB failure during the dequeue means the abort did NOT happen: it must propagate, not be
    # treated as the pre-running window (which would release the net and report success while
    # the job is still queued).
    mock_remove.side_effect = DatabaseError("Error removing job from queue")

    request = MagicMock()
    request.path_params = {}

    with pytest.raises(DatabaseError, match="Error removing job from queue"):
        fl_service.abort_model_training(request, model_id, fake_session)

    mock_release.assert_not_called()
    mock_fetch_server_status.assert_not_called()
    mock_abort.assert_not_called()


@patch("flip_api.fl_services.services.fl_service.extract_current_job_data")
@patch("flip_api.fl_services.services.fl_service.get_fl_backend_job_id_by_model_id")
@patch("flip_api.fl_services.services.fl_service.fetch_server_status")
@patch("flip_api.fl_services.services.fl_service.abort_job")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_net_by_model_id")
@patch("flip_api.fl_services.services.fl_scheduler_service.remove_job_from_queue")
@patch("flip_api.fl_services.services.fl_scheduler_service.release_scheduler_for_model")
def test_abort_model_training_raises_when_server_not_running(
    mock_release,
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
    mock_fetch_server_status.return_value = None  # FL server is down

    request = MagicMock()
    request.path_params = {"target": "server", "clients": None}

    with pytest.raises(ValueError, match="FL Server not running"):
        fl_service.abort_model_training(request, model_id, fake_session)

    # The abort must short-circuit before consulting the job list or issuing an abort.
    mock_extract_current_job_data.assert_not_called()
    mock_abort.assert_not_called()
    # The abort was never delivered, so the net must NOT be released — the job may still be live.
    mock_release.assert_not_called()


@patch("flip_api.fl_services.services.fl_service.extract_current_job_data")
@patch("flip_api.fl_services.services.fl_service.get_fl_backend_job_id_by_model_id")
@patch("flip_api.fl_services.services.fl_service.fetch_server_status")
@patch("flip_api.fl_services.services.fl_service.abort_job")
@patch("flip_api.fl_services.services.fl_scheduler_service.get_net_by_model_id")
@patch("flip_api.fl_services.services.fl_scheduler_service.remove_job_from_queue")
def test_abort_model_training_raises_on_invalid_target(
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
    mock_fetch_server_status.return_value = {"status": "started"}
    mock_extract_current_job_data.return_value = IJobMetaData(job_id="job123", status="RUNNING")

    request = MagicMock()
    request.path_params = {"target": "bogus-target", "clients": None}

    with pytest.raises(ValueError, match="Invalid target: bogus-target"):
        fl_service.abort_model_training(request, model_id, fake_session)

    mock_abort.assert_not_called()


# --- local base-file helper + local-directory bundling edge cases (FLIP#724) ----------------------


def test_list_local_base_files_missing_dir_returns_empty(tmp_path):
    # A non-existent base directory yields no files (bundlers turn this into FileNotFoundError).
    assert fl_service.list_local_base_files(tmp_path / "does-not-exist") == []


def test_list_local_base_files_skips_symlinks(tmp_path):
    # A symlinked file/dir inside FL_APP_BASE_DIR must not pull host files outside the template
    # tree into the uploaded bundle.
    base = tmp_path / "base"
    (base / "app").mkdir(parents=True)
    (base / "app" / "real.py").write_text("x")

    external_file = tmp_path / "external_secret.txt"
    external_file.write_text("secret")
    external_dir = tmp_path / "external_dir"
    external_dir.mkdir()
    (external_dir / "leak.py").write_text("leak")

    (base / "app" / "link.py").symlink_to(external_file)  # symlinked file
    (base / "linked_dir").symlink_to(external_dir)  # symlinked directory

    assert fl_service.list_local_base_files(base) == ["app/real.py"]


def test_list_local_base_files_returns_sorted_nested_relpaths(tmp_path):
    (tmp_path / "app" / "custom" / "sub").mkdir(parents=True)
    (tmp_path / "app" / "custom" / "sub" / "deep.py").write_text("x")
    (tmp_path / "app" / "config").mkdir(parents=True)
    (tmp_path / "app" / "config" / "config_fed_server.json").write_text("{}")
    (tmp_path / "pyproject.toml").write_text("x")

    # Directories are not returned, only files; paths are relative POSIX and sorted.
    assert fl_service.list_local_base_files(tmp_path) == [
        "app/config/config_fed_server.json",
        "app/custom/sub/deep.py",
        "pyproject.toml",
    ]


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.is_valid_job_type", return_value=True)
@patch("flip_api.fl_services.services.fl_service.verify_bundle_paths")
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_nvflare_application_uploads_nested_base_paths(
    mock_s3, mock_required, mock_verify, mock_is_valid, model_id, mocked_settings
):
    """Deeply nested base files are uploaded to matching nested destination keys (paths not flattened)."""
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET
    dest_bucket = mocked_settings.FL_APP_DESTINATION_BUCKET

    write_base_tree(base_dir, "nvflare", "standard", ["app/config/config_fed_server.json", "app/custom/sub/deep.py"])

    mock_client = mock_s3.return_value
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps({"job_type": "standard"}).encode("utf-8")))
    }
    mock_required.return_value = ["config.json"]
    mock_client.list_objects.side_effect = [
        [f"{model_bucket}/{model_id}/config.json"],
        [],  # dest clear
    ]
    mock_client.object_exists.return_value = False
    mock_verify.return_value = None

    fl_service.bundle_nvflare_application(model_id)

    mock_client.upload_file.assert_any_call(
        str(Path(base_dir) / "nvflare/standard/app/custom/sub/deep.py"),
        f"{dest_bucket}/{model_id}/app/custom/sub/deep.py",
    )
    mock_client.upload_file.assert_any_call(
        str(Path(base_dir) / "nvflare/standard/app/config/config_fed_server.json"),
        f"{dest_bucket}/{model_id}/app/config/config_fed_server.json",
    )


@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.is_valid_job_type", return_value=True)
@patch("flip_api.fl_services.services.fl_service.verify_bundle_paths")
@patch("flip_api.fl_services.services.fl_service.JobRequiredFiles.get_required_files")
@patch("flip_api.fl_services.services.fl_service.S3Client")
def test_bundle_nvflare_application_propagates_upload_failure(
    mock_s3, mock_required, mock_verify, mock_is_valid, model_id, mocked_settings
):
    """A failed base-file upload aborts the bundle (the error is not swallowed)."""
    base_dir = mocked_settings.FL_APP_BASE_DIR
    model_bucket = mocked_settings.SCANNED_MODEL_FILES_BUCKET

    write_base_tree(base_dir, "nvflare", "standard", ["app/file1.py"])

    mock_client = mock_s3.return_value
    mock_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps({"job_type": "standard"}).encode("utf-8")))
    }
    mock_required.return_value = ["config.json"]
    mock_client.list_objects.side_effect = [
        [f"{model_bucket}/{model_id}/config.json"],
        [],  # dest clear
    ]
    mock_client.upload_file.side_effect = Exception("S3 upload boom")

    with pytest.raises(Exception, match="S3 upload boom"):
        fl_service.bundle_nvflare_application(model_id)
