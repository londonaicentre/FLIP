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

import pytest

from flip.controllers.fed_evaluation import ModelEval


class TestModelEval:
    def test_init_with_valid_uuid(self):
        """Test initialization with valid UUID"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ModelEval(model_id=model_id)
        assert controller._model_id == model_id

    def test_init_with_invalid_uuid_raises_error(self):
        """Test initialization with invalid UUID raises ValueError"""
        with pytest.raises(ValueError, match="not a valid UUID"):
            ModelEval(model_id="invalid-uuid")

    def test_init_with_custom_task_check_period(self):
        """Test initialization with custom task_check_period"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ModelEval(model_id=model_id, task_check_period=1.0)
        assert controller._task_check_period == 1.0

    def test_init_with_custom_submit_model_timeout(self):
        """Test initialization with custom submit_model_timeout"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ModelEval(model_id=model_id, submit_model_timeout=1200)
        assert controller._submit_model_timeout == 1200

    def test_init_with_negative_submit_model_timeout_raises_error(self):
        """Test initialization with negative submit_model_timeout raises ValueError"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(ValueError, match="submit_model_timeout must be greater"):
            ModelEval(model_id=model_id, submit_model_timeout=-1)

    def test_init_with_custom_validation_timeout(self):
        """Test initialization with custom validation_timeout"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ModelEval(model_id=model_id, validation_timeout=7200)
        assert controller._validation_timeout == 7200

    def test_init_with_negative_validation_timeout_raises_error(self):
        """Test initialization with negative validation_timeout raises ValueError"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(ValueError, match="model_validate_timeout must be greater"):
            ModelEval(model_id=model_id, validation_timeout=-1)

    def test_init_with_custom_wait_for_clients_timeout(self):
        """Test initialization with custom wait_for_clients_timeout"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ModelEval(model_id=model_id, wait_for_clients_timeout=600)
        assert controller._wait_for_clients_timeout == 600

    def test_init_with_negative_wait_for_clients_timeout_raises_error(self):
        """Test initialization with negative wait_for_clients_timeout raises ValueError"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(ValueError, match="wait_for_clients_timeout must be greater"):
            ModelEval(model_id=model_id, wait_for_clients_timeout=-1)

    def test_init_with_custom_cleanup_timeout(self):
        """Test initialization with custom cleanup_timeout"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ModelEval(model_id=model_id, cleanup_timeout=300)
        assert controller._cleanup_timeout == 300

    def test_init_with_negative_cleanup_timeout_raises_error(self):
        """Test initialization with negative cleanup_timeout raises ValueError"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        with pytest.raises(ValueError, match="cleanup_timeout must be greater"):
            ModelEval(model_id=model_id, cleanup_timeout=-1)

    def test_init_with_custom_fatal_error_delay(self):
        """Test initialization with custom fatal_error_delay"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ModelEval(model_id=model_id, fatal_error_delay=10)
        assert controller._fatal_error_delay == 10

    def test_init_with_negative_fatal_error_delay(self):
        """Test initialization with negative fatal_error_delay is allowed (no validation)"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ModelEval(model_id=model_id, fatal_error_delay=-1)
        assert controller._fatal_error_delay == -1

    def test_init_with_cleanup_models_true(self):
        """Test initialization with cleanup_models set to True"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ModelEval(model_id=model_id, cleanup_models=True)
        assert controller._cleanup_models is True

    def test_init_with_cleanup_models_false(self):
        """Test initialization with cleanup_models set to False"""
        model_id = "123e4567-e89b-12d3a456-426614174000"
        controller = ModelEval(model_id=model_id, cleanup_models=False)
        assert controller._cleanup_models is False

    def test_init_with_custom_task_names(self):
        """Test initialization with custom task names"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ModelEval(
            model_id=model_id, submit_model_task_name="custom_submit", evaluation_task_name="custom_eval"
        )
        assert controller._submit_model_task_name == "custom_submit"
        assert controller._evaluation_task_name == "custom_eval"

    def test_init_with_custom_component_ids(self):
        """Test initialization with custom component IDs"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ModelEval(model_id=model_id, model_locator_id="my_locator", formatter_id="my_formatter")
        assert controller._model_locator_id == "my_locator"
        assert controller._formatter_id == "my_formatter"

    def test_init_with_participating_clients_list(self):
        """Test initialization with list of participating clients"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        clients = ["client1", "client2", "client3"]
        controller = ModelEval(model_id=model_id, participating_clients=clients)
        assert controller._participating_clients == clients

    def test_init_with_none_participating_clients(self):
        """Test initialization with None participating_clients (default)"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = ModelEval(model_id=model_id, participating_clients=None)
        assert controller._participating_clients is None
