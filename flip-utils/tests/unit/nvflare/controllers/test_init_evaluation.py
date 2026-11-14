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
from unittest.mock import MagicMock, patch

from nvflare.apis.event_type import EventType
from nvflare.apis.shareable import ReturnCode

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

    def test_accept_cleanup_result_with_unrecognised_return_code_takes_no_action(self):
        """Only an execution failure panics the run; any other return code is left alone"""
        mock_flip = MagicMock()
        controller = InitEvaluation(model_id="123e4567-e89b-12d3-a456-426614174000", flip=mock_flip)
        controller.system_panic = MagicMock()

        shareable = MagicMock()
        shareable.get_return_code.return_value = ReturnCode.TASK_ABORTED

        result = controller._accept_cleanup_result("client1", shareable, MagicMock())

        assert result is None
        controller.system_panic.assert_not_called()
        mock_flip.send_handled_exception.assert_not_called()

    def test_process_cleanup_result_checks_the_result_then_clears_it(self):
        """The task callback passes the client's result on and drops it from the task"""
        controller = InitEvaluation(model_id="123e4567-e89b-12d3-a456-426614174000")
        controller._accept_cleanup_result = MagicMock()

        fl_ctx = MagicMock()
        client_task = MagicMock()
        client_task.client.name = "client1"
        result = client_task.result

        controller._process_cleanup_result(client_task, fl_ctx)

        controller._accept_cleanup_result.assert_called_once_with(client_name="client1", result=result, fl_ctx=fl_ctx)
        assert client_task.result is None

    def test_set_init_evaluation_status_panics_when_the_event_fails(self):
        """A failure announcing the start is logged and panics the run"""
        controller = InitEvaluation(model_id="123e4567-e89b-12d3-a456-426614174000")
        controller.log_info = MagicMock()
        controller.log_error = MagicMock()
        controller.system_panic = MagicMock()
        controller.fire_event = MagicMock(side_effect=RuntimeError("hub unreachable"))

        fl_ctx = MagicMock()

        controller._set_init_evaluation_status(fl_ctx)

        controller.log_error.assert_called_once_with(fl_ctx, "hub unreachable")
        controller.system_panic.assert_called_once_with("hub unreachable", fl_ctx)

    @patch("flip.nvflare.controllers.init_evaluation.Task")
    def test_control_flow_panics_when_the_cleanup_broadcast_fails(self, mock_task):
        """An exception in the control flow is logged and panics the run instead of escaping"""
        controller = InitEvaluation(model_id="123e4567-e89b-12d3-a456-426614174000")
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

    def _run_control_flow_with_config(self, tmp_path, config):
        """Run control_flow against an app directory holding ``config`` (None: no config.json)"""
        (tmp_path / "custom").mkdir()
        if config is not None:
            (tmp_path / "custom" / "config.json").write_text(json.dumps(config))

        controller = InitEvaluation(model_id="123e4567-e89b-12d3-a456-426614174000")
        for name in ("log_info", "log_error", "log_exception", "fire_event", "system_panic", "broadcast_and_wait"):
            setattr(controller, name, MagicMock())

        fl_ctx = MagicMock()
        fl_ctx.get_engine.return_value.get_workspace.return_value.get_app_dir.return_value = str(tmp_path)
        abort_signal = MagicMock()
        abort_signal.triggered = False

        with patch("flip.nvflare.controllers.init_evaluation.Task"):
            controller.control_flow(abort_signal, fl_ctx)
        return controller

    def test_control_flow_missing_config_is_reported_and_panics(self, tmp_path):
        """A job without config.json is named in the log and the run panics"""
        controller = self._run_control_flow_with_config(tmp_path, None)

        assert "config.json is a mandatory file" in controller.log_error.call_args.args[1]
        controller.system_panic.assert_called_once()

    def test_control_flow_config_without_models_is_reported_and_panics(self, tmp_path):
        """A config.json with no models key is named in the log and the run panics"""
        controller = self._run_control_flow_with_config(tmp_path, {"other": 1})

        assert "a models key has to be present" in controller.log_error.call_args.args[1]
        controller.system_panic.assert_called_once()

    def test_control_flow_model_without_path_ends_the_run(self, tmp_path):
        """A model entry missing 'checkpoint' or 'path' is named in the log and ends the run"""
        controller = self._run_control_flow_with_config(tmp_path, {"models": {"model1": {"checkpoint": "ckpt1"}}})

        assert "Issue found in model: model1" in controller.log_error.call_args.args[1]
        assert EventType.END_RUN in [call.args[0] for call in controller.fire_event.call_args_list]
        controller.system_panic.assert_not_called()

    def test_control_flow_valid_config_raises_no_error(self, tmp_path):
        """A complete config.json passes the check without an error, END_RUN or panic"""
        config = {"models": {"model1": {"checkpoint": "ckpt1", "path": "/path/to/model"}}}
        controller = self._run_control_flow_with_config(tmp_path, config)

        controller.log_error.assert_not_called()
        assert EventType.END_RUN not in [call.args[0] for call in controller.fire_event.call_args_list]
        controller.system_panic.assert_not_called()
