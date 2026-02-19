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

import sys
import tempfile
from unittest.mock import MagicMock, patch

from nvflare.apis.fl_constant import ReturnCode

from flip.constants import PTConstants

# Mock the evaluator module before importing
sys.modules["evaluator"] = MagicMock()

from flip.nvflare.executors.evaluator import RUN_EVALUATOR, MetricsValidator  # noqa: E402


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

    @patch("evaluator.FLIP_EVALUATOR")
    @patch("flip.nvflare.executors.evaluator.from_shareable")
    @patch("builtins.open", create=True)
    @patch("os.listdir")
    @patch("os.remove")
    def test_execute_with_config_file(
        self, mock_remove, mock_listdir, mock_open, mock_from_shareable, mock_uploaded_evaluator
    ):
        """Test execute method with config.json file"""
        evaluator = RUN_EVALUATOR()
        evaluator.log_info = MagicMock()
        evaluator.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        shareable = MagicMock()
        abort_signal = MagicMock()

        # Setup config file content
        config_content = {"evaluation_output": {"accuracy": 0.0}, "models": {"model1": {}, "model2": {}}}

        mock_listdir.return_value = ["config.json"]
        mock_file = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_file

        mock_evaluator_instance = MagicMock()
        mock_output = MagicMock()
        mock_evaluator_instance.execute.return_value = mock_output
        mock_uploaded_evaluator.return_value = mock_evaluator_instance

        mock_dxo = MagicMock()
        mock_dxo.data = {"model1": {"accuracy": 0.95}, "model2": {"accuracy": 0.93}}
        mock_from_shareable.return_value = mock_dxo

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("flip.nvflare.executors.evaluator.Path") as mock_path:
                with patch("json.load", return_value=config_content):
                    mock_path_instance = MagicMock()
                    mock_path_instance.parent.resolve.return_value = tmpdir
                    mock_path.return_value = mock_path_instance

                    result = evaluator.execute("eval", shareable, fl_ctx, abort_signal)

        assert result == mock_output

    @patch("evaluator.FLIP_EVALUATOR")
    @patch("flip.nvflare.executors.evaluator.from_shareable")
    @patch("os.listdir")
    @patch("os.remove")
    def test_execute_removes_pytorch_files(
        self, mock_remove, mock_listdir, mock_from_shareable, mock_uploaded_evaluator
    ):
        """Test execute method removes .pt and .pth files"""
        evaluator = RUN_EVALUATOR()
        evaluator.log_info = MagicMock()
        evaluator.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        shareable = MagicMock()
        abort_signal = MagicMock()

        # os.listdir is called twice - once for config check, once for weight files
        mock_listdir.return_value = ["model.pt", "weights.pth", "config.json", "data.txt"]

        mock_evaluator_instance = MagicMock()
        mock_output = MagicMock()
        mock_evaluator_instance.execute.return_value = mock_output
        mock_uploaded_evaluator.return_value = mock_evaluator_instance

        mock_dxo = MagicMock()
        mock_dxo.data = {"model1": {"accuracy": 0.95}}
        mock_from_shareable.return_value = mock_dxo

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("flip.nvflare.executors.evaluator.Path") as mock_path:
                with patch("builtins.open", create=True):
                    with patch("json.load", return_value={"evaluation_output": {}, "models": {}}):
                        mock_path_instance = MagicMock()
                        mock_path_instance.parent.resolve.return_value = tmpdir
                        mock_path.return_value = mock_path_instance

                        evaluator.execute("eval", shareable, fl_ctx, abort_signal)

        # Should have removed 2 pytorch files (model.pt and weights.pth)
        assert mock_remove.call_count == 2

    @patch("evaluator.FLIP_EVALUATOR")
    @patch("flip.nvflare.executors.evaluator.from_shareable")
    @patch("builtins.open", create=True)
    @patch("os.listdir")
    @patch("os.remove")
    def test_execute_validation_failure(
        self, mock_remove, mock_listdir, mock_open, mock_from_shareable, mock_uploaded_evaluator
    ):
        """Test execute method with validation failure"""
        evaluator = RUN_EVALUATOR()
        evaluator.log_info = MagicMock()
        evaluator.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        shareable = MagicMock()
        abort_signal = MagicMock()

        config_content = {"evaluation_output": {"accuracy": 0.0}, "models": {"model1": {}}}

        mock_listdir.return_value = ["config.json"]

        mock_evaluator_instance = MagicMock()
        mock_output = MagicMock()
        mock_evaluator_instance.execute.return_value = mock_output
        mock_uploaded_evaluator.return_value = mock_evaluator_instance

        # Return data for wrong model
        mock_dxo = MagicMock()
        mock_dxo.data = {"model_wrong": {"accuracy": 0.95}}
        mock_from_shareable.return_value = mock_dxo

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("flip.nvflare.executors.evaluator.Path") as mock_path:
                with patch("json.load", return_value=config_content):
                    mock_path_instance = MagicMock()
                    mock_path_instance.parent.resolve.return_value = tmpdir
                    mock_path.return_value = mock_path_instance

                    result = evaluator.execute("eval", shareable, fl_ctx, abort_signal)

        # Should still return output but log error
        assert result == mock_output
        evaluator.log_error.assert_called()

    @patch("evaluator.FLIP_EVALUATOR")
    def test_execute_exception_handling(self, mock_uploaded_evaluator):
        """Test execute method exception handling"""
        evaluator = RUN_EVALUATOR()
        evaluator.log_info = MagicMock()
        evaluator.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        shareable = MagicMock()
        abort_signal = MagicMock()

        # Force an exception
        mock_uploaded_evaluator.side_effect = Exception("Test exception")

        result = evaluator.execute("eval", shareable, fl_ctx, abort_signal)

        assert result.get_return_code() == ReturnCode.EXECUTION_EXCEPTION
        assert result.get_header("exception") is not None
        evaluator.log_error.assert_called()
        evaluator.log_error.assert_called()
