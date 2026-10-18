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

import json
import os
import tempfile
from unittest.mock import MagicMock, Mock

import numpy as np
from nvflare.apis.dxo import DXO, DataKind
from nvflare.apis.event_type import EventType
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.shareable import make_reply
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.app_event_type import AppEventType

from flip.constants import FlipMetaKey, PTConstants
from flip.nvflare.components.evaluation_json_generator import EvaluationJsonGenerator
from flip.nvflare.components.validation_json_generator import ValidationJsonGenerator as FlipValidationJsonGenerator


class TestEvaluationJsonGenerator:
    def setup_method(self):
        """Setup test fixtures"""
        self.generator = EvaluationJsonGenerator()
        self.fl_ctx = MagicMock()
        self.fl_ctx.get_peer_context.return_value = None

    def _set_props(self, data_client, validation_result, model_owner=None):
        """Stub the FLContext props that a controller sets before VALIDATION_RESULT_RECEIVED.

        Args:
            data_client (str | None): The client that produced the result.
            validation_result (Shareable | None): The result shareable.
            model_owner (str | None): The validated model's name. ``GlobalModelEval`` sets this;
                the retired legacy ``ModelEval`` controller (deleted) did not.
        """
        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.DATA_CLIENT: data_client,
            AppConstants.MODEL_OWNER: model_owner,
            AppConstants.VALIDATION_RESULT: validation_result,
        }.get(key, default)

    def _receive(self, data_client, validation_result, model_owner=None):
        """Drive one VALIDATION_RESULT_RECEIVED event through the generator."""
        self._set_props(data_client, validation_result, model_owner)
        self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

    @staticmethod
    def _metrics(metrics=None, supplementary=None, /, **kwargs):
        """Build the shareable a client returns on a successful validate task, as ``FLModelUtils.to_shareable`` does."""
        meta = {} if supplementary is None else {FlipMetaKey.SUPPLEMENTARY_RESULTS: supplementary}
        return DXO(data_kind=DataKind.METRICS, data={**(metrics or {}), **kwargs}, meta=meta).to_shareable()

    def test_init_default_values(self):
        """Test initialization with default values"""
        generator = EvaluationJsonGenerator()
        assert generator is not None
        assert generator._results_dir == PTConstants.EvalDir
        assert generator._json_file_name == PTConstants.EvalResultsFilename

    def test_init_custom_values(self):
        """Test initialization with custom values"""
        generator = EvaluationJsonGenerator(results_dir="custom_dir", json_file_name="custom_results.json")
        assert generator._results_dir == "custom_dir"
        assert generator._json_file_name == "custom_results.json"

    def test_handle_start_run_clears_results(self):
        """Test that START_RUN event clears results"""
        self.generator._eval_results = {"client1": {"accuracy": 0.9}}

        self.generator.handle_evaluation_events(EventType.START_RUN, self.fl_ctx)

        assert len(self.generator._eval_results) == 0

    def test_handle_validation_result_received_success(self):
        """Test successful handling of VALIDATION_RESULT_RECEIVED event"""
        data_client = "client1"
        metrics_data = {"accuracy": 0.95, "loss": 0.05}

        # Create DXO with METRICS data
        dxo = DXO(data_kind=DataKind.METRICS, data=metrics_data)
        shareable = dxo.to_shareable()

        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.DATA_CLIENT: data_client,
            AppConstants.VALIDATION_RESULT: shareable,
        }.get(key, default)

        self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        assert data_client in self.generator._eval_results
        assert self.generator._eval_results[data_client] == metrics_data

    def test_handle_validation_result_received_no_data_client(self):
        """Test handling when data_client is missing"""
        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.DATA_CLIENT: None,
            AppConstants.VALIDATION_RESULT: MagicMock(),
        }.get(key, default)

        self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        # Should log error, results should remain empty
        assert len(self.generator._eval_results) == 0

    def test_handle_validation_result_received_wrong_data_kind(self):
        """Test handling when DXO has wrong data kind"""
        data_client = "client1"

        # Create DXO with WEIGHTS data kind instead of METRICS
        dxo = DXO(data_kind=DataKind.WEIGHTS, data={"weight": [1, 2, 3]})
        shareable = dxo.to_shareable()

        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.DATA_CLIENT: data_client,
            AppConstants.VALIDATION_RESULT: shareable,
        }.get(key, default)

        self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        # Should not add to results due to wrong data kind
        assert data_client not in self.generator._eval_results

    def test_handle_validation_result_received_no_result(self):
        """Test handling when validation result is None"""
        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.DATA_CLIENT: "client1",
            AppConstants.VALIDATION_RESULT: None,
        }.get(key, default)

        self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        assert len(self.generator._eval_results) == 0

    def test_handle_validation_result_received_exception(self):
        """Test handling when exception occurs processing result"""
        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.DATA_CLIENT: "client1",
            AppConstants.VALIDATION_RESULT: "invalid_shareable",
        }.get(key, default)

        # Should handle exception gracefully
        self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        assert len(self.generator._eval_results) == 0

    def test_handle_end_run_creates_json_file(self):
        """Test that END_RUN event creates JSON file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = os.path.join(tmpdir, "run_1")
            os.makedirs(run_dir)

            # Setup mock engine and workspace
            mock_workspace = Mock()
            mock_workspace.get_run_dir.return_value = run_dir
            mock_engine = Mock()
            mock_engine.get_workspace.return_value = mock_workspace
            self.fl_ctx.get_engine.return_value = mock_engine
            self.fl_ctx.get_job_id.return_value = "job_123"

            # Add some test results
            self.generator._eval_results = {
                "client1": {"accuracy": 0.95, "loss": 0.05},
                "client2": {"accuracy": 0.92, "loss": 0.08},
            }

            self.generator.handle_evaluation_events(EventType.END_RUN, self.fl_ctx)

            # Check that JSON file was created
            eval_dir = os.path.join(run_dir, self.generator._results_dir)
            json_file = os.path.join(eval_dir, self.generator._json_file_name)

            assert os.path.exists(json_file)

            # Verify contents
            with open(json_file, "r") as f:
                saved_results = json.load(f)

            assert saved_results == self.generator._eval_results

    def test_handle_end_run_creates_directory_if_not_exists(self):
        """Test that END_RUN creates results directory if it doesn't exist"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = os.path.join(tmpdir, "run_1")
            os.makedirs(run_dir)

            mock_workspace = Mock()
            mock_workspace.get_run_dir.return_value = run_dir
            mock_engine = Mock()
            mock_engine.get_workspace.return_value = mock_workspace
            self.fl_ctx.get_engine.return_value = mock_engine
            self.fl_ctx.get_job_id.return_value = "job_123"

            self.generator._eval_results = {"client1": {"accuracy": 0.9}}

            eval_dir = os.path.join(run_dir, self.generator._results_dir)
            assert not os.path.exists(eval_dir)

            self.generator.handle_evaluation_events(EventType.END_RUN, self.fl_ctx)

            assert os.path.exists(eval_dir)

    def test_handle_multiple_clients(self):
        """Test handling results from multiple clients"""
        clients = ["client1", "client2", "client3"]

        for client in clients:
            metrics_data = {"accuracy": 0.9 + (0.01 * clients.index(client))}
            dxo = DXO(data_kind=DataKind.METRICS, data=metrics_data)
            shareable = dxo.to_shareable()

            self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
                AppConstants.DATA_CLIENT: client,
                AppConstants.VALIDATION_RESULT: shareable,
            }.get(key, default)

            self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        assert len(self.generator._eval_results) == 3
        for client in clients:
            assert client in self.generator._eval_results
            assert client in self.generator._eval_results
            assert client in self.generator._eval_results

    # --- de-fork contract: deduped as a subclass of FLIP's ValidationJsonGenerator ---

    def test_subclasses_flip_validation_json_generator(self):
        """De-fork guard: shares FLIP's manual-dispatch base (no-op handle_event + numpy encoder)."""
        assert issubclass(EvaluationJsonGenerator, FlipValidationJsonGenerator)
        assert isinstance(EvaluationJsonGenerator(), FlipValidationJsonGenerator)

    def test_handle_event_is_a_noop(self):
        """Auto-dispatched handle_event must not process events; ServerEventHandler drives
        handle_evaluation_events instead (else each result is recorded twice)."""
        dxo = DXO(data_kind=DataKind.METRICS, data={"accuracy": 0.9})
        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.DATA_CLIENT: "c",
            AppConstants.VALIDATION_RESULT: dxo.to_shareable(),
        }.get(key, default)

        self.generator.handle_event(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        assert self.generator._eval_results == {}

    def test_handle_end_run_encodes_numpy_floats(self):
        """Gained from the shared base: np.float32 metrics serialise (fork's json.dump raised)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = os.path.join(tmpdir, "run_1")
            os.makedirs(run_dir)
            mock_engine = Mock()
            mock_engine.get_workspace.return_value.get_run_dir.return_value = run_dir
            self.fl_ctx.get_engine.return_value = mock_engine
            self.fl_ctx.get_job_id.return_value = "job_123"
            self.generator._eval_results = {"client1": {"dice": np.float32(0.5)}}

            self.generator.handle_evaluation_events(EventType.END_RUN, self.fl_ctx)

            json_file = os.path.join(run_dir, self.generator._results_dir, self.generator._json_file_name)
            with open(json_file) as f:
                saved = json.load(f)
            assert saved["client1"]["dice"] == 0.5

    # --- FLIP#754: task-level failures must be recorded, not swallowed ---

    def test_aborted_validate_task_is_recorded_as_a_failure(self):
        """An aborted task carries no DXO. Its return code is recorded rather than raising."""
        self._receive("Trust_1", make_reply(ReturnCode.TASK_ABORTED), model_owner="SRV_arkplus_pretrained")

        assert self.generator._eval_results == {}
        assert self.generator._eval_failures == [
            {"model": "SRV_arkplus_pretrained", "client": "Trust_1", "return_code": "TASK_ABORTED"}
        ]

    def test_result_with_ok_code_but_non_metrics_dxo_is_recorded_as_a_failure(self):
        """A well-formed reply carrying the wrong data kind is still a failed evaluation task."""
        weights = DXO(data_kind=DataKind.WEIGHTS, data={"w": [1, 2, 3]}).to_shareable()

        self._receive("Trust_1", weights, model_owner="SRV_model")

        assert self.generator._eval_results == {}
        assert self.generator._eval_failures == [
            {"model": "SRV_model", "client": "Trust_1", "return_code": ReturnCode.EXECUTION_RESULT_ERROR}
        ]

    def test_all_tasks_failed_when_every_validate_task_aborts(self):
        """The all-aborted evaluation from FLIP#754: 2 models x 2 clients, every task aborted."""
        for model in ("SRV_model_a", "SRV_model_b"):
            for client in ("Trust_1", "Trust_2"):
                self._receive(client, make_reply(ReturnCode.TASK_ABORTED), model_owner=model)

        assert len(self.generator._eval_failures) == 4
        assert self.generator.all_tasks_failed() is True

    def test_all_tasks_failed_is_false_when_one_task_succeeded(self):
        """A partial failure still has results to upload, so the run is not wholly failed."""
        self._receive("Trust_1", self._metrics(accuracy=0.9), model_owner="SRV_model")
        self._receive("Trust_2", make_reply(ReturnCode.TASK_ABORTED), model_owner="SRV_model")

        assert self.generator.all_tasks_failed() is False

    def test_all_tasks_failed_is_false_when_no_validate_task_ran(self):
        """No validate tasks at all (e.g. a training job) is not an evaluation failure."""
        assert self.generator.all_tasks_failed() is False

    def test_start_run_clears_failures(self):
        self.generator._eval_failures = [{"model": "m", "client": "c", "return_code": "TASK_ABORTED"}]

        self.generator.handle_evaluation_events(EventType.START_RUN, self.fl_ctx)

        assert self.generator._eval_failures == []

    def test_end_run_writes_failures_file_alongside_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = os.path.join(tmpdir, "run_1")
            os.makedirs(run_dir)
            mock_engine = Mock()
            mock_engine.get_workspace.return_value.get_run_dir.return_value = run_dir
            self.fl_ctx.get_engine.return_value = mock_engine
            self.fl_ctx.get_job_id.return_value = "job_123"
            self.generator._eval_failures = [{"model": "SRV_model", "client": "Trust_1", "return_code": "TASK_ABORTED"}]

            self.generator.handle_evaluation_events(EventType.END_RUN, self.fl_ctx)

            failures_file = os.path.join(run_dir, self.generator._results_dir, PTConstants.EvalFailuresFilename)
            with open(failures_file) as f:
                assert json.load(f) == self.generator._eval_failures

    def test_end_run_writes_empty_failures_file_for_a_clean_run(self):
        """The file is always written, so its absence never has to be interpreted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = os.path.join(tmpdir, "run_1")
            os.makedirs(run_dir)
            mock_engine = Mock()
            mock_engine.get_workspace.return_value.get_run_dir.return_value = run_dir
            self.fl_ctx.get_engine.return_value = mock_engine
            self.fl_ctx.get_job_id.return_value = "job_123"

            self.generator.handle_evaluation_events(EventType.END_RUN, self.fl_ctx)

            failures_file = os.path.join(run_dir, self.generator._results_dir, PTConstants.EvalFailuresFilename)
            with open(failures_file) as f:
                assert json.load(f) == []

    # --- FLIP#754: GlobalModelEval sends one validate task per (model, client) ---

    def test_results_are_nested_by_model_when_model_owner_is_set(self):
        """Client-API path: without nesting, the second model's metrics overwrite the first's."""
        self._receive("Trust_1", self._metrics(accuracy=0.9), model_owner="SRV_model_a")
        self._receive("Trust_1", self._metrics(accuracy=0.8), model_owner="SRV_model_b")

        assert self.generator._eval_results == {
            "Trust_1": {"SRV_model_a": {"accuracy": 0.9}, "SRV_model_b": {"accuracy": 0.8}}
        }

    def test_results_stay_flat_when_model_owner_is_absent(self):
        """The retired ModelEval controller set no MODEL_OWNER: legacy stored results keep that shape."""
        self._receive("Trust_1", self._metrics(**{"model_a": {"dice": 0.7}}), model_owner=None)

        assert self.generator._eval_results == {"Trust_1": {"model_a": {"dice": 0.7}}}

    # --- FLIP#1526: the same results, flattened for the Central Hub ---

    def test_hub_metrics_for_one_model_are_the_bare_metric_names(self):
        self._receive("Trust_1", self._metrics(mean_dice=0.9, mean_iou=0.8), model_owner="SRV_model")
        self._receive("Trust_2", self._metrics(mean_dice=0.7), model_owner="SRV_model")

        assert self.generator.hub_metrics() == {
            "Trust_1": {"MEAN_DICE": 0.9, "MEAN_IOU": 0.8},
            "Trust_2": {"MEAN_DICE": 0.7},
        }

    def test_hub_metrics_prefix_the_model_name_when_a_client_evaluated_several(self):
        self._receive("Trust_1", self._metrics(dice=0.9), model_owner="SRV_model_a")
        self._receive("Trust_1", self._metrics(dice=0.8), model_owner="SRV_model_b")

        assert self.generator.hub_metrics() == {"Trust_1": {"MODEL_A/DICE": 0.9, "MODEL_B/DICE": 0.8}}

    def test_hub_metrics_label_a_model_the_same_at_every_client_when_one_task_failed(self):
        self._receive("Trust_1", self._metrics(dice=0.9), model_owner="SRV_model_a")
        self._receive("Trust_1", self._metrics(dice=0.8), model_owner="SRV_model_b")
        self._receive("Trust_2", self._metrics(dice=0.7), model_owner="SRV_model_a")
        self._receive("Trust_2", make_reply(ReturnCode.TASK_ABORTED), model_owner="SRV_model_b")

        assert self.generator.hub_metrics()["Trust_2"] == {"MODEL_A/DICE": 0.7}

    def test_hub_metrics_prefix_a_metric_that_shares_its_models_name(self):
        self._receive("Trust_1", self._metrics(dice=0.9), model_owner="SRV_dice")
        self._receive("Trust_1", self._metrics(dice=0.8), model_owner="SRV_b")

        assert self.generator.hub_metrics() == {"Trust_1": {"DICE/DICE": 0.9, "B/DICE": 0.8}}

    def test_hub_metrics_take_a_reply_already_keyed_by_its_model_as_it_is(self):
        """Ark+ multimodel: ``{model_name: {auroc_<lesion>: ...}}`` under owner ``SRV_<model_name>``."""
        self._receive("Trust_1", self._metrics(arkplus={"auroc_Effusion": 0.81}), model_owner="SRV_arkplus")

        assert self.generator.hub_metrics() == {"Trust_1": {"ARKPLUS/AUROC_EFFUSION": 0.81}}

    def test_supplementary_results_reach_the_results_file_but_not_the_hub(self):
        """``meta[SUPPLEMENTARY_RESULTS]`` merges back into the reply's entry; only ``metrics`` are plotted."""
        reply = {"m_a": {"auroc_Effusion": 0.8}}
        extra = {"m_a": {"delong_p_values": {"Effusion": {"m_b": 0.04}}}}
        self._receive("Trust_1", self._metrics(reply, extra), model_owner="SRV_m_a")

        assert self.generator._eval_results == {
            "Trust_1": {"SRV_m_a": {"m_a": {"auroc_Effusion": 0.8, "delong_p_values": {"Effusion": {"m_b": 0.04}}}}}
        }
        assert self.generator.hub_metrics() == {"Trust_1": {"M_A/AUROC_EFFUSION": 0.8}}

    def test_cumulative_multimodel_replies_keep_the_documented_results_and_plot_each_auroc_once(self):
        """Ark+ multimodel: each reply repeats every model scored so far; the last one carries the DeLong map."""
        p_a, p_b = {"Effusion": {"m_a": 1.0, "m_b": 0.04}}, {"Effusion": {"m_a": 0.04, "m_b": 1.0}}
        last = {"m_a": {"auroc_Effusion": 0.8}, "m_b": {"auroc_Effusion": 0.7}}
        extra = {"m_a": {"delong_p_values": p_a}, "m_b": {"delong_p_values": p_b}}
        self._receive("Trust_1", self._metrics({"m_a": {"auroc_Effusion": 0.8}}), model_owner="SRV_m_a")
        self._receive("Trust_1", self._metrics(last, extra), model_owner="SRV_m_b")

        assert self.generator._eval_results["Trust_1"]["SRV_m_b"] == {
            "m_a": {"auroc_Effusion": 0.8, "delong_p_values": p_a},
            "m_b": {"auroc_Effusion": 0.7, "delong_p_values": p_b},
        }
        assert self.generator.hub_metrics() == {"Trust_1": {"M_A/AUROC_EFFUSION": 0.8, "M_B/AUROC_EFFUSION": 0.7}}

    def test_hub_metrics_omit_undefined_and_non_scalar_values(self):
        """NaN is undefined, not 0; a list could hold per-sample values; neither is sent."""
        self._receive(
            "Trust_1",
            self._metrics(
                auroc=float("nan"), big=float("inf"), flag=True, per_sample=[0.1, 0.2], note="x", n=np.float32(0.5)
            ),
            model_owner="SRV_model",
        )

        self.generator.logger = Mock()

        assert self.generator.hub_metrics() == {"Trust_1": {"N": 0.5}}
        self.generator.logger.warning.assert_called_once_with(
            "Not sent to the hub for Trust_1 (not a finite number): ['AUROC', 'BIG', 'FLAG', 'NOTE', 'PER_SAMPLE']"
        )

    def test_hub_metrics_leave_out_failed_tasks_and_clear_on_start_run(self):
        self._receive("Trust_1", make_reply(ReturnCode.TASK_ABORTED), model_owner="SRV_model")
        assert self.generator.hub_metrics() == {}

        self._receive("Trust_2", self._metrics(dice=0.9), model_owner="SRV_model")
        self.generator.handle_evaluation_events(EventType.START_RUN, self.fl_ctx)

        assert self.generator.hub_metrics() == {}

    def test_hub_metrics_without_a_model_owner_flatten_the_reply(self):
        self._receive("Trust_1", self._metrics(**{"model_a": {"dice": 0.7}}), model_owner=None)

        assert self.generator.hub_metrics() == {"Trust_1": {"MODEL_A/DICE": 0.7}}
