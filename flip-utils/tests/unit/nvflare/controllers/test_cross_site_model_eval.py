# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import MagicMock

import pytest
from nvflare.apis.dxo import DXO, DataKind
from nvflare.apis.fl_constant import ReturnCode
from nvflare.app_common.workflows.cross_site_model_eval import CrossSiteModelEval as NVFlareCrossSiteModelEval

from flip.constants import FlipTasks
from flip.nvflare.controllers.cross_site_model_eval import CrossSiteModelEval

_MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"


def _make(**kwargs) -> CrossSiteModelEval:
    kwargs.setdefault("model_id", _MODEL_ID)
    controller = CrossSiteModelEval(**kwargs)
    controller.flip = MagicMock()
    for method in ("log_info", "log_debug", "log_error", "log_exception", "fire_event"):
        setattr(controller, method, MagicMock())
    return controller


def _fl_ctx() -> MagicMock:
    """MagicMock FLContext with the two lookups nvflare's log/event helpers validate."""
    fl_ctx = MagicMock()
    fl_ctx.get_peer_context.return_value = None
    fl_ctx.get_prop.return_value = None
    return fl_ctx


class TestCrossSiteModelEval:
    """FLIP's CrossSiteModelEval subclasses stock and adds only: FLIP model-id resolution,
    a POST_VALIDATION cleanup broadcast on normal completion, and reporting client execution
    exceptions to the hub. Everything else (T2 multi-DXO handling, path-safety, saving/loading)
    is inherited from stock rather than vendored.
    """

    def test_subclasses_stock_cross_site_model_eval(self):
        """De-fork guard: must subclass stock so it inherits get_leaf_dxos + resolve_path_under_root."""
        assert issubclass(CrossSiteModelEval, NVFlareCrossSiteModelEval)
        assert isinstance(_make(), NVFlareCrossSiteModelEval)

    def test_init_stores_flip_model_id_and_cleanup_timeout(self):
        controller = _make(cleanup_timeout=42)
        assert controller._model_id_fallback == _MODEL_ID
        assert controller._model_id is None
        assert controller._cleanup_timeout == 42
        assert controller.flip is not None

    def test_init_invalid_task_check_period_raises(self):
        """Stock's type validation is inherited."""
        with pytest.raises(TypeError):
            CrossSiteModelEval(task_check_period="not-a-float", model_id=_MODEL_ID)

    def test_resolve_model_id_uses_fallback_when_fl_ctx_has_no_props(self):
        controller = _make()
        fl_ctx = _fl_ctx()
        assert controller._resolve_model_id(fl_ctx) == _MODEL_ID

    def test_accept_local_model_reports_execution_exception_to_hub(self):
        controller = _make()
        result = MagicMock()
        result.get_return_code.return_value = ReturnCode.EXECUTION_EXCEPTION
        result.get_header.return_value = "boom-traceback"
        fl_ctx = _fl_ctx()

        controller._accept_local_model(client_name="c1", result=result, fl_ctx=fl_ctx)

        controller.flip.send_handled_exception.assert_called_once()
        kwargs = controller.flip.send_handled_exception.call_args.kwargs
        assert kwargs["formatted_exception"] == "boom-traceback"
        assert kwargs["client_name"] == "c1"
        assert kwargs["model_id"] == _MODEL_ID

    def test_accept_val_result_reports_execution_exception_to_hub(self):
        controller = _make()
        result = MagicMock()
        result.get_cookie.return_value = "SRV_best"
        result.get_return_code.return_value = ReturnCode.EXECUTION_EXCEPTION
        result.get_header.return_value = "val-boom"
        fl_ctx = _fl_ctx()

        controller._accept_val_result(client_name="c1", result=result, fl_ctx=fl_ctx)

        controller.flip.send_handled_exception.assert_called_once()
        assert controller.flip.send_handled_exception.call_args.kwargs["formatted_exception"] == "val-boom"

    def test_accept_local_model_ok_delegates_to_stock_save_and_sends_validation(self, tmp_path):
        """OK path delegates to stock, which is T2-aware (get_leaf_dxos) and path-safe."""
        controller = _make()
        controller._cross_val_models_dir = str(tmp_path)
        controller._send_validation_task = MagicMock()

        # METRICS dict (FOBS-serialisable without the numpy decomposer that only registers under
        # full nvflare init); the real WEIGHTS path is covered by the tutorial integration sweep.
        dxo = DXO(data_kind=DataKind.METRICS, data={"accuracy": 0.5})
        result = dxo.to_shareable()  # carries ReturnCode.OK
        fl_ctx = _fl_ctx()

        controller._accept_local_model(client_name="c1", result=result, fl_ctx=fl_ctx)

        # stock saved the single leaf DXO under the client name and triggered its validation
        assert "c1" in controller._client_models
        controller._send_validation_task.assert_called_once_with("c1", fl_ctx)
        controller.flip.send_handled_exception.assert_not_called()

    def _wire_for_control_flow(self, controller, *, standing_tasks=0):
        controller._submit_model_task_name = ""  # skip submit-model broadcast
        controller._model_locator = None  # skip server-model validation
        controller.get_num_standing_tasks = MagicMock(return_value=standing_tasks)
        controller.broadcast = MagicMock()
        controller.broadcast_and_wait = MagicMock()

    def test_control_flow_normal_completion_broadcasts_post_validation_cleanup(self):
        controller = _make()
        controller._participating_clients = ["c1", "c2"]
        self._wire_for_control_flow(controller)
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, _fl_ctx())

        controller.broadcast_and_wait.assert_called_once()
        cleanup_task = controller.broadcast_and_wait.call_args.kwargs["task"]
        assert cleanup_task.name == FlipTasks.POST_VALIDATION.value

    def test_control_flow_abort_skips_post_validation_cleanup(self):
        controller = _make()
        controller._participating_clients = ["c1"]
        self._wire_for_control_flow(controller)
        abort_signal = MagicMock()
        abort_signal.triggered = True  # stock returns early; cleanup must NOT run post-abort

        controller.control_flow(abort_signal, _fl_ctx())

        controller.broadcast_and_wait.assert_not_called()

    def test_control_flow_no_participating_clients_skips_cleanup(self):
        controller = _make()
        controller._participating_clients = []
        controller._wait_for_clients_timeout = 0
        controller._engine = MagicMock()
        controller._engine.get_clients.return_value = []
        self._wire_for_control_flow(controller)
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, _fl_ctx())

        controller.broadcast_and_wait.assert_not_called()
