# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from unittest.mock import MagicMock, patch

from flip.constants import FlipEvents
from flip.nvflare.controllers.init_evaluation import InitEvaluation


def _fl_ctx() -> MagicMock:
    fl_ctx = MagicMock()
    fl_ctx.get_peer_context.return_value = None
    return fl_ctx


class TestInitEvaluation:
    def test_init(self):
        assert InitEvaluation() is not None

    def test_start_controller_no_engine(self):
        """Test start_controller when engine is not found"""
        controller = InitEvaluation()
        controller.system_panic = MagicMock()

        fl_ctx = _fl_ctx()
        fl_ctx.get_engine.return_value = None

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()
        assert "Engine not found" in str(controller.system_panic.call_args)

    def test_start_controller_success(self):
        """Test successful start_controller"""
        controller = InitEvaluation()
        controller.log_info = MagicMock()

        fl_ctx = _fl_ctx()
        fl_ctx.get_engine.return_value = MagicMock()

        controller.start_controller(fl_ctx)

        controller.log_info.assert_called()

    @patch("flip.nvflare.controllers.init_evaluation.json.load")
    @patch("builtins.open", create=True)
    @patch("flip.nvflare.controllers.init_evaluation.os.path.isfile")
    def test_control_flow_fires_status_and_validates_config(self, mock_isfile, mock_open, mock_json_load):
        """control_flow fires the hub status event and checks config.json — no cleanup
        broadcast any more (imaging retention moved trust-side, FLIP#1050)."""
        controller = InitEvaluation()
        controller.log_info = MagicMock()
        controller.log_error = MagicMock()
        controller.fire_event = MagicMock()

        mock_isfile.return_value = True
        mock_json_load.return_value = {"models": {"model1": {"checkpoint": "ckpt1", "path": "/path/to/model"}}}
        mock_open.return_value.__enter__ = MagicMock()

        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, _fl_ctx())

        event_calls = [call[0][0] for call in controller.fire_event.call_args_list]
        assert event_calls == [FlipEvents.TASK_INITIATED]
        controller.log_error.assert_not_called()
        assert not hasattr(controller, "_cleanup_timeout")

    @patch("flip.nvflare.controllers.init_evaluation.json.load")
    @patch("builtins.open", create=True)
    @patch("flip.nvflare.controllers.init_evaluation.os.path.isfile")
    def test_control_flow_flags_invalid_models_config(self, mock_isfile, mock_open, mock_json_load):
        """A model entry missing checkpoint/path is logged and ends the run."""
        controller = InitEvaluation()
        controller.log_info = MagicMock()
        controller.log_error = MagicMock()
        controller.fire_event = MagicMock()

        mock_isfile.return_value = True
        mock_json_load.return_value = {"models": {"model1": {"path": "/path/only"}}}
        mock_open.return_value.__enter__ = MagicMock()

        controller.control_flow(MagicMock(triggered=False), _fl_ctx())

        controller.log_error.assert_called()
        assert "'checkpoint' and 'path'" in str(controller.log_error.call_args)

    def test_stop_controller(self):
        """Test stop_controller"""
        controller = InitEvaluation()
        controller.log_info = MagicMock()
        controller.cancel_all_tasks = MagicMock()

        controller.stop_controller(_fl_ctx())

        controller.cancel_all_tasks.assert_called_once()

    def test_process_result_of_unknown_task(self):
        """Test process_result_of_unknown_task"""
        controller = InitEvaluation()
        controller.log_error = MagicMock()

        controller.process_result_of_unknown_task(MagicMock(), "unknown_task", "task_id", MagicMock(), _fl_ctx())

        controller.log_error.assert_called()
