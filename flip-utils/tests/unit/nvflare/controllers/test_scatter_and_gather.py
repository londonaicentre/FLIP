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


from unittest.mock import MagicMock, patch

import pytest
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_constant import FLContextKey, ReturnCode
from nvflare.apis.shareable import Shareable
from nvflare.app_common.abstract.aggregator import Aggregator
from nvflare.app_common.abstract.shareable_generator import ShareableGenerator
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_opt.pt.fedopt import PTFedOptModelShareableGenerator

from flip.constants import FlipEvents
from flip.nvflare.controllers.scatter_and_gather import ScatterAndGather

_VALID_MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"


class TestScatterAndGather:
    def test_init_with_valid_uuid(self):
        """Test initialization with valid UUID stores it as fallback"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id)
        assert controller._model_id_fallback == model_id
        assert controller._model_id is None

    def test_resolve_model_id_uses_fallback_when_fl_ctx_has_no_custom_props(self):
        """Lazy resolution returns the constructor UUID when fl_ctx has no custom_props."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id)
        fl_ctx = MagicMock()
        fl_ctx.get_prop.return_value = None
        result = controller._resolve_model_id(fl_ctx)
        assert result == model_id

    def test_init_with_custom_min_clients(self):
        """Test initialization with custom min_clients"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id, min_clients=5)
        assert controller._min_clients == 5

    def test_init_with_zero_min_clients(self):
        """Test initialization with zero min_clients is valid (>= 0 check)"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id, min_clients=0)
        assert controller._min_clients == 0

    def test_init_with_custom_num_rounds(self):
        """Test initialization with custom num_rounds"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id, num_rounds=10)
        assert controller._num_rounds == 10

    def test_init_with_negative_num_rounds_raises_error(self):
        """Test initialization with negative num_rounds raises Exception"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(Exception, match="num_rounds must be greater"):
            ScatterAndGather(model_id=model_id, num_rounds=-1)

    def test_init_with_custom_start_round(self):
        """Test initialization with custom start_round"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id, start_round=3)
        assert controller._start_round == 3

    def test_init_with_negative_start_round_raises_error(self):
        """Test initialization with negative start_round raises Exception"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(Exception, match="start_round must be greater"):
            ScatterAndGather(model_id=model_id, start_round=-1)

    def test_init_with_custom_wait_time(self):
        """Test initialization with custom wait_time_after_min_received"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id, wait_time_after_min_received=20)
        assert controller._wait_time_after_min_received == 20

    def test_init_with_negative_wait_time_raises_error(self):
        """Test initialization with negative wait_time raises Exception"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(Exception, match="wait_time_after_min_received must be greater"):
            ScatterAndGather(model_id=model_id, wait_time_after_min_received=-1)

    def test_init_with_custom_train_timeout(self):
        """Test initialization with custom train_timeout"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id, train_timeout=300)
        assert controller._train_timeout == 300

    def test_init_with_negative_train_timeout_raises_error(self):
        """Test initialization with negative train_timeout raises Exception"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(Exception, match="train_timeout must be greater"):
            ScatterAndGather(model_id=model_id, train_timeout=-1)

    def test_init_with_custom_fatal_error_delay(self):
        """Test initialization with custom fatal_error_delay"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id, fatal_error_delay=10)
        assert controller._fatal_error_delay == 10

    def test_init_with_negative_fatal_error_delay(self):
        """Test initialization with negative fatal_error_delay is allowed (no validation)"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id, fatal_error_delay=-1)
        assert controller._fatal_error_delay == -1

    def test_init_with_custom_persist_every_n_rounds(self):
        """Test initialization with custom persist_every_n_rounds"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id, persist_every_n_rounds=5)
        assert controller._persist_every_n_rounds == 5

    def test_init_with_negative_persist_every_n_rounds_raises_error(self):
        """Test initialization with negative persist_every_n_rounds raises Exception"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(Exception, match="persist_every_n_rounds must be greater"):
            ScatterAndGather(model_id=model_id, persist_every_n_rounds=-1)

    def test_init_with_boolean_ignore_result_error(self):
        """Test initialization with ignore_result_error flag"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id, ignore_result_error=True)
        assert controller._ignore_result_error is True

    def test_init_with_custom_task_names(self):
        """Test initialization with custom task names"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id, train_task_name="custom_train")
        assert controller.train_task_name == "custom_train"

    def test_init_with_custom_component_ids(self):
        """Test initialization with custom component IDs"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(
            model_id=model_id,
            aggregator_id="my_aggregator",
            persistor_id="my_persistor",
            shareable_generator_id="my_generator",
        )
        assert controller.aggregator_id == "my_aggregator"
        assert controller.persistor_id == "my_persistor"
        assert controller.shareable_generator_id == "my_generator"

    def test_start_controller_no_engine(self):
        """Test start_controller when engine is not found"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id)
        controller.system_panic = MagicMock()
        controller.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value = None

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()
        assert "Engine not found" in str(controller.system_panic.call_args)
        assert controller._phase == AppConstants.PHASE_INIT

    def test_start_controller_invalid_aggregator(self):
        """Test start_controller with invalid aggregator component"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id, aggregator_id="bad_aggregator")
        controller.system_panic = MagicMock()
        controller.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine
        # Return non-Aggregator object
        engine.get_component.return_value = "not_an_aggregator"

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()
        assert "must be an Aggregator type object" in str(controller.system_panic.call_args)

    def test_start_controller_invalid_shareable_generator(self):
        """Test start_controller with invalid shareable generator"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id)
        controller.system_panic = MagicMock()
        controller.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        # Mock aggregator as valid but shareable_gen as invalid
        mock_aggregator = MagicMock(spec=Aggregator)

        def side_effect(component_id):
            if component_id == controller.aggregator_id:
                return mock_aggregator
            else:
                return "not_a_shareable_generator"

        engine.get_component.side_effect = side_effect

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()
        assert "ShareableGenerator" in str(controller.system_panic.call_args)

    def test_start_controller_invalid_persistor(self):
        """Test start_controller with invalid persistor"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id)
        controller.system_panic = MagicMock()
        controller.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        # Mock aggregator and shareable_gen as valid but persistor as invalid
        mock_aggregator = MagicMock(spec=Aggregator)
        mock_shareable_gen = MagicMock(spec=ShareableGenerator)

        def side_effect(component_id):
            if component_id == controller.aggregator_id:
                return mock_aggregator
            elif component_id == controller.shareable_generator_id:
                return mock_shareable_gen
            else:
                return "not_a_persistor"

        engine.get_component.side_effect = side_effect

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()
        assert "LearnablePersistor" in str(controller.system_panic.call_args)

    def test_stop_controller(self):
        """Test stop_controller"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id)
        controller.cancel_all_tasks = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        controller.stop_controller(fl_ctx)

        assert controller._phase == AppConstants.PHASE_FINISHED
        controller.cancel_all_tasks.assert_called_once()

    def test_accept_train_result_converts_weight_diff_to_weights_for_non_fedopt(self):
        """WEIGHT_DIFF should be converted to WEIGHTS before aggregator.accept for non-FedOpt aggregators."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id)
        controller.aggregator = MagicMock(spec=Aggregator)
        controller.aggregator.accept.return_value = True
        controller.fire_event = MagicMock()
        controller.log_info = MagicMock()
        controller.log_error = MagicMock()
        controller._current_round = 2
        controller._global_weights = {"weights": {"w1": 1.0, "w2": 2.0}}

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        result = DXO(
            data_kind=DataKind.WEIGHT_DIFF,
            data={"w1": 0.25, "w2": -0.5},
            meta={"origin": "client"},
        ).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, 2)

        accepted = controller._accept_train_result(client_name="site-1", result=result, fl_ctx=fl_ctx)

        assert accepted is True
        sent_shareable = controller.aggregator.accept.call_args[0][0]
        sent_dxo = from_shareable(sent_shareable)
        assert sent_dxo.data_kind == DataKind.WEIGHTS
        assert sent_dxo.data == {"w1": 1.25, "w2": 1.5}
        assert sent_dxo.meta == {"origin": "client"}
        controller.log_error.assert_not_called()

    def test_accept_train_result_logs_error_when_data_kind_is_not_weight_diff(self):
        """Non-WEIGHT_DIFF inputs should log an error in non-FedOpt mode."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id)
        controller.aggregator = MagicMock(spec=Aggregator)
        controller.aggregator.accept.return_value = True
        controller.fire_event = MagicMock()
        controller.log_info = MagicMock()
        controller.log_error = MagicMock()
        controller._current_round = 1
        controller._global_weights = {"weights": {"w1": 1.0}}

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        result = DXO(data_kind=DataKind.WEIGHTS, data={"w1": 1.0}).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, 1)

        accepted = controller._accept_train_result(client_name="site-1", result=result, fl_ctx=fl_ctx)

        assert accepted is True
        controller.log_error.assert_called_once()
        assert "not of type WEIGHT_DIFF" in controller.log_error.call_args[0][1]

    def test_accept_train_result_keeps_weight_diff_for_fedopt_aggregator(self):
        """FedOpt aggregators should receive WEIGHT_DIFF payloads without conversion."""

        class _FedOptAggregatorStub(PTFedOptModelShareableGenerator):
            def __init__(self):
                self.accept = MagicMock(return_value=True)

        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id)
        controller.aggregator = _FedOptAggregatorStub()
        controller.fire_event = MagicMock()
        controller.log_info = MagicMock()
        controller.log_error = MagicMock()
        controller._current_round = 4
        controller._global_weights = {"weights": {"w1": 10.0}}

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        result = DXO(
            data_kind=DataKind.WEIGHT_DIFF,
            data={"w1": -0.75},
            meta={"origin": "client"},
        ).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, 4)

        accepted = controller._accept_train_result(client_name="site-1", result=result, fl_ctx=fl_ctx)

        assert accepted is True
        sent_shareable = controller.aggregator.accept.call_args[0][0]
        sent_dxo = from_shareable(sent_shareable)
        assert sent_dxo.data_kind == DataKind.WEIGHT_DIFF
        assert sent_dxo.data == {"w1": -0.75}
        assert sent_dxo.meta == {"origin": "client"}
        controller.log_error.assert_not_called()

    @pytest.mark.parametrize(
        ("bad_kwargs", "frag"),
        [
            ({"aggregator_id": 123}, "aggregator_id"),
            ({"persistor_id": 123}, "persistor_id"),
            ({"shareable_generator_id": 123}, "shareable_generator_id"),
            ({"train_task_name": 123}, "train_task_name"),
            ({"ignore_result_error": "nope"}, "ignore_result_error"),
        ],
    )
    def test_init_rejects_wrong_arg_types(self, bad_kwargs, frag):
        """Each id/flag arg is type-checked at construction and raises TypeError on the wrong type."""
        with pytest.raises(TypeError, match=frag):
            ScatterAndGather(model_id=_VALID_MODEL_ID, **bad_kwargs)

    def test_handle_event_send_result_forwards_resolved_model_id(self):
        """On SEND_RESULT the controller forwards the metric with the lazily-resolved model_id."""
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        controller.log_error = MagicMock()
        controller._current_round = 2

        fl_ctx = MagicMock()
        # EVENT_DATA present; JOB_META absent so _resolve_model_id falls back to the constructor UUID.
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

    def test_handle_event_send_result_no_data_logs_error(self):
        """SEND_RESULT with no EVENT_DATA logs an error and does not forward."""
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID)
        controller.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_prop.return_value = None

        with patch("flip.nvflare.controllers.scatter_and_gather.handle_metrics_event") as mock_metrics:
            controller.handle_event(FlipEvents.SEND_RESULT, fl_ctx)

        mock_metrics.assert_not_called()
        controller.log_error.assert_called_once()

    def test_accept_train_result_reports_handled_execution_exception(self):
        """An EXECUTION_EXCEPTION result with an exception header is forwarded to the hub with the
        lazily-resolved model_id, then the controller panics."""
        controller = ScatterAndGather(model_id=_VALID_MODEL_ID, ignore_result_error=False)
        controller.flip = MagicMock()
        controller.system_panic = MagicMock()
        controller.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_prop.return_value = None  # JOB_META absent -> fallback resolves the model_id

        result = Shareable()
        result.set_return_code(ReturnCode.EXECUTION_EXCEPTION)
        result.set_header("exception", "boom traceback")

        accepted = controller._accept_train_result(client_name="site-1", result=result, fl_ctx=fl_ctx)

        assert accepted is False
        controller.flip.send_handled_exception.assert_called_once()
        assert controller.flip.send_handled_exception.call_args.kwargs["model_id"] == _VALID_MODEL_ID
        controller.system_panic.assert_called_once()

    def test_accept_train_result_partial_weight_diff_is_partial_safe(self):
        """A partial WEIGHT_DIFF (only some keys — e.g. a frozen-backbone head-only update, FLIP#684)
        is applied to the keys present in the diff; keys absent from the diff keep their global value.
        No error is logged."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id)
        controller.aggregator = MagicMock(spec=Aggregator)
        controller.aggregator.accept.return_value = True
        controller.fire_event = MagicMock()
        controller.log_info = MagicMock()
        controller.log_error = MagicMock()
        controller._current_round = 3
        controller._global_weights = {"weights": {"w1": 1.0, "w2": 2.0}}

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        # Diff carries only "w1" (the trainable var); the frozen "w2" is absent.
        result = DXO(data_kind=DataKind.WEIGHT_DIFF, data={"w1": 0.1}).to_shareable()
        result.add_cookie(AppConstants.CONTRIBUTION_ROUND, 3)

        accepted = controller._accept_train_result(client_name="site-1", result=result, fl_ctx=fl_ctx)

        assert accepted is True
        # No merge error — the missing key is tolerated, not a KeyError.
        assert not any(
            "Error while adding client WEIGHT_DIFF to global weights at server" in call.args[1]
            for call in controller.log_error.call_args_list
        )
        # w1 updated (1.0 + 0.1); w2 preserved from global (2.0).
        reconstructed = from_shareable(result).data
        assert reconstructed["w1"] == pytest.approx(1.1)
        assert reconstructed["w2"] == 2.0
