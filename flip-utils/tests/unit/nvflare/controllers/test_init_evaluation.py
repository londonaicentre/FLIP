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

from flip.constants.flip_constants import FlipEvents
from flip.nvflare.controllers.init_evaluation import InitEvaluation


class TestInitEvaluation:
    @patch("flip.nvflare.controllers.init_evaluation.Task")
    @patch("flip.nvflare.controllers.init_evaluation.json.load")
    @patch("builtins.open", create=True)
    @patch("flip.nvflare.controllers.init_evaluation.os.path.isfile")
    def test_control_flow_success(self, mock_isfile, mock_open, mock_json_load, mock_task):
        """Test successful control_flow"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitEvaluation(model_id=model_id)
        controller.log_info = MagicMock()
        controller.fire_event = MagicMock()
        controller.broadcast_and_wait = MagicMock()

        # Mock config.json existence and content
        mock_isfile.return_value = True
        mock_json_load.return_value = {"models": {"model1": {"checkpoint": "ckpt1", "path": "/path/to/model"}}}
        mock_open.return_value.__enter__ = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, fl_ctx)

        controller.fire_event.assert_called_with(FlipEvents.TASK_INITIATED, fl_ctx)
        controller.broadcast_and_wait.assert_called_once()
        # The cleanup must reach every client, not just the first to reply.
        assert controller.broadcast_and_wait.call_args.kwargs["min_responses"] == 0

    @patch("flip.nvflare.controllers.init_evaluation.Task")
    @patch("flip.nvflare.controllers.init_evaluation.json.load")
    @patch("builtins.open", create=True)
    @patch("flip.nvflare.controllers.init_evaluation.os.path.isfile")
    def test_control_flow_with_abort_signal(self, mock_isfile, mock_open, mock_json_load, mock_task):
        """Test control_flow with abort signal"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitEvaluation(model_id=model_id)
        controller.log_info = MagicMock()
        controller.fire_event = MagicMock()
        controller.broadcast_and_wait = MagicMock()

        # Mock config.json existence and content
        mock_isfile.return_value = True
        mock_json_load.return_value = {"models": {"model1": {"checkpoint": "ckpt1", "path": "/path/to/model"}}}
        mock_open.return_value.__enter__ = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        abort_signal = MagicMock()
        # Trigger abort after broadcast
        abort_signal.triggered = True

        controller.control_flow(abort_signal, fl_ctx)

        # Should fire ABORTED event
        event_calls = [call[0][0] for call in controller.fire_event.call_args_list]
        assert FlipEvents.ABORTED in event_calls
