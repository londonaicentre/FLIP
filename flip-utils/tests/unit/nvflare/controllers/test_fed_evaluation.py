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
from unittest.mock import MagicMock

import pytest
from nvflare.apis.fl_constant import ReturnCode

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
