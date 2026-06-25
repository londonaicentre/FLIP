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

import json
import sys
from unittest.mock import MagicMock, patch

from nvflare.apis.fl_constant import ReturnCode

from flip.constants import PTConstants

# Mock the evaluator module before importing
sys.modules["evaluator"] = MagicMock()

from flip.nvflare.executors.evaluator import RUN_EVALUATOR  # noqa: E402


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

    def test_execute_returns_output_without_evaluation_output_in_config(self, tmp_path, monkeypatch):
        """The evaluator's output is returned even when config.json declares no evaluation_output schema."""
        # config.json is present (the evaluation pipeline needs it for model loading) but carries no
        # evaluation_output block — the executor must not require one to return metrics.
        (tmp_path / "config.json").write_text(
            json.dumps({"models": {"model1": {"checkpoint": "model.pt", "path": "unet"}}})
        )
        monkeypatch.chdir(tmp_path)

        evaluator = RUN_EVALUATOR()
        evaluator.log_info = MagicMock()
        evaluator.log_error = MagicMock()

        mock_output = MagicMock()
        mock_evaluator_instance = MagicMock()
        mock_evaluator_instance.execute.return_value = mock_output
        evaluator._evaluator = mock_evaluator_instance

        result = evaluator.execute("eval", MagicMock(), MagicMock(), MagicMock())

        assert result == mock_output

    def test_execute_removes_pytorch_files(self, tmp_path, monkeypatch):
        """.pt and .pth weight files are stripped from the client working dir before the evaluator runs."""
        (tmp_path / "model.pt").write_bytes(b"")
        (tmp_path / "weights.pth").write_bytes(b"")
        (tmp_path / "data.txt").write_text("keep me")
        monkeypatch.chdir(tmp_path)

        evaluator = RUN_EVALUATOR()
        evaluator.log_info = MagicMock()
        evaluator.log_error = MagicMock()

        mock_output = MagicMock()
        mock_evaluator_instance = MagicMock()
        mock_evaluator_instance.execute.return_value = mock_output
        evaluator._evaluator = mock_evaluator_instance

        result = evaluator.execute("eval", MagicMock(), MagicMock(), MagicMock())

        assert result == mock_output
        assert not (tmp_path / "model.pt").exists()
        assert not (tmp_path / "weights.pth").exists()
        assert (tmp_path / "data.txt").exists()

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
