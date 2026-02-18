from unittest.mock import MagicMock

import pytest
from nvflare.app_common.abstract.aggregator import Aggregator
from nvflare.app_common.abstract.shareable_generator import ShareableGenerator
from nvflare.app_common.app_constant import AppConstants

from flip.nvflare.controllers.scatter_and_gather_ldm import ScatterAndGatherLDM


class TestScatterAndGatherLDM:
    def test_init_with_valid_uuid(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id)
        assert controller.model_id == model_id

    def test_init_with_invalid_uuid_raises_error(self):
        with pytest.raises(Exception, match="not a valid UUID"):
            ScatterAndGatherLDM(model_id="invalid-uuid")

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

    def test_init_non_bool_ignore_result_error_raises_error(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(Exception, match="ignore_result_error must be a bool"):
            ScatterAndGatherLDM(model_id=model_id, ignore_result_error="yes")

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

    def test_start_controller_invalid_aggregator(self):
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGatherLDM(model_id=model_id)
        controller.system_panic = MagicMock()
        controller.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine
        engine.get_component.return_value = "not_an_aggregator"

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

        from nvflare.app_common.abstract.learnable_persistor import LearnablePersistor

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
        with pytest.raises(Exception, match="persist_every_n_rounds must be greater"):
            ScatterAndGatherLDM(model_id=model_id, persist_every_n_rounds=-1)
