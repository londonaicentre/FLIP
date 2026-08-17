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

import numpy as np
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_constant import FLContextKey, ReturnCode
from nvflare.apis.shareable import Shareable
from nvflare.app_common.abstract.aggregator import Aggregator
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.workflows.scatter_and_gather import ScatterAndGather as NVFlareScatterAndGather

from flip.constants import FlipEvents, FlipProps
from flip.nvflare.controllers.scatter_and_gather import ScatterAndGather
from flip.schemas import FLLogEvent

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

    def test_keeps_weight_diff_for_weight_diff_aggregator(self):
        """A FedOpt-style server (the ``fed_opt`` job type) wires an aggregator that consumes
        WEIGHT_DIFF directly; the conversion must be skipped or the server optimizer step is
        silently bypassed. The old guard isinstance-checked the aggregator against the FedOpt
        *shareable generator* class — which no real config ever satisfies, so the conversion ran
        unconditionally; the fixed guard keys on the aggregator's ``expected_data_kind``."""
        controller = self._controller()
        aggregator = MagicMock()
        # The runtime shape: InTimeAccumulateWeightedAggregator normalises a single expected kind
        # into the {dxo_name: DataKind} dict form ({"" : kind}) — the guard must match it, not just
        # the plain enum a hand-constructed aggregator carries.
        aggregator.expected_data_kind = {"": DataKind.WEIGHT_DIFF}
        aggregator.accept = MagicMock(return_value=True)
        controller.aggregator = aggregator
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


class TestClientResultTelemetry:
    """Accepted client results are relayed to the hub as CLIENT_RESULT_RECEIVED facts."""

    def _controller(self):
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        controller.aggregator = MagicMock(spec=Aggregator)
        controller.aggregator.accept.return_value = True
        controller.fire_event = MagicMock()
        controller.log_info = MagicMock()
        controller.log_error = MagicMock()
        controller.flip = MagicMock()
        controller._current_round = 2
        controller._current_num_targets = 3
        controller._global_weights = {"weights": {"w1": np.zeros(4, dtype=np.float32)}}
        return controller

    def _ok_result(self, round_no=2):
        result = DXO(data_kind=DataKind.WEIGHT_DIFF, data={"w1": np.zeros(4, dtype=np.float32)}).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, round_no)
        return result

    def test_accepted_result_emits_client_result_event(self):
        controller = self._controller()

        controller._accept_train_result(client_name="Trust_1", result=self._ok_result(), fl_ctx=_ctx())

        controller.flip.send_event.assert_called_once_with(
            model_id=_VALID_MODEL_ID,
            event_type=FLLogEvent.CLIENT_RESULT_RECEIVED,
            global_round=3,  # _current_round is 0-based; the wire contract is 1-based
            client_name="Trust_1",
            details={"size_bytes": 16},  # 4 x float32
        )

    def test_acceptance_counts_are_shared_as_sticky_props(self):
        controller = self._controller()
        fl_ctx = _ctx()

        controller._accept_train_result(client_name="Trust_1", result=self._ok_result(), fl_ctx=fl_ctx)
        controller._accept_train_result(client_name="Trust_2", result=self._ok_result(), fl_ctx=fl_ctx)

        prop_calls = {call.args[0]: call.args[1] for call in fl_ctx.set_prop.call_args_list}
        assert prop_calls[FlipProps.ROUND_RETURNED] == 2
        assert prop_calls[FlipProps.ROUND_EXPECTED] == 3

    def test_same_client_twice_counts_once(self):
        controller = self._controller()
        fl_ctx = _ctx()

        controller._accept_train_result(client_name="Trust_1", result=self._ok_result(), fl_ctx=fl_ctx)
        controller._accept_train_result(client_name="Trust_1", result=self._ok_result(), fl_ctx=fl_ctx)

        prop_calls = {call.args[0]: call.args[1] for call in fl_ctx.set_prop.call_args_list}
        assert prop_calls[FlipProps.ROUND_RETURNED] == 1

    def test_aggregator_rejected_result_emits_no_event_and_no_counts(self):
        """A rejected contribution (e.g. a stale contribution_round cookie) is not an
        accepted upload: no CLIENT_RESULT_RECEIVED, no bump of the sticky counts."""
        controller = self._controller()
        controller.aggregator.accept.return_value = False
        fl_ctx = _ctx()

        accepted = controller._accept_train_result(client_name="Trust_1", result=self._ok_result(), fl_ctx=fl_ctx)

        assert accepted is False
        controller.flip.send_event.assert_not_called()
        assert FlipProps.ROUND_RETURNED not in {call.args[0] for call in fl_ctx.set_prop.call_args_list}

    def test_unknown_task_result_emits_no_event_and_no_counts(self):
        """A late result routed via process_result_of_unknown_task carries an earlier
        round's weights — it must not be reported against the current round."""
        controller = self._controller()
        fl_ctx = _ctx()

        accepted = controller._accept_train_result(
            client_name="Trust_1", result=self._ok_result(), fl_ctx=fl_ctx, is_unknown_task=True
        )

        assert accepted is True  # stock still forwards it to the aggregator
        controller.flip.send_event.assert_not_called()
        assert FlipProps.ROUND_RETURNED not in {call.args[0] for call in fl_ctx.set_prop.call_args_list}

    def test_reported_size_is_the_original_diff_not_the_reconstruction(self):
        """A head-only partial diff must report its own size, not the full model's —
        the size is probed before _diff_to_weights rewrites the shareable."""
        controller = self._controller()
        controller._global_weights = {
            "weights": {"w1": np.zeros(4, dtype=np.float32), "w2": np.zeros(4, dtype=np.float32)}
        }
        result = DXO(data_kind=DataKind.WEIGHT_DIFF, data={"w1": np.zeros(4, dtype=np.float32)}).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, 2)

        controller._accept_train_result(client_name="Trust_1", result=result, fl_ctx=_ctx())

        assert controller.flip.send_event.call_args.kwargs["details"] == {"size_bytes": 16}

    def test_telemetry_failure_never_blocks_acceptance(self):
        """The result must be accepted even if the hub relay explodes."""
        controller = self._controller()
        controller.flip.send_event.side_effect = Exception("hub down")

        accepted = controller._accept_train_result(client_name="Trust_1", result=self._ok_result(), fl_ctx=_ctx())

        assert accepted is True

    def test_exception_result_does_not_emit_client_result(self):
        controller = self._controller()
        result = Shareable()
        result.set_return_code(ReturnCode.EXECUTION_EXCEPTION)
        result.set_header("exception", "boom")

        with patch.object(
            NVFlareScatterAndGather, "_accept_train_result", return_value=False
        ):
            controller._accept_train_result(client_name="Trust_1", result=result, fl_ctx=_ctx())

        controller.flip.send_event.assert_not_called()
        controller.flip.send_handled_exception.assert_called_once()
