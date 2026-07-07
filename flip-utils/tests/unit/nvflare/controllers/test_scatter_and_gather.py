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

# FLIP's ScatterAndGather is a thin subclass of NVFLARE's stock ScatterAndGather; these tests cover
# only the FLIP-specific overrides (model_id + snapshot default, the WEIGHT_DIFF->WEIGHTS
# reconstruction, metrics relay, and the ABORTED event). Stock's own behaviour (round loop, arg
# validation, component wiring, memory_gc_rounds) is exercised by NVFLARE's own tests, not here.

from unittest.mock import MagicMock, patch

from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_constant import FLContextKey, ReturnCode
from nvflare.apis.shareable import Shareable
from nvflare.app_common.abstract.aggregator import Aggregator
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_opt.pt.fedopt import PTFedOptModelShareableGenerator

from flip.constants import FlipEvents
from flip.nvflare.controllers.scatter_and_gather import ScatterAndGather

_VALID_MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"


def _ctx():
    # NVFLARE's logging path validates get_peer_context() is an FLContext or None.
    ctx = MagicMock()
    ctx.get_peer_context.return_value = None
    return ctx


class TestInit:
    def test_stores_model_id_as_fallback(self):
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        assert controller._model_id_fallback == _VALID_MODEL_ID
        assert controller._model_id is None

    def test_instantiates_flip_client(self):
        assert ScatterAndGather(model_id=_VALID_MODEL_ID).flip is not None

    def test_disables_per_round_snapshot_by_default(self):
        # FLIP default: no per-round component snapshot (would re-serialise the full model each round).
        assert ScatterAndGather(model_id=_VALID_MODEL_ID)._snapshot_every_n_rounds == 0

    def test_explicit_snapshot_value_wins(self):
        assert ScatterAndGather(model_id=_VALID_MODEL_ID, snapshot_every_n_rounds=3)._snapshot_every_n_rounds == 3

    def test_inherits_memory_gc_cleanup_each_round(self):
        # The OOM fix is inherited from stock, not re-implemented here.
        assert ScatterAndGather(model_id=_VALID_MODEL_ID)._memory_gc_rounds == 1

    def test_passes_stock_args_through_to_super(self):
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID, num_rounds=10, min_clients=5)
        assert controller._num_rounds == 10
        assert controller._min_clients == 5


class TestResolveModelId:
    def test_uses_fallback_when_fl_ctx_has_no_job_meta(self):
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        fl_ctx = MagicMock()
        fl_ctx.get_prop.return_value = None
        assert controller._resolve_model_id(fl_ctx) == _VALID_MODEL_ID


class TestAcceptTrainResult:
    """The FLIP-specific WEIGHT_DIFF->WEIGHTS reconstruction + client-exception reporting."""

    def _controller(self):
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        controller.aggregator = MagicMock(spec=Aggregator)
        controller.aggregator.accept.return_value = True
        controller.fire_event = MagicMock()
        controller.log_info = MagicMock()
        controller.log_error = MagicMock()
        controller._current_round = 2
        return controller

    def test_converts_weight_diff_to_weights_for_non_fedopt(self):
        controller = self._controller()
        controller._global_weights = {"weights": {"w1": 1.0, "w2": 2.0}}

        result = DXO(
            data_kind=DataKind.WEIGHT_DIFF, data={"w1": 0.25, "w2": -0.5}, meta={"origin": "client"}
        ).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, 2)

        accepted = controller._accept_train_result(client_name="site-1", result=result, fl_ctx=_ctx())

        assert accepted is True
        sent_dxo = from_shareable(controller.aggregator.accept.call_args[0][0])
        assert sent_dxo.data_kind == DataKind.WEIGHTS
        assert sent_dxo.data == {"w1": 1.25, "w2": 1.5}
        assert sent_dxo.meta == {"origin": "client"}
        controller.log_error.assert_not_called()

    def test_partial_weight_diff_is_partial_safe(self):
        # A head-only diff (frozen-backbone fine-tune, FLIP#684): missing keys keep their global value.
        controller = self._controller()
        controller._current_round = 3
        controller._global_weights = {"weights": {"w1": 1.0, "w2": 2.0}}

        result = DXO(data_kind=DataKind.WEIGHT_DIFF, data={"w1": 0.1}).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, 3)

        accepted = controller._accept_train_result(client_name="site-1", result=result, fl_ctx=_ctx())

        assert accepted is True
        assert not any(
            "Error while adding client WEIGHT_DIFF" in call.args[1] for call in controller.log_error.call_args_list
        )
        reconstructed = from_shareable(result).data
        assert reconstructed["w1"] == 1.1  # updated
        assert reconstructed["w2"] == 2.0  # frozen key preserved from global

    def test_logs_error_when_data_kind_is_not_weight_diff(self):
        controller = self._controller()
        controller._current_round = 1
        controller._global_weights = {"weights": {"w1": 1.0}}

        result = DXO(data_kind=DataKind.WEIGHTS, data={"w1": 1.0}).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, 1)

        controller._accept_train_result(client_name="site-1", result=result, fl_ctx=_ctx())

        controller.log_error.assert_called_once()
        assert "not of type WEIGHT_DIFF" in controller.log_error.call_args[0][1]

    def test_reconstruction_error_passes_original_result_through_and_logs(self):
        # Corrupt global weights (no "weights" entry) → the reconstruction raises; the original
        # WEIGHT_DIFF result must be passed through unchanged so the base class applies its own
        # handling, with the failure logged rather than swallowed.
        controller = self._controller()
        controller._current_round = 5
        controller._global_weights = {}

        result = DXO(data_kind=DataKind.WEIGHT_DIFF, data={"w1": 0.1}).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, 5)

        accepted = controller._accept_train_result(client_name="site-1", result=result, fl_ctx=_ctx())

        assert accepted is True
        controller.log_error.assert_called_once()
        assert "Error while adding client WEIGHT_DIFF" in controller.log_error.call_args[0][1]
        sent_dxo = from_shareable(controller.aggregator.accept.call_args[0][0])
        assert sent_dxo.data_kind == DataKind.WEIGHT_DIFF  # not converted
        assert sent_dxo.data == {"w1": 0.1}

    def test_keeps_weight_diff_for_fedopt_aggregator(self):
        class _FedOptAggregatorStub(PTFedOptModelShareableGenerator):
            def __init__(self):
                self.accept = MagicMock(return_value=True)

        controller = self._controller()
        controller.aggregator = _FedOptAggregatorStub()
        controller._current_round = 4
        controller._global_weights = {"weights": {"w1": 10.0}}

        result = DXO(data_kind=DataKind.WEIGHT_DIFF, data={"w1": -0.75}, meta={"origin": "client"}).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, 4)

        accepted = controller._accept_train_result(client_name="site-1", result=result, fl_ctx=_ctx())

        assert accepted is True
        sent_dxo = from_shareable(controller.aggregator.accept.call_args[0][0])
        assert sent_dxo.data_kind == DataKind.WEIGHT_DIFF  # not converted
        assert sent_dxo.data == {"w1": -0.75}
        controller.log_error.assert_not_called()

    def test_reports_handled_execution_exception_to_hub(self):
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID, ignore_result_error=False)
        controller.flip = MagicMock()
        controller.system_panic = MagicMock()
        controller.log_error = MagicMock()
        controller.log_warning = MagicMock()
        # Attrs the base class reads on the non-OK path (set during a real run's control_flow).
        controller._current_failed_clients = set()
        controller._current_num_targets = 1

        result = Shareable()
        result.set_return_code(ReturnCode.EXECUTION_EXCEPTION)
        result.set_header("exception", "boom traceback")

        controller._accept_train_result(client_name="site-1", result=result, fl_ctx=_ctx())

        # FLIP forwards the client-side traceback to the hub with the resolved model_id.
        controller.flip.send_handled_exception.assert_called_once()
        assert controller.flip.send_handled_exception.call_args.kwargs["model_id"] == _VALID_MODEL_ID


class TestHandleEvent:
    def test_send_result_forwards_metric_with_resolved_model_id(self):
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        controller.log_error = MagicMock()
        controller._current_round = 2

        fl_ctx = MagicMock()
        fl_ctx.get_prop.side_effect = lambda key, default=None: (
            "metrics-shareable"
            if key == FLContextKey.EVENT_DATA
            else (None if key == FLContextKey.JOB_META else default)
        )

        with patch("flip.nvflare.controllers.scatter_and_gather.handle_metrics_event") as mock_metrics:
            controller.handle_event(FlipEvents.SEND_RESULT, fl_ctx)

        mock_metrics.assert_called_once()
        args = mock_metrics.call_args[0]
        assert args[0] == "metrics-shareable"
        assert args[1] == 2
        assert args[2] == _VALID_MODEL_ID

    def test_send_result_without_data_logs_error(self):
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        controller.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_prop.return_value = None

        with patch("flip.nvflare.controllers.scatter_and_gather.handle_metrics_event") as mock_metrics:
            controller.handle_event(FlipEvents.SEND_RESULT, fl_ctx)

        mock_metrics.assert_not_called()
        controller.log_error.assert_called_once()

    def test_send_result_skips_relay_when_round_not_started(self):
        # A multi-phase job (e.g. the diffusion AE/DM split) wires several ScatterAndGather
        # controllers. SEND_RESULT is broadcast to every controller, but a controller whose
        # control_flow has not run yet has _current_round is None (inherited from stock). It must
        # not relay another controller's metric — previously it passed None into handle_metrics_event,
        # raising "global_round must be type int but got NoneType".
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        controller.log_error = MagicMock()
        controller._current_round = None  # this instance has not started its training loop

        fl_ctx = MagicMock()
        fl_ctx.get_prop.side_effect = lambda key, default=None: (
            "metrics-shareable"
            if key == FLContextKey.EVENT_DATA
            else (None if key == FLContextKey.JOB_META else default)
        )

        with patch("flip.nvflare.controllers.scatter_and_gather.handle_metrics_event") as mock_metrics:
            controller.handle_event(FlipEvents.SEND_RESULT, fl_ctx)  # must not raise

        mock_metrics.assert_not_called()


class TestFedJobSerialisation:
    """FedJob/recipe serialisation must capture stock's __init__ args, not just model_id.

    NVFLARE's ``get_component_init_parameters`` walks a component's base classes to inherit their
    __init__ params ONLY when the subclass __init__ declares BOTH ``*args`` and ``**kwargs``. A
    ``**kwargs``-only subclass silently drops the stock args (persistor_id, aggregator_id, …), so a
    recipe's ScatterAndGather ends up with no persistor -> an empty round-0 global model -> the
    Client-API trainer's ``load_state_dict({})`` crashes. This guards that regression.
    """

    def test_init_exposes_stock_args_for_recipe_serialisation(self):
        from nvflare.fuel.utils.class_utils import get_component_init_parameters

        params = set(get_component_init_parameters(ScatterAndGather(model_id=_VALID_MODEL_ID)))
        for arg in ("persistor_id", "aggregator_id", "shareable_generator_id", "num_rounds", "min_clients"):
            assert arg in params, f"'{arg}' dropped by FedJob serialisation (thin-subclass **kwargs trap)"


class TestCheckAbortSignal:
    def test_fires_flip_aborted_event_when_aborted(self):
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        controller.log_info = MagicMock()
        controller.fire_event = MagicMock()
        controller._current_round = 3

        fl_ctx = _ctx()
        abort_signal = MagicMock()
        abort_signal.triggered = True

        assert controller._check_abort_signal(fl_ctx, abort_signal) is True
        controller.fire_event.assert_called_once_with(FlipEvents.ABORTED, fl_ctx)

    def test_does_not_fire_when_not_aborted(self):
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        controller.fire_event = MagicMock()

        abort_signal = MagicMock()
        abort_signal.triggered = False

        assert controller._check_abort_signal(_ctx(), abort_signal) is False
        controller.fire_event.assert_not_called()
