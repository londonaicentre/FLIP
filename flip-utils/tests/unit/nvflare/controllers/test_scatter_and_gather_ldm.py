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

# ScatterAndGatherLDM subclasses FLIP's ScatterAndGather and delegates the round loop to stock
# NVFLARE; these tests cover only the LDM-specific overrides (per-phase round counts, the config
# override, the phase-1 -> phase-2 weight handoff, and the delegation itself). Stock's own round
# loop behaviour is exercised by NVFLARE's tests, and the FLIP hooks by test_scatter_and_gather.py.

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from nvflare.apis.fl_constant import FLContextKey, ReturnCode
from nvflare.apis.shareable import Shareable
from nvflare.app_common.abstract.aggregator import Aggregator
from nvflare.app_common.abstract.learnable_persistor import LearnablePersistor
from nvflare.app_common.abstract.model import make_model_learnable
from nvflare.app_common.abstract.model_locator import ModelLocator
from nvflare.app_common.abstract.shareable_generator import ShareableGenerator
from nvflare.app_common.app_constant import AppConstants

from flip.constants import FlipEvents, PTConstants
from flip.nvflare.controllers.scatter_and_gather import ScatterAndGather
from flip.nvflare.controllers.scatter_and_gather_ldm import ScatterAndGatherLDM

_VALID_MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"


class TestScatterAndGatherLDM:
    @pytest.mark.parametrize(
        ("bad_kwargs", "frag"),
        [
            ({"aggregator_id": 123}, "aggregator_id"),
            ({"model_locator_id": 123}, "model_locator_id"),
            ({"persistor_id": 123}, "persistor_id"),
            ({"shareable_generator_id": 123}, "shareable_generator_id"),
            ({"train_task_name": 123}, "train_task_name"),
        ],
    )
    def test_init_rejects_wrong_arg_types(self, bad_kwargs, frag):
        """Each id/flag arg is type-checked at construction and raises TypeError on the wrong type."""
        with pytest.raises(TypeError, match=frag):
            ScatterAndGatherLDM(model_id=_VALID_MODEL_ID, **bad_kwargs)

    def test_handle_event_send_result_forwards_resolved_model_id(self):
        """On SEND_RESULT the controller forwards the metric with the lazily-resolved model_id."""
        controller = ScatterAndGatherLDM(model_id=_VALID_MODEL_ID)
        controller.log_error = MagicMock()
        controller._current_round = 3

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
        assert args[1] == 3
        assert args[2] == _VALID_MODEL_ID

    def test_handle_event_send_result_no_data_logs_error(self):
        """SEND_RESULT with no EVENT_DATA logs an error and does not forward."""
        controller = ScatterAndGatherLDM(model_id=_VALID_MODEL_ID)
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
        controller = ScatterAndGatherLDM(model_id=_VALID_MODEL_ID, ignore_result_error=False)
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

    def test_init_with_valid_uuid(self):
        """Test initialization with valid UUID stores it as fallback."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id)
        assert controller._model_id_fallback == model_id
        assert controller._model_id is None

    def test_resolve_model_id_uses_fallback_when_fl_ctx_has_no_custom_props(self):
        """Lazy resolution returns the constructor UUID when fl_ctx has no custom_props."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id)
        fl_ctx = MagicMock()
        fl_ctx.get_prop.return_value = None
        result = controller._resolve_model_id(fl_ctx)
        assert result == model_id

    def test_init_with_custom_ae_rounds(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id, num_rounds_ae=10)
        assert controller._num_rounds_ae == 10

    def test_init_with_custom_dm_rounds(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id, num_rounds_dm=15)
        assert controller._num_rounds_dm == 15

    def test_init_negative_ae_rounds_raises_error(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(Exception, match="num_rounds_ae must be greater"):
            ScatterAndGatherLDM(model_id=model_id, num_rounds_ae=-1)

    def test_init_negative_dm_rounds_raises_error(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(Exception, match="num_rounds_dm must be greater"):
            ScatterAndGatherLDM(model_id=model_id, num_rounds_dm=-1)

    def test_init_negative_min_clients_raises_error(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(Exception, match="min_clients must be greater"):
            ScatterAndGatherLDM(model_id=model_id, min_clients=-1)

    def test_init_with_custom_component_ids(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(
            model_id=model_id,
            aggregator_id="my_agg",
            persistor_id="my_persist",
            shareable_generator_id="my_gen",
            model_locator_id="my_locator",
        )
        assert controller.aggregator_id == "my_agg"
        assert controller.persistor_id == "my_persist"
        assert controller.shareable_generator_id == "my_gen"
        assert controller.model_locator_id == "my_locator"

    def test_init_with_custom_train_task_name(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id, train_task_name="train_ae")
        assert controller.train_task_name == "train_ae"

    def test_init_default_phase_is_init(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id)
        assert controller._phase == AppConstants.PHASE_INIT

    def test_init_non_string_aggregator_id_raises_error(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(Exception, match="aggregator_id must be a string"):
            ScatterAndGatherLDM(model_id=model_id, aggregator_id=123)

    def test_start_controller_no_engine(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id)
        controller.system_panic = MagicMock()
        controller.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value = None

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()
        assert "Engine not found" in str(controller.system_panic.call_args)

    def test_start_controller_invalid_aggregator(self, tmp_path):
        """A non-Aggregator component panics — via stock's validation, which start_controller
        delegates to after resolving the LDM-specific model locator."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id, model_locator_id="locator")
        controller.system_panic = MagicMock()
        controller.log_info = MagicMock()

        def side_effect(component_id):
            if component_id == "locator":
                return MagicMock(spec=ModelLocator)
            return "not_an_aggregator"

        engine = MagicMock()
        engine.get_component.side_effect = side_effect
        engine.get_workspace.return_value.get_app_dir.return_value = str(tmp_path)
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value = engine
        # Stock's start_controller resolves components via self._engine (set by
        # Controller.initialize() in production).
        controller._engine = engine

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()
        assert "Aggregator" in str(controller.system_panic.call_args)

    def test_start_controller_invalid_model_locator(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id, model_locator_id="locator")
        controller.system_panic = MagicMock()
        controller.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        mock_aggregator = MagicMock(spec=Aggregator)
        mock_shareable_gen = MagicMock(spec=ShareableGenerator)
        mock_persistor = MagicMock(spec=LearnablePersistor)

        def side_effect(component_id):
            if component_id == controller.aggregator_id:
                return mock_aggregator
            elif component_id == controller.shareable_generator_id:
                return mock_shareable_gen
            elif component_id == controller.persistor_id:
                return mock_persistor
            elif component_id == "locator":
                return "not_a_locator"
            return MagicMock()

        engine.get_component.side_effect = side_effect

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()
        assert "ModelLocator" in str(controller.system_panic.call_args)

    @staticmethod
    def _round_ready_controller(**kwargs):
        """A controller wired to run stock's real round loop with every component mocked."""
        controller = ScatterAndGatherLDM(model_id=_VALID_MODEL_ID, **kwargs)
        controller.log_info = MagicMock()
        controller.fire_event = MagicMock()
        controller.system_panic = MagicMock()
        controller.broadcast_and_wait = MagicMock()
        controller._maybe_cleanup_memory = MagicMock()
        controller.aggregator = MagicMock(spec=Aggregator)
        controller.persistor = MagicMock(spec=LearnablePersistor)
        controller.shareable_gen = MagicMock(spec=ShareableGenerator)
        controller.shareable_gen.learnable_to_shareable.return_value = Shareable()
        controller._global_weights = {"weights": {"w1": 1.0}}
        # Stock's loop reads self._engine (set by Controller.initialize() in production).
        controller._engine = MagicMock()
        controller._engine.get_clients.return_value = ["site-1"]
        return controller

    def test_control_flow_runs_round_and_cleans_memory(self):
        """One AE round end-to-end through stock's real (inherited) loop: broadcasts the train
        task, aggregates, persists, and calls the per-round _maybe_cleanup_memory so server RSS
        does not climb across rounds on large-model jobs."""
        controller = self._round_ready_controller(num_rounds_ae=1, num_rounds_dm=0, train_task_name="train_ae")

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, fl_ctx)

        controller.system_panic.assert_not_called()
        controller.broadcast_and_wait.assert_called_once()
        assert controller.broadcast_and_wait.call_args.kwargs["task"].name == "train_ae"
        controller.persistor.save.assert_called_once()
        controller._maybe_cleanup_memory.assert_called_once()
        assert controller._phase == AppConstants.PHASE_FINISHED

    def test_control_flow_dm_round_does_handoff_and_resets_aggregator(self):
        """One DM round through stock's real loop: loads the phase-1 autoencoder weights via the
        persistor before the loop, broadcasts under the train_dm task name, and resets the (shared,
        two-phase) aggregator at round end — stock behaviour the old vendored loop had dropped."""
        controller = self._round_ready_controller(num_rounds_ae=0, num_rounds_dm=1, train_task_name="train_dm")
        controller.model_locator = MagicMock(spec=ModelLocator)
        controller.model_locator.get_model_names.return_value = [PTConstants.PTServerName]
        # The DM handoff drives PTFileModelPersistor API (load_model / source_ckpt_file_full_name)
        # beyond the LearnablePersistor spec, so use a plain mock.
        controller.persistor = MagicMock()
        loaded_weights = make_model_learnable({"w1": 2.0}, {})
        controller.persistor.load_model.return_value = loaded_weights

        engine = controller._engine
        engine.get_workspace.return_value.get_app_dir.return_value = "/srv/app"
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value = engine
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, fl_ctx)

        controller.system_panic.assert_not_called()
        assert controller.persistor.source_ckpt_file_full_name == os.path.join("/srv/app", PTConstants.PTFileModelName)
        controller.persistor.load_model.assert_called_once()
        controller.broadcast_and_wait.assert_called_once()
        assert controller.broadcast_and_wait.call_args.kwargs["task"].name == "train_dm"
        controller.aggregator.reset.assert_called_once()
        assert controller._phase == AppConstants.PHASE_FINISHED

    @pytest.mark.parametrize(
        ("task_name", "expected_rounds"),
        [("train_ae", 3), ("train_dm", 2)],
    )
    def test_control_flow_sets_phase_rounds_and_delegates(self, task_name, expected_rounds):
        """control_flow narrows self._num_rounds to this phase's count, then delegates the loop to
        the base (stock) control_flow."""
        controller = ScatterAndGatherLDM(
            model_id=_VALID_MODEL_ID, num_rounds_ae=3, num_rounds_dm=2, train_task_name=task_name
        )
        controller.log_info = MagicMock()
        controller.model_locator = MagicMock(spec=ModelLocator)
        controller.model_locator.get_model_names.return_value = [PTConstants.PTServerName]
        controller.persistor = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        abort_signal = MagicMock()

        with patch.object(ScatterAndGather, "control_flow") as stock_control_flow:
            controller.control_flow(abort_signal, fl_ctx)

        assert controller._num_rounds == expected_rounds
        stock_control_flow.assert_called_once_with(abort_signal, fl_ctx)

    def test_control_flow_unknown_task_name_panics(self):
        """A train_task_name that is neither phase (incl. stock's default 'train') panics without
        entering the loop — there is no phase round count to run it against."""
        controller = ScatterAndGatherLDM(model_id=_VALID_MODEL_ID)  # default train_task_name="train"
        controller.system_panic = MagicMock()

        with patch.object(ScatterAndGather, "control_flow") as stock_control_flow:
            controller.control_flow(MagicMock(), MagicMock())

        controller.system_panic.assert_called_once()
        assert "train_task_name" in str(controller.system_panic.call_args)
        stock_control_flow.assert_not_called()

    def test_control_flow_dm_handoff_failure_panics(self):
        """A DM handoff that cannot resolve the phase-1 server model panics without delegating."""
        controller = ScatterAndGatherLDM(model_id=_VALID_MODEL_ID, train_task_name="train_dm")
        controller.log_info = MagicMock()
        controller.system_panic = MagicMock()
        controller.model_locator = MagicMock(spec=ModelLocator)
        controller.model_locator.get_model_names.return_value = ["not-the-server-model"]

        with patch.object(ScatterAndGather, "control_flow") as stock_control_flow:
            controller.control_flow(MagicMock(), MagicMock())

        controller.system_panic.assert_called_once()
        assert "phase-1" in str(controller.system_panic.call_args)
        stock_control_flow.assert_not_called()

    def test_control_flow_dm_resume_skips_handoff(self):
        """On a snapshot-restore resume (_current_round already set) the DM handoff is skipped so
        the restored global weights are not overwritten with the phase-1 checkpoint."""
        controller = ScatterAndGatherLDM(model_id=_VALID_MODEL_ID, num_rounds_dm=2, train_task_name="train_dm")
        controller.model_locator = MagicMock(spec=ModelLocator)
        controller.persistor = MagicMock()
        controller._current_round = 1

        with patch.object(ScatterAndGather, "control_flow") as stock_control_flow:
            controller.control_flow(MagicMock(), MagicMock())

        controller.persistor.load_model.assert_not_called()
        controller.model_locator.get_model_names.assert_not_called()
        stock_control_flow.assert_called_once()

    def test_start_controller_config_override_resyncs_num_rounds(self, tmp_path):
        """GLOBAL_ROUNDS_AE/DM in the app's custom/config.json override the constructor counts, and
        the inherited two-phase total is re-synced before delegating to stock (which loads the
        initial model and fires INITIAL_MODEL_LOADED)."""
        (tmp_path / "custom").mkdir()
        (tmp_path / "custom" / "config.json").write_text(json.dumps({"GLOBAL_ROUNDS_AE": 7, "GLOBAL_ROUNDS_DM": 4}))

        controller = ScatterAndGatherLDM(
            model_id=_VALID_MODEL_ID, num_rounds_ae=1, num_rounds_dm=1, model_locator_id="locator"
        )
        controller.log_info = MagicMock()
        controller.system_panic = MagicMock()
        controller.fire_event = MagicMock()

        mock_persistor = MagicMock(spec=LearnablePersistor)
        loaded_weights = make_model_learnable({"w1": 1.0}, {})
        mock_persistor.load.return_value = loaded_weights

        def side_effect(component_id):
            if component_id == "locator":
                return MagicMock(spec=ModelLocator)
            if component_id == controller.aggregator_id:
                return MagicMock(spec=Aggregator)
            if component_id == controller.shareable_generator_id:
                return MagicMock(spec=ShareableGenerator)
            if component_id == controller.persistor_id:
                return mock_persistor
            return MagicMock()

        engine = MagicMock()
        engine.get_component.side_effect = side_effect
        engine.get_workspace.return_value.get_app_dir.return_value = str(tmp_path)
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value = engine
        controller._engine = engine

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_not_called()
        assert controller._num_rounds_ae == 7
        assert controller._num_rounds_dm == 4
        assert controller._num_rounds == 11
        assert controller._global_weights is loaded_weights

    def test_stop_controller(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id)
        controller.cancel_all_tasks = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        controller.stop_controller(fl_ctx)

        assert controller._phase == AppConstants.PHASE_FINISHED
        controller.cancel_all_tasks.assert_called_once()

    def test_check_abort_signal_triggered(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id)
        controller.fire_event = MagicMock()
        controller.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        abort_signal = MagicMock()
        abort_signal.triggered = True

        result = controller._check_abort_signal(fl_ctx, abort_signal)

        assert result is True
        assert controller._phase == AppConstants.PHASE_FINISHED

    def test_check_abort_signal_not_triggered(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        abort_signal = MagicMock()
        abort_signal.triggered = False

        result = controller._check_abort_signal(fl_ctx, abort_signal)

        assert result is False

    def test_init_with_custom_persist_every_n_rounds(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id, persist_every_n_rounds=3)
        assert controller._persist_every_n_rounds == 3

    def test_init_negative_persist_every_n_rounds_raises_error(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(Exception, match="persist_every_n_rounds must"):
            ScatterAndGatherLDM(model_id=model_id, persist_every_n_rounds=-1)


class TestSubclassContract:
    """The de-fork contract: LDM subclasses FLIP's ScatterAndGather and does not re-fork the
    shared hooks — it must keep inheriting the FLIP overrides (and, through them, stock's loop)."""

    def test_subclasses_flip_scatter_and_gather(self):
        assert issubclass(ScatterAndGatherLDM, ScatterAndGather)

    @pytest.mark.parametrize("hook", ["_accept_train_result", "handle_event", "_check_abort_signal"])
    def test_flip_hooks_are_inherited_not_redeclared(self, hook):
        assert hook not in ScatterAndGatherLDM.__dict__, f"{hook} re-forked on ScatterAndGatherLDM"


class TestFedJobSerialisation:
    """FedJob/recipe serialisation must capture every JSON-config arg of the LDM controller.

    LDM declares its full signature explicitly (no *args/**kwargs), so
    ``get_component_init_parameters`` reads it directly. If this __init__ is ever thinned to a
    ``*args/**kwargs`` passthrough it must declare BOTH so NVFLARE's base-class walk still runs
    (see the equivalent guard on ScatterAndGather)."""

    def test_init_exposes_all_config_args_for_recipe_serialisation(self):
        from nvflare.fuel.utils.class_utils import get_component_init_parameters

        params = set(get_component_init_parameters(ScatterAndGatherLDM(model_id=_VALID_MODEL_ID)))
        for arg in (
            "num_rounds_ae",
            "num_rounds_dm",
            "model_locator_id",
            "persistor_id",
            "aggregator_id",
            "shareable_generator_id",
            "train_task_name",
            "min_clients",
        ):
            assert arg in params, f"'{arg}' dropped by FedJob serialisation"
