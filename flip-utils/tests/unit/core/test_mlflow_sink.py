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

"""Tests for flip.core.mlflow_sink module."""

from unittest.mock import Mock, patch

import pytest

from flip.constants import ModelStatus
from flip.core import mlflow_sink
from flip.core.mlflow_sink import (
    TAG_LAST_ERROR,
    TAG_MODEL_ID,
    TAG_RESULTS_URI,
    TAG_STATUS,
    MlflowSink,
    experiment_name,
    get_mlflow_sink,
    registered_model_name,
    reset_mlflow_sink,
)

MODEL_ID = "9c9b32e1-46b0-4a05-a67a-2934d3a6b2f4"


@pytest.fixture
def mock_client():
    """Build an MlflowSink whose MlflowClient is a Mock."""
    with patch("mlflow.tracking.MlflowClient") as client_cls:
        sink = MlflowSink("http://mlflow:5000")
        yield client_cls.return_value, sink


def _experiment(experiment_id: str = "exp-1") -> Mock:
    experiment = Mock()
    experiment.experiment_id = experiment_id
    return experiment


def _run(run_id: str = "run-1") -> Mock:
    run = Mock()
    run.info.run_id = run_id
    return run


class TestNaming:
    def test_experiment_name(self):
        assert experiment_name(MODEL_ID) == f"flip/{MODEL_ID}"

    def test_registered_model_name(self):
        assert registered_model_name(MODEL_ID) == f"flip-model-{MODEL_ID}"


class TestRunResolution:
    def test_adopts_existing_running_run(self, mock_client):
        """The newest RUNNING run tagged with the model id is adopted (hub pre-created it)."""
        client, sink = mock_client
        client.get_experiment_by_name.return_value = _experiment()
        client.search_runs.return_value = [_run("existing-run")]

        assert sink._resolve_run_id(MODEL_ID) == "existing-run"
        client.create_run.assert_not_called()
        filter_string = client.search_runs.call_args.kwargs["filter_string"]
        assert f"tags.{TAG_MODEL_ID} = '{MODEL_ID}'" in filter_string
        assert "attributes.status = 'RUNNING'" in filter_string

    def test_creates_run_when_none_running(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.return_value = _experiment()
        client.search_runs.return_value = []
        client.create_run.return_value = _run("fresh-run")

        assert sink._resolve_run_id(MODEL_ID) == "fresh-run"
        assert client.create_run.call_args.kwargs["tags"][TAG_MODEL_ID] == MODEL_ID

    def test_creates_experiment_when_missing(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.return_value = None
        client.create_experiment.return_value = "exp-new"
        client.search_runs.return_value = [_run()]

        sink._resolve_run_id(MODEL_ID)
        client.create_experiment.assert_called_once_with(experiment_name(MODEL_ID))

    def test_lost_experiment_create_race_falls_back_to_get(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.side_effect = [None, _experiment("exp-raced")]
        client.create_experiment.side_effect = Exception("RESOURCE_ALREADY_EXISTS")
        client.search_runs.return_value = [_run()]

        sink._resolve_run_id(MODEL_ID)
        assert client.search_runs.call_args.args[0] == ["exp-raced"]

    def test_run_id_is_cached(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.return_value = _experiment()
        client.search_runs.return_value = [_run("cached-run")]

        sink._resolve_run_id(MODEL_ID)
        sink._resolve_run_id(MODEL_ID)
        client.search_runs.assert_called_once()


class TestLogMetric:
    def test_logs_metric_with_client_suffixed_key_and_step(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.return_value = _experiment()
        client.search_runs.return_value = [_run("run-9")]

        sink.log_metric(MODEL_ID, "Trust_1", "LOSS_FUNCTION", 0.42, round=3)

        args, kwargs = client.log_metric.call_args
        assert args == ("run-9", "LOSS_FUNCTION/Trust_1", 0.42)
        assert kwargs["step"] == 3

    def test_sanitises_invalid_key_characters(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.return_value = _experiment()
        client.search_runs.return_value = [_run()]

        sink.log_metric(MODEL_ID, "trust:one", "LOSS", 1.0, round=0)

        assert client.log_metric.call_args.args[1] == "LOSS/trust_one"

    def test_swallows_client_errors(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.side_effect = Exception("mlflow down")

        sink.log_metric(MODEL_ID, "Trust_1", "LOSS", 1.0, round=0)  # must not raise


class TestOnStatus:
    def test_non_terminal_status_tags_only(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.return_value = _experiment()
        client.search_runs.return_value = [_run("run-2")]

        sink.on_status(MODEL_ID, ModelStatus.TRAINING_STARTED)

        client.set_tag.assert_called_once_with("run-2", TAG_STATUS, "TRAINING_STARTED")
        client.set_terminated.assert_not_called()

    @pytest.mark.parametrize(
        ("status", "mlflow_status"),
        [
            (ModelStatus.RESULTS_UPLOADED, "FINISHED"),
            (ModelStatus.ERROR, "FAILED"),
            (ModelStatus.RESULTS_UPLOAD_FAILED, "FAILED"),
            (ModelStatus.STOPPED, "KILLED"),
        ],
    )
    def test_terminal_status_terminates_run(self, mock_client, status, mlflow_status):
        client, sink = mock_client
        client.get_experiment_by_name.return_value = _experiment()
        client.search_runs.return_value = [_run("run-3")]

        sink.on_status(MODEL_ID, status)

        client.set_terminated.assert_called_once_with("run-3", status=mlflow_status)

    def test_terminal_status_evicts_cached_run(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.return_value = _experiment()
        client.search_runs.return_value = [_run("run-4")]

        sink.on_status(MODEL_ID, ModelStatus.RESULTS_UPLOADED)

        assert MODEL_ID not in sink._run_ids

    def test_swallows_client_errors(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.side_effect = Exception("mlflow down")

        sink.on_status(MODEL_ID, ModelStatus.ERROR)  # must not raise


class TestNoteException:
    def test_tags_truncated_exception(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.return_value = _experiment()
        client.search_runs.return_value = [_run("run-5")]

        sink.note_exception(MODEL_ID, "Trust_1", "boom " * 200)

        run_id, tag, value = client.set_tag.call_args.args
        assert (run_id, tag) == ("run-5", TAG_LAST_ERROR)
        assert value.startswith("Trust_1: boom")
        assert len(value) <= 500


class TestRegisterResults:
    URI = f"s3://results-bucket/{MODEL_ID}/20260708_120000.zip"

    def test_registers_version_by_reference_uri(self, mock_client):
        """The version source is the existing S3 URI — no bytes are copied into MLflow."""
        client, sink = mock_client
        client.get_experiment_by_name.return_value = _experiment()
        client.search_runs.return_value = [_run("run-6")]

        sink.register_results(MODEL_ID, self.URI)

        client.set_tag.assert_called_once_with("run-6", TAG_RESULTS_URI, self.URI)
        client.create_model_version.assert_called_once_with(
            name=registered_model_name(MODEL_ID), source=self.URI, run_id="run-6"
        )

    def test_existing_registered_model_is_reused(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.return_value = _experiment()
        client.search_runs.return_value = [_run()]
        client.create_registered_model.side_effect = Exception("RESOURCE_ALREADY_EXISTS")

        sink.register_results(MODEL_ID, self.URI)

        client.create_model_version.assert_called_once()

    def test_swallows_client_errors(self, mock_client):
        client, sink = mock_client
        client.get_experiment_by_name.side_effect = Exception("mlflow down")

        sink.register_results(MODEL_ID, self.URI)  # must not raise


class TestGetMlflowSink:
    @pytest.fixture(autouse=True)
    def _reset(self):
        reset_mlflow_sink()
        yield
        reset_mlflow_sink()

    def test_disabled_when_uri_unset(self):
        with patch("flip.core.mlflow_sink.FlipConstants") as constants:
            constants.MLFLOW_TRACKING_URI = ""
            assert get_mlflow_sink() is None

    def test_enabled_when_uri_set(self):
        with (
            patch("flip.core.mlflow_sink.FlipConstants") as constants,
            patch("mlflow.tracking.MlflowClient"),
        ):
            constants.MLFLOW_TRACKING_URI = "http://mlflow:5000"
            sink = get_mlflow_sink()
            assert isinstance(sink, MlflowSink)
            assert get_mlflow_sink() is sink  # singleton

    def test_disabled_when_client_init_fails(self):
        with (
            patch("flip.core.mlflow_sink.FlipConstants") as constants,
            patch("mlflow.tracking.MlflowClient", side_effect=ImportError("no mlflow")),
        ):
            constants.MLFLOW_TRACKING_URI = "http://mlflow:5000"
            assert get_mlflow_sink() is None

    def test_decision_is_sticky_until_reset(self):
        with patch("flip.core.mlflow_sink.FlipConstants") as constants:
            constants.MLFLOW_TRACKING_URI = ""
            assert get_mlflow_sink() is None
            constants.MLFLOW_TRACKING_URI = "http://mlflow:5000"
            assert get_mlflow_sink() is None  # still None: evaluated once per process
            reset_mlflow_sink()
            with patch("mlflow.tracking.MlflowClient"):
                assert get_mlflow_sink() is not None

    def test_sets_http_timeout_env_defaults(self, monkeypatch):
        monkeypatch.delenv("MLFLOW_HTTP_REQUEST_TIMEOUT", raising=False)
        monkeypatch.delenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", raising=False)
        with patch("mlflow.tracking.MlflowClient"):
            MlflowSink("http://mlflow:5000")
        assert mlflow_sink.os.environ["MLFLOW_HTTP_REQUEST_TIMEOUT"] == "10"
        assert mlflow_sink.os.environ["MLFLOW_HTTP_REQUEST_MAX_RETRIES"] == "1"
