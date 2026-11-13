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
from flip.nvflare.controllers.init_training import InitTraining


class TestInitTraining:
    @patch("flip.nvflare.controllers.init_training.Task")
    def test_control_flow_success(self, mock_task):
        """Test successful control_flow"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id)
        controller.log_info = MagicMock()
        controller.fire_event = MagicMock()
        controller.broadcast_and_wait = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, fl_ctx)

        controller.fire_event.assert_called_with(FlipEvents.TRAINING_INITIATED, fl_ctx)
        controller.broadcast_and_wait.assert_called_once()
        # The cleanup must reach every client, not just the first to reply.
        assert controller.broadcast_and_wait.call_args.kwargs["min_responses"] == 0

    @patch("flip.nvflare.controllers.init_training.Task")
    def test_control_flow_with_abort_signal(self, mock_task):
        """Test control_flow with abort signal"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id)
        controller.log_info = MagicMock()
        controller.fire_event = MagicMock()
        controller.broadcast_and_wait = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        abort_signal = MagicMock()
        # Trigger abort after broadcast
        abort_signal.triggered = True

        controller.control_flow(abort_signal, fl_ctx)

        # Should fire ABORTED event
        assert any(call[0][0] == FlipEvents.ABORTED for call in controller.fire_event.call_args_list)
