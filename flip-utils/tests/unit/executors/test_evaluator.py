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

import sys
from unittest.mock import MagicMock

from flip.constants import PTConstants

# Mock the evaluator module before importing
sys.modules["evaluator"] = MagicMock()

from flip.executors.evaluator import RUN_EVALUATOR, MetricsValidator


class TestMetricsValidator:
    def test_init(self):
        """Test MetricsValidator initialization"""
        input_eval = {"accuracy": 0.0}
        input_models = ["model1", "model2"]

        validator = MetricsValidator(input_eval, input_models)
        assert validator.input_evaluation == input_eval
        assert validator.input_models == input_models

    def test_validate_success_simple(self):
        """Test successful validation with simple metrics"""
        input_eval = {"accuracy": 0.0}
        test_eval = {"model1": {"accuracy": 0.95}}

        validator = MetricsValidator(input_eval, ["model1"])
        success, message = validator.validate(test_eval)

        assert success is True

    def test_validate_model_not_in_list(self):
        """Test validation failure when model not in input list"""
        input_eval = {"accuracy": 0.0}
        test_eval = {"model3": {"accuracy": 0.95}}

        validator = MetricsValidator(input_eval, ["model1", "model2"])
        success, message = validator.validate(test_eval)

        assert success is False
        assert "model3" in message

    def test_validate_element_with_float(self):
        """Test validate_element with float values"""
        input_eval = {"accuracy": 0.0, "loss": 0.0}
        test_element = {"accuracy": 0.95, "loss": 0.05}

        validator = MetricsValidator(input_eval, ["model1"])
        success, message = validator.validate_element(test_element, input_eval)

        assert success is True

    def test_validate_element_with_list_of_floats(self):
        """Test validate_element with list of floats"""
        input_eval = {"per_class_accuracy": [0.0]}
        test_element = {"per_class_accuracy": [0.9, 0.92, 0.88]}

        validator = MetricsValidator(input_eval, ["model1"])
        success, message = validator.validate_element(test_element, input_eval)

        assert success is True


class TestRunEvaluator:
    def test_init_default_values(self):
        """Test initialization with default values"""
        evaluator = RUN_EVALUATOR()
        assert evaluator._evaluate_task_name == PTConstants.EvalTaskName
        assert evaluator._project_id == ""
        assert evaluator._query == ""
        assert evaluator._evaluator is None

    def test_init_custom_values(self):
        """Test initialization with custom values"""
        evaluator = RUN_EVALUATOR(
            evaluate_task_name="custom_eval", project_id="proj_111", query="SELECT * FROM eval_data"
        )
        assert evaluator._evaluate_task_name == "custom_eval"
        assert evaluator._project_id == "proj_111"
        assert evaluator._query == "SELECT * FROM eval_data"
