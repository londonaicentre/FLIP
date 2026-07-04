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

import os
from unittest.mock import MagicMock, patch

import pytest
from nvflare.apis.dxo import DXO, DataKind
from nvflare.apis.fl_constant import ReturnCode
from nvflare.widgets.info_collector import GroupInfoCollector, InfoCollector

from flip.constants import FlipTasks, PTConstants
from flip.nvflare.controllers.cross_site_model_eval import CrossSiteModelEval as FlipCrossSiteModelEval
from flip.nvflare.controllers.fed_evaluation import ModelEval

_MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"


def _make(**kwargs) -> ModelEval:
    kwargs.setdefault("model_id", _MODEL_ID)
    controller = ModelEval(**kwargs)
    controller.flip = MagicMock()
    for method in ("log_info", "log_debug", "log_error", "log_exception", "fire_event"):
        setattr(controller, method, MagicMock())
    return controller


def _fl_ctx() -> MagicMock:
    fl_ctx = MagicMock()
    fl_ctx.get_peer_context.return_value = None
    fl_ctx.get_prop.return_value = None
    return fl_ctx


class _AbortAfter:
    """abort_signal whose ``triggered`` returns False until the Nth read, then True — lets a test
    drive control_flow to a specific abort checkpoint."""

    def __init__(self, trigger_on: int):
        self._reads = 0
        self._trigger_on = trigger_on

    @property
    def triggered(self) -> bool:
        self._reads += 1
        return self._reads >= self._trigger_on


class TestModelEval:
    """ModelEval evaluates a collection of server models against every client. It shares FLIP's
    eval base (model-id resolution + hub exception reporting) with CrossSiteModelEval by
    subclassing it, and overrides only the collection-evaluation orchestration.
    """

    def test_subclasses_flip_cross_site_model_eval(self):
        """De-fork guard: shares one FLIP eval base; this also *defines* _validation_task_name
        and the other attrs the fork referenced but never assigned."""
        assert issubclass(ModelEval, FlipCrossSiteModelEval)
        assert isinstance(_make(), FlipCrossSiteModelEval)

    def test_init_stores_flip_model_id_and_eval_task_name(self):
        controller = _make(evaluation_task_name="validate", cleanup_timeout=300)
        assert controller._model_id_fallback == _MODEL_ID
        assert controller._model_id is None
        assert controller._evaluation_task_name == "validate"
        assert controller._cleanup_timeout == 300
        # dead-residue fix: the attr the fork referenced but never defined now exists
        assert controller._validation_task_name == "validate"

    def test_init_defaults_eval_task_name_and_results_dir(self):
        controller = _make()
        assert controller._evaluation_task_name == PTConstants.EvalTaskName
        assert controller._eval_results_dir == PTConstants.EvalDir
        assert controller._eval_results == {}

    def test_resolve_model_id_uses_fallback_when_fl_ctx_has_no_props(self):
        controller = _make()
        assert controller._resolve_model_id(_fl_ctx()) == _MODEL_ID

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"submit_model_timeout": -1}, "submit_model_timeout must be greater"),
            ({"validation_timeout": -1}, "model_validate_timeout must be greater"),
            ({"wait_for_clients_timeout": -1}, "wait_for_clients_timeout must be greater"),
            ({"cleanup_timeout": -1}, "cleanup_timeout must be greater"),
        ],
    )
    def test_init_negative_timeouts_raise(self, kwargs, match):
        """Timeout validation is inherited from the stock/FLIP base."""
        with pytest.raises(ValueError, match=match):
            ModelEval(model_id=_MODEL_ID, **kwargs)

    def test_init_custom_task_names_and_clients(self):
        controller = _make(submit_model_task_name="custom_submit", evaluation_task_name="custom_eval",
                           participating_clients=["a", "b"])
        assert controller._submit_model_task_name == "custom_submit"
        assert controller._evaluation_task_name == "custom_eval"
        assert controller._participating_clients == ["a", "b"]

    def test_accept_val_result_ok_records_result_path(self):
        controller = _make()
        controller._eval_results_dir = "/eval"
        result = MagicMock()
        result.get_return_code.return_value = ReturnCode.OK

        controller._accept_val_result("c1", result, _fl_ctx())

        assert controller._eval_results["c1"] == os.path.join("/eval", "c1")
        controller.flip.send_handled_exception.assert_not_called()

    def test_accept_val_result_execution_exception_reports_and_empties(self):
        controller = _make()
        result = MagicMock()
        result.get_return_code.return_value = ReturnCode.EXECUTION_EXCEPTION
        result.get_header.return_value = "boom-traceback"

        controller._accept_val_result("c1", result, _fl_ctx())

        controller.flip.send_handled_exception.assert_called_once()
        assert controller.flip.send_handled_exception.call_args.kwargs["formatted_exception"] == "boom-traceback"
        assert controller._eval_results["c1"] == {}

    def test_control_flow_broadcasts_eval_task_then_post_task_cleanup(self):
        controller = _make(evaluation_task_name="validate")
        controller._participating_clients = ["c1", "c2"]
        controller._model_locator = None  # skip server-model loading
        controller.get_num_standing_tasks = MagicMock(return_value=0)
        controller.broadcast = MagicMock()
        controller.broadcast_and_wait = MagicMock()
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, _fl_ctx())

        controller.broadcast.assert_called_once()
        assert controller.broadcast.call_args.kwargs["task"].name == "validate"
        controller.broadcast_and_wait.assert_called_once()
        assert controller.broadcast_and_wait.call_args.kwargs["task"].name == FlipTasks.POST_TASK.value

    # --- coverage for the overridden collection-evaluation methods ---

    def test_start_controller_no_engine_panics(self):
        controller = _make()
        fl_ctx = _fl_ctx()
        fl_ctx.get_engine.return_value = None
        controller.system_panic = MagicMock()

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()

    @patch("os.makedirs")
    @patch("shutil.rmtree")
    @patch("os.path.exists", return_value=True)
    def test_start_controller_sets_eval_dir_and_registers_clients(self, mock_exists, mock_rmtree, mock_makedirs):
        controller = _make()
        engine = MagicMock()
        client = MagicMock()
        client.name = "c1"
        engine.get_clients.return_value = [client]
        engine.get_workspace.return_value.get_run_dir.return_value = "/run"
        fl_ctx = _fl_ctx()
        fl_ctx.get_engine.return_value = engine

        controller.start_controller(fl_ctx)

        assert "c1" in controller._participating_clients
        assert controller._eval_results["c1"] == {}
        assert controller._eval_results_dir == os.path.join("/run", PTConstants.EvalDir)

    def test_control_flow_locates_server_models_then_evaluates(self):
        controller = _make(evaluation_task_name="validate")
        controller._participating_clients = ["c1"]
        controller._model_locator = MagicMock()
        controller._locate_server_models = MagicMock(return_value=True)
        controller.get_num_standing_tasks = MagicMock(return_value=0)
        controller.broadcast = MagicMock()
        controller.broadcast_and_wait = MagicMock()
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, _fl_ctx())

        controller._locate_server_models.assert_called_once()
        controller.broadcast.assert_called_once()

    def test_control_flow_locate_fails_returns_before_broadcast(self):
        controller = _make()
        controller._participating_clients = ["c1"]
        controller._model_locator = MagicMock()
        controller._locate_server_models = MagicMock(return_value=False)
        controller.broadcast = MagicMock()
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, _fl_ctx())

        controller.broadcast.assert_not_called()

    def test_control_flow_exception_triggers_system_panic(self):
        controller = _make()
        controller._participating_clients = ["c1"]
        controller._model_locator = None
        controller.broadcast = MagicMock(side_effect=RuntimeError("boom"))
        controller.system_panic = MagicMock()
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, _fl_ctx())

        controller.system_panic.assert_called_once()

    def test_locate_server_models_loads_collection(self):
        controller = _make()
        all_models = MagicMock()
        all_models.data = {"m1": DXO(data_kind=DataKind.METRICS, data={"a": 1})}
        controller._model_locator = MagicMock()
        controller._model_locator.locate_model.return_value = all_models
        controller._model_locator.model_names = ["m1"]

        assert controller._locate_server_models(_fl_ctx()) is True
        assert controller._all_models_dxo is all_models
        assert controller._eval_results["m1"] == {}

    def test_locate_server_models_non_dxo_panics(self):
        controller = _make()
        all_models = MagicMock()
        all_models.data = {"m1": "not-a-dxo"}
        controller._model_locator = MagicMock()
        controller._model_locator.locate_model.return_value = all_models
        controller._model_locator.model_names = ["m1"]
        controller.system_panic = MagicMock()

        assert controller._locate_server_models(_fl_ctx()) is False
        controller.system_panic.assert_called_once()

    def test_before_send_validate_task_cb_bundles_all_models(self):
        controller = _make()
        controller._all_models_dxo = DXO(data_kind=DataKind.METRICS, data={"a": 1})
        controller._participating_clients = ["c1"]
        client_task = MagicMock()

        controller._before_send_validate_task_cb(client_task, _fl_ctx())

        assert client_task.task.data is not None

    def test_before_send_validate_task_cb_empty_models_panics(self):
        controller = _make()
        controller._all_models_dxo = None
        controller.system_panic = MagicMock()

        controller._before_send_validate_task_cb(MagicMock(), _fl_ctx())

        controller.system_panic.assert_called_once()

    def test_handle_event_get_stats_reports_eval_results(self):
        controller = _make()
        controller._formatter = MagicMock()
        controller._formatter.format.return_value = {"acc": 0.9}
        controller._eval_results = {"c1": "/eval/c1"}
        collector = MagicMock(spec=GroupInfoCollector)
        fl_ctx = _fl_ctx()
        fl_ctx.get_prop.side_effect = lambda key, default=None: (
            collector if key == InfoCollector.CTX_KEY_STATS_COLLECTOR else default
        )

        controller.handle_event(InfoCollector.EVENT_TYPE_GET_STATS, fl_ctx)

        collector.add_info.assert_called_once()

    def test_handle_event_get_stats_without_formatter_warns(self):
        controller = _make()
        controller._formatter = None
        controller.log_warning = MagicMock()

        controller.handle_event(InfoCollector.EVENT_TYPE_GET_STATS, _fl_ctx())

        controller.log_warning.assert_called_once()

    @patch("os.makedirs")
    @patch("shutil.rmtree")
    @patch("os.path.exists", return_value=False)
    def test_start_controller_bad_model_locator_panics(self, mock_exists, mock_rmtree, mock_makedirs):
        controller = _make(model_locator_id="mloc")
        controller._participating_clients = ["c1"]
        engine = MagicMock()
        engine.get_workspace.return_value.get_run_dir.return_value = "/run"
        engine.get_component.return_value = "not-a-locator"
        fl_ctx = _fl_ctx()
        fl_ctx.get_engine.return_value = engine
        controller.system_panic = MagicMock()

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()

    @patch("os.makedirs")
    @patch("shutil.rmtree")
    @patch("os.path.exists", return_value=False)
    def test_start_controller_bad_formatter_panics(self, mock_exists, mock_rmtree, mock_makedirs):
        controller = _make(formatter_id="fmt")
        controller._participating_clients = ["c1"]
        engine = MagicMock()
        engine.get_workspace.return_value.get_run_dir.return_value = "/run"
        engine.get_component.return_value = "not-a-formatter"
        fl_ctx = _fl_ctx()
        fl_ctx.get_engine.return_value = engine
        controller.system_panic = MagicMock()

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()

    @patch("os.makedirs")
    @patch("shutil.rmtree")
    @patch("os.path.exists", return_value=False)
    def test_start_controller_valid_locator_and_formatter(self, mock_exists, mock_rmtree, mock_makedirs):
        from nvflare.app_common.abstract.formatter import Formatter
        from nvflare.app_common.abstract.model_locator import ModelLocator

        controller = _make(model_locator_id="mloc", formatter_id="fmt")
        controller._participating_clients = ["c1"]
        mloc = MagicMock(spec=ModelLocator)
        fmt = MagicMock(spec=Formatter)
        engine = MagicMock()
        engine.get_workspace.return_value.get_run_dir.return_value = "/run"
        engine.get_component.side_effect = lambda cid: mloc if cid == "mloc" else fmt
        fl_ctx = _fl_ctx()
        fl_ctx.get_engine.return_value = engine

        controller.start_controller(fl_ctx)

        assert controller._model_locator is mloc
        assert controller._formatter is fmt

    @pytest.mark.parametrize(
        "rc", [ReturnCode.MISSING_PEER_CONTEXT, ReturnCode.EXECUTION_RESULT_ERROR, "SOME_UNKNOWN_ERROR"]
    )
    def test_accept_val_result_error_rc_logs_empty_result(self, rc):
        controller = _make()
        result = MagicMock()
        result.get_return_code.return_value = rc
        result.get_header.return_value = None

        controller._accept_val_result("c1", result, _fl_ctx())

        assert controller._eval_results["c1"] == {}

    def test_control_flow_no_clients_quits_before_eval(self):
        controller = _make()
        controller._participating_clients = []
        controller._wait_for_clients_timeout = 0
        controller._model_locator = None
        engine = MagicMock()
        engine.get_clients.return_value = []
        fl_ctx = _fl_ctx()
        fl_ctx.get_engine.return_value = engine
        controller.broadcast = MagicMock()
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, fl_ctx)

        controller.broadcast.assert_not_called()

    def test_control_flow_waits_for_standing_tasks(self):
        controller = _make()
        controller._participating_clients = ["c1"]
        controller._model_locator = None
        controller.get_num_standing_tasks = MagicMock(side_effect=[1, 0])
        controller.broadcast = MagicMock()
        controller.broadcast_and_wait = MagicMock()
        abort_signal = MagicMock()
        abort_signal.triggered = False

        with patch("time.sleep"):
            controller.control_flow(abort_signal, _fl_ctx())

        controller.broadcast_and_wait.assert_called_once()

    def test_locate_server_models_no_models(self):
        controller = _make()
        all_models = MagicMock()
        all_models.data = {}
        controller._model_locator = MagicMock()
        controller._model_locator.locate_model.return_value = all_models
        controller._model_locator.model_names = []

        assert controller._locate_server_models(_fl_ctx()) is True
        assert controller._all_models_dxo is all_models

    def test_control_flow_abort_after_broadcast_skips_cleanup(self):
        controller = _make()
        controller._participating_clients = ["c1"]
        controller._model_locator = None
        controller.get_num_standing_tasks = MagicMock(return_value=0)
        controller.broadcast = MagicMock()
        controller.broadcast_and_wait = MagicMock()

        controller.control_flow(_AbortAfter(trigger_on=1), _fl_ctx())  # aborts at the post-broadcast check

        controller.broadcast_and_wait.assert_not_called()

    def test_control_flow_abort_during_standing_tasks_skips_cleanup(self):
        controller = _make()
        controller._participating_clients = ["c1"]
        controller._model_locator = None
        controller.get_num_standing_tasks = MagicMock(return_value=1)
        controller.broadcast = MagicMock()
        controller.broadcast_and_wait = MagicMock()

        with patch("time.sleep"):
            controller.control_flow(_AbortAfter(trigger_on=2), _fl_ctx())  # aborts inside the wait loop

        controller.broadcast_and_wait.assert_not_called()

    def test_control_flow_waits_then_proceeds_when_clients_appear(self):
        controller = _make(evaluation_task_name="validate")
        controller._participating_clients = []
        controller._wait_for_clients_timeout = 300
        controller._model_locator = None
        controller.get_num_standing_tasks = MagicMock(return_value=0)
        controller.broadcast = MagicMock()
        controller.broadcast_and_wait = MagicMock()
        engine = MagicMock()
        engine.get_clients.side_effect = [[], ["c1"]]  # empty first, then a client appears
        fl_ctx = _fl_ctx()
        fl_ctx.get_engine.return_value = engine
        abort_signal = MagicMock()
        abort_signal.triggered = False

        with patch("time.sleep"):
            controller.control_flow(abort_signal, fl_ctx)

        controller.broadcast.assert_called_once()

    def test_control_flow_abort_during_no_clients_wait(self):
        controller = _make()
        controller._participating_clients = []
        controller._wait_for_clients_timeout = 300  # large, so the loop waits rather than timing out
        controller._model_locator = None
        engine = MagicMock()
        engine.get_clients.return_value = []  # clients never appear
        fl_ctx = _fl_ctx()
        fl_ctx.get_engine.return_value = engine
        controller.broadcast = MagicMock()

        with patch("time.sleep"):
            controller.control_flow(_AbortAfter(trigger_on=1), fl_ctx)  # aborts at the in-loop check

        controller.broadcast.assert_not_called()

    def test_handle_event_get_stats_non_group_collector_raises(self):
        controller = _make()
        controller._formatter = MagicMock()
        collector = MagicMock()  # a truthy collector that is NOT a GroupInfoCollector
        fl_ctx = _fl_ctx()
        fl_ctx.get_prop.side_effect = lambda key, default=None: (
            collector if key == InfoCollector.CTX_KEY_STATS_COLLECTOR else default
        )

        with pytest.raises(TypeError, match="collector must be GroupInfoCollector"):
            controller.handle_event(InfoCollector.EVENT_TYPE_GET_STATS, fl_ctx)
