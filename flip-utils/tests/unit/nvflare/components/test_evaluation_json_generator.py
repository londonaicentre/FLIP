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
from unittest.mock import MagicMock, Mock

import numpy as np
import pytest
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

    def _end_run(self, tmp_path):
        """Fire END_RUN into a fresh run dir; return the results dir (asserted absent beforehand)."""
        run_dir = tmp_path / "run_1"
        run_dir.mkdir()
        self.fl_ctx.get_engine.return_value.get_workspace.return_value.get_run_dir.return_value = str(run_dir)
        self.fl_ctx.get_job_id.return_value = "job_123"
        eval_dir = run_dir / self.generator._results_dir
        assert not eval_dir.exists()
        self.generator.handle_evaluation_events(EventType.END_RUN, self.fl_ctx)
        return eval_dir

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
        metrics_data = {"accuracy": 0.95, "loss": 0.05}

        self._receive("client1", self._metrics(metrics_data))

        assert self.generator._eval_results["client1"] == metrics_data

    @pytest.mark.parametrize(
        ("client", "result"),
        [
            pytest.param(None, MagicMock(), id="no_data_client"),
            pytest.param("client1", None, id="no_result"),
            pytest.param("client1", "invalid_shareable", id="unparseable_result"),
            pytest.param(
                "client1",
                DXO(data_kind=DataKind.WEIGHTS, data={"weight": [1, 2, 3]}).to_shareable(),
                id="wrong_data_kind",
            ),
        ],
    )
    def test_handle_validation_result_received_records_no_result(self, client, result):
        """Missing client, missing/unparseable result, or a non-METRICS DXO records no metrics."""
        self._receive(client, result)

        assert self.generator._eval_results == {}

    def test_handle_end_run_creates_json_file(self, tmp_path):
        """END_RUN creates the results directory and writes the JSON file into it"""
        self.generator._eval_results = {
            "client1": {"accuracy": 0.95, "loss": 0.05},
            "client2": {"accuracy": 0.92, "loss": 0.08},
        }

        eval_dir = self._end_run(tmp_path)

        assert eval_dir.is_dir()
        json_file = eval_dir / self.generator._json_file_name
        assert json_file.exists()
        assert json.loads(json_file.read_text()) == self.generator._eval_results

    def test_handle_multiple_clients(self):
        """Test handling results from multiple clients"""
        clients = ["client1", "client2", "client3"]

        for i, client in enumerate(clients):
            self._receive(client, self._metrics(accuracy=0.9 + 0.01 * i))

        assert len(self.generator._eval_results) == 3
        for client in clients:
            assert client in self.generator._eval_results

    # --- de-fork contract: deduped as a subclass of FLIP's ValidationJsonGenerator ---

    def test_subclasses_flip_validation_json_generator(self):
        """De-fork guard: shares FLIP's manual-dispatch base (no-op handle_event + numpy encoder)."""
        assert issubclass(EvaluationJsonGenerator, FlipValidationJsonGenerator)
        assert isinstance(EvaluationJsonGenerator(), FlipValidationJsonGenerator)

    def test_handle_event_is_a_noop(self):
        """Auto-dispatched handle_event must not process events; ServerEventHandler drives
        handle_evaluation_events instead (else each result is recorded twice)."""
        self._set_props("c", self._metrics(accuracy=0.9))

        self.generator.handle_event(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        assert self.generator._eval_results == {}

    def test_handle_end_run_encodes_numpy_floats(self, tmp_path):
        """Gained from the shared base: np.float32 metrics serialise (fork's json.dump raised)."""
        self.generator._eval_results = {"client1": {"dice": np.float32(0.5)}}

        eval_dir = self._end_run(tmp_path)

        saved = json.loads((eval_dir / self.generator._json_file_name).read_text())
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

    def test_end_run_writes_failures_file_alongside_results(self, tmp_path):
        self.generator._eval_failures = [{"model": "SRV_model", "client": "Trust_1", "return_code": "TASK_ABORTED"}]

        eval_dir = self._end_run(tmp_path)

        failures_file = eval_dir / PTConstants.EvalFailuresFilename
        assert json.loads(failures_file.read_text()) == self.generator._eval_failures

    def test_end_run_writes_empty_failures_file_for_a_clean_run(self, tmp_path):
        """The file is always written, so its absence never has to be interpreted."""
        eval_dir = self._end_run(tmp_path)

        assert json.loads((eval_dir / PTConstants.EvalFailuresFilename).read_text()) == []

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
