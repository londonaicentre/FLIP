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

from unittest.mock import MagicMock, patch

import pytest
from nvflare.apis.shareable import ReturnCode

from flip.constants import FlipConstants
from flip.constants.flip_constants import FlipEvents
from flip.controllers.init_evaluation import InitEvaluation


class TestInitEvaluation:
    def test_init_with_valid_uuid(self):
        """Test initialization with valid UUID"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitEvaluation(model_id=model_id)
        assert controller._model_id == model_id
        assert controller._min_clients == FlipConstants.MIN_CLIENTS

    def test_init_with_invalid_uuid_raises_error(self):
        """Test initialization with invalid UUID raises ValueError"""
        mock_flip = MagicMock()
        with pytest.raises(ValueError, match="not a valid UUID"):
            InitEvaluation(model_id="invalid-uuid", flip=mock_flip)

    def test_init_with_min_clients(self):
        """Test initialization with custom min_clients"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitEvaluation(model_id=model_id, min_clients=3)
        assert controller._min_clients == 3

    def test_init_with_invalid_min_clients_raises_error(self):
        """Test initialization with invalid min_clients raises ValueError"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        mock_flip = MagicMock()
        with pytest.raises(ValueError, match="Invalid number of minimum clients"):
            InitEvaluation(model_id=model_id, min_clients=0, flip=mock_flip)

    def test_init_with_negative_cleanup_timeout_raises_error(self):
        """Test initialization with negative cleanup_timeout raises ValueError"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        mock_flip = MagicMock()
        with pytest.raises(ValueError, match="cleanup_timeout must be greater"):
            InitEvaluation(model_id=model_id, cleanup_timeout=-1, flip=mock_flip)

    def test_init_with_custom_cleanup_timeout(self):
        """Test initialization with custom cleanup_timeout"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitEvaluation(model_id=model_id, cleanup_timeout=300)
        assert controller._cleanup_timeout == 300

    def test_start_controller_no_engine(self):
        """Test start_controller when engine is not found"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitEvaluation(model_id=model_id)
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
        controller = InitEvaluation(model_id=model_id)
        controller.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        controller.start_controller(fl_ctx)

        controller.log_info.assert_called()

    @patch("flip.controllers.init_evaluation.Task")
    @patch("flip.controllers.init_evaluation.json.load")
    @patch("builtins.open", create=True)
    @patch("flip.controllers.init_evaluation.os.path.isfile")
    def test_control_flow_success(self, mock_isfile, mock_open, mock_json_load, mock_task):
        """Test successful control_flow"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitEvaluation(model_id=model_id, min_clients=1)
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

    @patch("flip.controllers.init_evaluation.Task")
    @patch("flip.controllers.init_evaluation.json.load")
    @patch("builtins.open", create=True)
    @patch("flip.controllers.init_evaluation.os.path.isfile")
    def test_control_flow_with_abort_signal(self, mock_isfile, mock_open, mock_json_load, mock_task):
        """Test control_flow with abort signal"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitEvaluation(model_id=model_id, min_clients=1)
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

    def test_stop_controller(self):
        """Test stop_controller"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitEvaluation(model_id=model_id)
        controller.log_info = MagicMock()
        controller.cancel_all_tasks = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        controller.stop_controller(fl_ctx)

        controller.cancel_all_tasks.assert_called_once()

    def test_process_result_of_unknown_task(self):
        """Test process_result_of_unknown_task"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitEvaluation(model_id=model_id)
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
        controller = InitEvaluation(model_id=model_id)

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
        controller = InitEvaluation(model_id=model_id, flip=mock_flip)
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
