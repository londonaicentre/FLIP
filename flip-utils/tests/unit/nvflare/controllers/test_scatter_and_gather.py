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
# only the FLIP-specific overrides (model_id + snapshot default, hub relays for failed and accepted
# client results, the data-kind mismatch report, the zero-acceptance abort, metrics relay, and the
# ABORTED event). Stock's own behaviour (round loop, arg validation, component wiring,
# memory_gc_rounds) is exercised by NVFLARE's own tests, not here.

from unittest.mock import MagicMock, patch

import numpy as np
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_constant import FLContextKey, ReturnCode
from nvflare.apis.shareable import Shareable
from nvflare.app_common.abstract.aggregator import Aggregator
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.app_event_type import AppEventType
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
    """The FLIP-specific pass-through-to-aggregator semantics + failed-task reporting."""

    def _controller(self):
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        controller.aggregator = MagicMock(spec=Aggregator)
        controller.aggregator.accept.return_value = True
        controller.fire_event = MagicMock()
        controller.log_info = MagicMock()
        controller.log_error = MagicMock()
        controller._current_round = 2
        return controller

    def test_weight_diff_reaches_aggregator_untouched(self):
        """Stock semantics: the client's (possibly partial, head-only) WEIGHT_DIFF is aggregated
        directly — the fork must not transform it. The old DIFF→WEIGHTS reconstruction existed to
        bridge the legacy templates' WEIGHTS-expecting aggregator (FLIP#684) and silently broke
        FedOpt (which consumes the diff through the server optimizer); with every template now
        aggregating diffs (the stock aggregator default), any transformation here is a bug."""
        controller = self._controller()
        aggregator = MagicMock()
        aggregator.expected_data_kind = {"": DataKind.WEIGHT_DIFF}
        aggregator.accept = MagicMock(return_value=True)
        controller.aggregator = aggregator

        result = DXO(
            data_kind=DataKind.WEIGHT_DIFF, data={"head.w": 0.25}, meta={"origin": "client"}
        ).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, 2)

        accepted = controller._accept_train_result(client_name="site-1", result=result, fl_ctx=_ctx())

        assert accepted is True
        sent_dxo = from_shareable(controller.aggregator.accept.call_args[0][0])
        assert sent_dxo.data_kind == DataKind.WEIGHT_DIFF
        assert sent_dxo.data == {"head.w": 0.25}
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

    def test_non_ok_result_without_header_relays_pointed_fallback(self):
        """A Client-API script that dies pre-flare.init returns a bare TASK_ABORTED with no
        "exception" header (only the retired legacy executors ever set one). The researcher must
        still get a hub-visible pointer at the trust-side logs, not just a generic panic."""
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID, ignore_result_error=False)
        controller.flip = MagicMock()
        controller.system_panic = MagicMock()
        controller.log_error = MagicMock()
        controller.log_warning = MagicMock()
        controller._current_failed_clients = set()
        controller._current_num_targets = 1

        result = Shareable()
        result.set_return_code(ReturnCode.TASK_ABORTED)

        controller._accept_train_result(client_name="site-1", result=result, fl_ctx=_ctx())

        controller.flip.send_handled_exception.assert_called_once()
        relayed = controller.flip.send_handled_exception.call_args.kwargs["formatted_exception"]
        assert ReturnCode.TASK_ABORTED in relayed
        assert "fl-client" in relayed

    def test_full_weights_update_reports_actionable_mismatch(self):
        """A trainer sending params_type="FULL" (NVFLARE's default when omitted) produces WEIGHTS,
        which the WEIGHT_DIFF-expecting aggregator rejects with one server-log line. FLIP relays
        an actionable message to the hub naming the params_type='DIFF' fix."""
        controller = self._controller()
        controller.flip = MagicMock()
        controller.aggregator.expected_data_kind = {"": DataKind.WEIGHT_DIFF}
        controller.aggregator.accept = MagicMock(return_value=False)

        result = DXO(data_kind=DataKind.WEIGHTS, data={"w": np.ones(2)}).to_shareable()
        controller._accept_train_result(client_name="site-1", result=result, fl_ctx=_ctx())

        controller.flip.send_handled_exception.assert_called_once()
        message = controller.flip.send_handled_exception.call_args.kwargs["formatted_exception"]
        assert "params_type='DIFF'" in message
        assert str(DataKind.WEIGHTS) in message
        controller.log_error.assert_called()
        assert 2 in controller._round_kind_mismatches

    def test_mismatch_reported_once_per_round(self):
        controller = self._controller()
        controller.flip = MagicMock()
        controller.aggregator.expected_data_kind = DataKind.WEIGHT_DIFF
        controller.aggregator.accept = MagicMock(return_value=False)

        result = DXO(data_kind=DataKind.WEIGHTS, data={"w": np.ones(2)}).to_shareable()
        controller._accept_train_result(client_name="site-1", result=result, fl_ctx=_ctx())
        controller._accept_train_result(client_name="site-2", result=result, fl_ctx=_ctx())

        controller.flip.send_handled_exception.assert_called_once()

    def test_matching_diff_reports_no_mismatch(self):
        controller = self._controller()
        controller.flip = MagicMock()
        controller.aggregator.expected_data_kind = {"": DataKind.WEIGHT_DIFF}

        result = DXO(data_kind=DataKind.WEIGHT_DIFF, data={"w": np.ones(2)}).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, 2)
        controller._accept_train_result(client_name="site-1", result=result, fl_ctx=_ctx())

        controller.flip.send_handled_exception.assert_not_called()
        assert controller._round_kind_mismatches == set()

    def test_kind_probe_failure_never_blocks_acceptance(self):
        """The mismatch probe is best-effort: an OK result it cannot parse (no DXO payload, so
        from_shareable raises) is still handed to the aggregator — which remains the authority on
        rejection — with the probe failure demoted to a debug log, not a hub-visible error."""
        controller = self._controller()
        controller.flip = MagicMock()
        controller.log_debug = MagicMock()
        controller.aggregator.expected_data_kind = {"": DataKind.WEIGHT_DIFF}

        accepted = controller._accept_train_result(client_name="site-1", result=Shareable(), fl_ctx=_ctx())

        assert accepted is True
        controller.flip.send_handled_exception.assert_not_called()
        assert controller._round_kind_mismatches == set()
        debug_messages = [call.args[1] for call in controller.log_debug.call_args_list]
        assert any(m.startswith("Could not probe the client update's data kind") for m in debug_messages)


class TestZeroAcceptancePanic:
    """A training round about to aggregate zero accepted results must abort the job loudly —
    stock applies the empty aggregate as a no-op and completes with an untrained model."""

    def _controller(self, round_no=1, targets=2):
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        controller.flip = MagicMock()
        controller.system_panic = MagicMock()
        controller.log_error = MagicMock()
        controller._current_round = round_no
        controller._current_num_targets = targets
        return controller

    def test_panics_and_relays_when_round_accepted_nothing(self):
        controller = self._controller()
        with patch.object(NVFlareScatterAndGather, "handle_event"):
            controller.handle_event(AppEventType.BEFORE_AGGREGATION, _ctx())
        controller.system_panic.assert_called_once()
        controller.flip.send_handled_exception.assert_called_once()
        reason = controller.system_panic.call_args.args[0]
        assert "accepted 0 of 2" in reason

    def test_panic_names_the_data_kind_cause_when_recorded(self):
        controller = self._controller()
        controller._round_kind_mismatches.add(1)
        with patch.object(NVFlareScatterAndGather, "handle_event"):
            controller.handle_event(AppEventType.BEFORE_AGGREGATION, _ctx())
        assert "data kind" in controller.system_panic.call_args.args[0]

    def test_no_panic_when_round_has_acceptances(self):
        controller = self._controller()
        controller._round_acceptances[1] = {"site-1"}
        with patch.object(NVFlareScatterAndGather, "handle_event"):
            controller.handle_event(AppEventType.BEFORE_AGGREGATION, _ctx())
        controller.system_panic.assert_not_called()

    def test_no_panic_for_a_not_yet_started_sibling_controller(self):
        """Multi-phase jobs broadcast every event to every controller; one whose control_flow has
        not started (_current_round is None, stock's init value) must not panic on the active
        controller's rounds."""
        controller = self._controller(round_no=1)
        controller._current_round = None
        with patch.object(NVFlareScatterAndGather, "handle_event"):
            controller.handle_event(AppEventType.BEFORE_AGGREGATION, _ctx())
        controller.system_panic.assert_not_called()

    def test_relay_failure_still_panics(self):
        controller = self._controller()
        controller.flip.send_handled_exception.side_effect = RuntimeError("hub down")
        with patch.object(NVFlareScatterAndGather, "handle_event"):
            controller.handle_event(AppEventType.BEFORE_AGGREGATION, _ctx())
        controller.system_panic.assert_called_once()


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

    def test_reported_size_is_the_partial_diff_itself(self):
        """A head-only partial diff must report its own bytes, not the full model's — the size
        probe reads the client's shareable as sent (nothing rewrites it on the way in)."""
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
