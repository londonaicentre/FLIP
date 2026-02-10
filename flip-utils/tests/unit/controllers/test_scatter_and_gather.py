# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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


from unittest.mock import MagicMock

import pytest
from nvflare.app_common.abstract.aggregator import Aggregator
from nvflare.app_common.abstract.shareable_generator import ShareableGenerator
from nvflare.app_common.app_constant import AppConstants

from flip.controllers.scatter_and_gather import ScatterAndGather


class TestScatterAndGather:
    def test_init_with_valid_uuid(self):
        """Test initialization with valid UUID"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ScatterAndGather(model_id=model_id)
        assert controller.model_id == model_id

    def test_init_with_invalid_uuid_raises_error(self):
        """Test initialization with invalid UUID raises Exception"""
        with pytest.raises(Exception, match="not a valid UUID"):
            ScatterAndGather(model_id="invalid-uuid")

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
