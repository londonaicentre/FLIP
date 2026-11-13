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

import pytest
from nvflare.apis.fl_constant import ReturnCode

from flip.constants import FlipEvents
from flip.nvflare.controllers.init_training import InitTraining


class TestInitTraining:
    def test_init_with_valid_uuid(self):
        """Test initialization with valid UUID stores it as fallback"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id)
        assert controller._model_id_fallback == model_id
        assert controller._model_id is None

    def test_resolve_model_id_uses_fallback_when_fl_ctx_has_no_custom_props(self):
        """Lazy resolution returns the constructor UUID when fl_ctx has no custom_props."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id)
        fl_ctx = MagicMock()
        fl_ctx.get_prop.return_value = None
        result = controller._resolve_model_id(fl_ctx)
        assert result == model_id

    def test_init_with_negative_cleanup_timeout_raises_error(self):
        """Test initialization with negative cleanup_timeout raises ValueError"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        mock_flip = MagicMock()
        with pytest.raises(ValueError, match="cleanup_timeout must be greater"):
            InitTraining(model_id=model_id, cleanup_timeout=-1, flip=mock_flip)

    def test_init_with_custom_cleanup_timeout(self):
        """Test initialization with custom cleanup_timeout"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id, cleanup_timeout=300)
        assert controller._cleanup_timeout == 300

    def test_start_controller_no_engine(self):
        """Test start_controller when engine is not found"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id)
        controller.system_panic = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value = None

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()
        assert "Engine not found" in str(controller.system_panic.call_args)

    def test_start_controller_success(self):
        """Test successful start_controller"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id)
        controller.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        controller.start_controller(fl_ctx)

        controller.log_info.assert_called()

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

    def test_stop_controller(self):
        """Test stop_controller"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id)
        controller.log_info = MagicMock()
        controller.cancel_all_tasks = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        controller.stop_controller(fl_ctx)

        controller.cancel_all_tasks.assert_called_once()

    def test_process_result_of_unknown_task(self):
        """Test process_result_of_unknown_task"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id)
        controller.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        client = MagicMock()
        shareable = MagicMock()

        controller.process_result_of_unknown_task(client, "unknown_task", "task_id", shareable, fl_ctx)

        controller.log_error.assert_called()

    def test_accept_cleanup_result_success(self):
        """Test _accept_cleanup_result with successful result"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        shareable = MagicMock()
        shareable.get_return_code.return_value = ReturnCode.OK

        result = controller._accept_cleanup_result("client1", shareable, fl_ctx)

        # Should return None for OK status
        assert result is None

    def test_accept_cleanup_result_with_exception(self):
        """Test _accept_cleanup_result with execution exception"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        mock_flip = MagicMock()
        controller = InitTraining(model_id=model_id, flip=mock_flip)
        controller.log_error = MagicMock()
        controller.system_panic = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        shareable = MagicMock()
        shareable.get_return_code.return_value = ReturnCode.EXECUTION_EXCEPTION
        shareable.get_header.return_value = "Test exception"

        controller._accept_cleanup_result("client1", shareable, fl_ctx)

        controller.system_panic.assert_called_once()
        mock_flip.send_handled_exception.assert_called_once()

    def test_accept_cleanup_result_with_unrecognised_return_code_takes_no_action(self):
        """Only an execution failure panics the run; any other return code is left alone"""
        mock_flip = MagicMock()
        controller = InitTraining(model_id="123e4567-e89b-12d3-a456-426614174000", flip=mock_flip)
        controller.system_panic = MagicMock()

        shareable = MagicMock()
        shareable.get_return_code.return_value = ReturnCode.TASK_ABORTED

        result = controller._accept_cleanup_result("client1", shareable, MagicMock())

        assert result is None
        controller.system_panic.assert_not_called()
        mock_flip.send_handled_exception.assert_not_called()

    def test_process_cleanup_result_checks_the_result_then_clears_it(self):
        """The task callback passes the client's result on and drops it from the task"""
        controller = InitTraining(model_id="123e4567-e89b-12d3-a456-426614174000")
        controller._accept_cleanup_result = MagicMock()

        fl_ctx = MagicMock()
        client_task = MagicMock()
        client_task.client.name = "client1"
        result = client_task.result

        controller._process_cleanup_result(client_task, fl_ctx)

        controller._accept_cleanup_result.assert_called_once_with(client_name="client1", result=result, fl_ctx=fl_ctx)
        assert client_task.result is None

    def test_set_init_training_status_panics_when_the_event_fails(self):
        """A failure announcing the start is logged and panics the run"""
        controller = InitTraining(model_id="123e4567-e89b-12d3-a456-426614174000")
        controller.log_info = MagicMock()
        controller.log_error = MagicMock()
        controller.system_panic = MagicMock()
        controller.fire_event = MagicMock(side_effect=RuntimeError("hub unreachable"))

        fl_ctx = MagicMock()

        controller._set_init_training_status(fl_ctx)

        controller.log_error.assert_called_once_with(fl_ctx, "hub unreachable")
        controller.system_panic.assert_called_once_with("hub unreachable", fl_ctx)

    @patch("flip.nvflare.controllers.init_training.Task")
    def test_control_flow_panics_when_the_cleanup_broadcast_fails(self, mock_task):
        """An exception in the control flow is logged and panics the run instead of escaping"""
        controller = InitTraining(model_id="123e4567-e89b-12d3-a456-426614174000")
        controller.log_info = MagicMock()
        controller.log_exception = MagicMock()
        controller.fire_event = MagicMock()
        controller.system_panic = MagicMock()
        controller.broadcast_and_wait = MagicMock(side_effect=RuntimeError("broadcast failed"))

        fl_ctx = MagicMock()
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, fl_ctx)

        controller.log_exception.assert_called_once()
        assert "broadcast failed" in controller.log_exception.call_args.args[1]
        controller.system_panic.assert_called_once_with("broadcast failed", fl_ctx)
