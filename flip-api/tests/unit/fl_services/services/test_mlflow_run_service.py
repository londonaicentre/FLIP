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

"""Tests for the best-effort MLflow run bootstrap (FLIP#745)."""

import json
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLJob, Model
from flip_api.domain.schemas.types import FLBackend
from flip_api.fl_services.services import mlflow_run_service

MODEL_ID = uuid4()
FL_JOB_ID = uuid4()
PROJECT_ID = uuid4()


@pytest.fixture
def settings_enabled():
    """Point the service at a (mocked) tracking server."""
    settings = Mock()
    settings.MLFLOW_TRACKING_URI = "http://mlflow:5000"
    settings.SCANNED_MODEL_FILES_BUCKET = "s3://scanned-bucket"
    with patch.object(mlflow_run_service, "get_settings", return_value=settings):
        yield settings


@pytest.fixture
def client(settings_enabled):
    """Mock the MlflowClient the service builds lazily."""
    with patch("mlflow.tracking.MlflowClient") as client_cls:
        client = client_cls.return_value
        experiment = Mock()
        experiment.experiment_id = "exp-1"
        client.get_experiment_by_name.return_value = experiment
        client.search_runs.return_value = []
        run = Mock()
        run.info.run_id = "run-1"
        client.create_run.return_value = run
        yield client


@pytest.fixture
def s3_no_config():
    """S3 with no config.json for the model."""
    with patch.object(mlflow_run_service, "S3Client") as s3_cls:
        s3_cls.return_value.list_objects.return_value = []
        yield s3_cls.return_value


@pytest.fixture
def session():
    """DB session whose .get returns a model row and an FLJob row."""
    model = Model(id=MODEL_ID, name="ChestXray", project_id=PROJECT_ID, owner_id=uuid4())
    fl_job = FLJob(id=FL_JOB_ID, model_id=MODEL_ID, fl_backend_job_id="backend-job-7")
    session = Mock()
    session.get.side_effect = lambda entity, key: {Model: model, FLJob: fl_job}[entity]
    return session


class TestGetClient:
    def test_arn_uri_pins_the_global_tracking_uri(self):
        """The sagemaker-mlflow plugin authenticates from the GLOBAL tracking URI (FLIP#745 spike)."""
        arn = "arn:aws:sagemaker:eu-west-2:123456789012:mlflow-app/app-x"
        settings = Mock()
        settings.MLFLOW_TRACKING_URI = arn
        with (
            patch.object(mlflow_run_service, "get_settings", return_value=settings),
            patch("mlflow.tracking.MlflowClient"),
            patch("mlflow.set_tracking_uri") as set_uri,
        ):
            assert mlflow_run_service._get_client() is not None
        set_uri.assert_called_once_with(arn)

    def test_http_uri_does_not_touch_the_global_tracking_uri(self, settings_enabled):
        with (
            patch("mlflow.tracking.MlflowClient"),
            patch("mlflow.set_tracking_uri") as set_uri,
        ):
            assert mlflow_run_service._get_client() is not None
        set_uri.assert_not_called()


class TestStartRunForJob:
    def test_noop_when_uri_unset(self):
        settings = Mock()
        settings.MLFLOW_TRACKING_URI = ""
        with (
            patch.object(mlflow_run_service, "get_settings", return_value=settings),
            patch("mlflow.tracking.MlflowClient") as client_cls,
        ):
            mlflow_run_service.start_run_for_job(MODEL_ID, FL_JOB_ID, ["Trust_1"], FLBackend.NVFLARE, Mock())
        client_cls.assert_not_called()

    def test_creates_run_with_lineage_tags(self, client, s3_no_config, session):
        mlflow_run_service.start_run_for_job(MODEL_ID, FL_JOB_ID, ["Trust_1", "Trust_2"], FLBackend.FLOWER, session)

        client.create_run.assert_called_once()
        kwargs = client.create_run.call_args.kwargs
        assert kwargs["run_name"] == f"job-{FL_JOB_ID}"
        tags = kwargs["tags"]
        assert tags[mlflow_run_service.TAG_MODEL_ID] == str(MODEL_ID)
        assert tags[mlflow_run_service.TAG_FL_JOB_ID] == str(FL_JOB_ID)
        assert tags[mlflow_run_service.TAG_BACKEND] == "flower"
        assert tags[mlflow_run_service.TAG_JOB_TYPE] == "standard"
        assert tags[mlflow_run_service.TAG_TRUSTS] == "Trust_1,Trust_2"
        assert tags[mlflow_run_service.TAG_MODEL_NAME] == "ChestXray"
        assert tags[mlflow_run_service.TAG_PROJECT_ID] == str(PROJECT_ID)

    def test_logs_overridable_params_from_config_json(self, client, session):
        config = {
            "job_type": "fed_opt",
            "GLOBAL_ROUNDS": 10,
            "LOCAL_ROUNDS": 2,
            "AGGREGATION_WEIGHTS": {"Trust_1": 0.5},
            "unrelated_key": "ignored",
        }
        with patch.object(mlflow_run_service, "S3Client") as s3_cls:
            s3 = s3_cls.return_value
            s3.list_objects.return_value = [f"scanned-bucket/{MODEL_ID}/config.json"]
            s3.get_object.return_value = {"Body": Mock(read=Mock(return_value=json.dumps(config)))}

            mlflow_run_service.start_run_for_job(MODEL_ID, FL_JOB_ID, ["Trust_1"], FLBackend.NVFLARE, session)

        assert client.create_run.call_args.kwargs["tags"][mlflow_run_service.TAG_JOB_TYPE] == "fed_opt"
        logged = {call.args[1]: call.args[2] for call in client.log_param.call_args_list}
        assert logged == {
            "GLOBAL_ROUNDS": "10",
            "LOCAL_ROUNDS": "2",
            "AGGREGATION_WEIGHTS": json.dumps({"Trust_1": 0.5}),
        }

    def test_terminates_stale_running_runs_first(self, client, s3_no_config, session):
        stale = Mock()
        stale.info.run_id = "stale-run"
        client.search_runs.return_value = [stale]

        mlflow_run_service.start_run_for_job(MODEL_ID, FL_JOB_ID, ["Trust_1"], FLBackend.NVFLARE, session)

        client.set_terminated.assert_called_once_with("stale-run", status="FAILED")
        client.create_run.assert_called_once()

    def test_swallows_mlflow_errors(self, client, s3_no_config, session):
        client.create_run.side_effect = Exception("mlflow down")

        mlflow_run_service.start_run_for_job(MODEL_ID, FL_JOB_ID, ["Trust_1"], FLBackend.NVFLARE, session)


class TestRecordBackendJobId:
    def test_tags_running_run_with_backend_job_id(self, client, session):
        run = Mock()
        run.info.run_id = "run-1"
        client.search_runs.return_value = [run]

        mlflow_run_service.record_backend_job_id(MODEL_ID, FL_JOB_ID, session)

        client.set_tag.assert_called_once_with("run-1", mlflow_run_service.TAG_FL_BACKEND_JOB_ID, "backend-job-7")

    def test_noop_when_backend_job_id_missing(self, client):
        session = Mock()
        session.get.return_value = FLJob(id=FL_JOB_ID, model_id=MODEL_ID, fl_backend_job_id=None)

        mlflow_run_service.record_backend_job_id(MODEL_ID, FL_JOB_ID, session)

        client.set_tag.assert_not_called()


class TestFailRun:
    def test_terminates_all_running_runs_as_failed(self, client):
        runs = [Mock(), Mock()]
        runs[0].info.run_id = "run-a"
        runs[1].info.run_id = "run-b"
        client.search_runs.return_value = runs

        mlflow_run_service.fail_run(MODEL_ID)

        assert [call.args[0] for call in client.set_terminated.call_args_list] == ["run-a", "run-b"]
        assert all(call.kwargs == {"status": "FAILED"} for call in client.set_terminated.call_args_list)

    def test_swallows_mlflow_errors(self, client):
        client.search_runs.side_effect = Exception("mlflow down")

        mlflow_run_service.fail_run(MODEL_ID)


class TestStopRun:
    def test_terminates_running_runs_as_killed(self, client):
        run = Mock()
        run.info.run_id = "run-live"
        client.search_runs.return_value = [run]

        mlflow_run_service.stop_run(MODEL_ID)

        client.set_terminated.assert_called_once_with("run-live", status="KILLED")

    def test_swallows_mlflow_errors(self, client):
        client.search_runs.side_effect = Exception("mlflow down")

        mlflow_run_service.stop_run(MODEL_ID)


class TestSoftDeleteModel:
    def test_soft_deletes_active_experiment_and_tags_registered_model(self, client):
        experiment = Mock()
        experiment.experiment_id = "exp-1"
        experiment.lifecycle_stage = "active"
        client.get_experiment_by_name.return_value = experiment

        mlflow_run_service.soft_delete_model(MODEL_ID)

        client.delete_experiment.assert_called_once_with("exp-1")
        client.set_registered_model_tag.assert_called_once_with(f"flip-model-{MODEL_ID}", "flip.deleted", "true")

    def test_missing_experiment_and_registered_model_are_fine(self, client):
        client.get_experiment_by_name.return_value = None
        client.set_registered_model_tag.side_effect = Exception("RESOURCE_DOES_NOT_EXIST")

        mlflow_run_service.soft_delete_model(MODEL_ID)

        client.delete_experiment.assert_not_called()

    def test_already_deleted_experiment_is_not_deleted_again(self, client):
        experiment = Mock()
        experiment.experiment_id = "exp-1"
        experiment.lifecycle_stage = "deleted"
        client.get_experiment_by_name.return_value = experiment

        mlflow_run_service.soft_delete_model(MODEL_ID)

        client.delete_experiment.assert_not_called()
