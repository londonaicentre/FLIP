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

from unittest.mock import MagicMock

from flip.constants import FlipEvents
from flip.nvflare.controllers.init_training import InitTraining


class TestInitTraining:
    def test_init(self):
        assert InitTraining() is not None

    def test_start_controller_no_engine(self):
        """Test start_controller when engine is not found"""
        controller = InitTraining()
        controller.system_panic = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value = None

        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()
        assert "Engine not found" in str(controller.system_panic.call_args)

    def test_start_controller_success(self):
        """Test successful start_controller"""
        controller = InitTraining()
        controller.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value = MagicMock()

        controller.start_controller(fl_ctx)

        controller.log_info.assert_called()

    def test_control_flow_fires_training_initiated(self):
        """control_flow's whole job is the hub status event — no cleanup broadcast any more
        (imaging retention moved trust-side to imaging-api's TTL sweeper, FLIP#1050)."""
        controller = InitTraining()
        controller.log_info = MagicMock()
        controller.fire_event = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, fl_ctx)

        controller.fire_event.assert_called_once_with(FlipEvents.TRAINING_INITIATED, fl_ctx)
        assert not hasattr(controller, "_cleanup_timeout")

    def test_control_flow_panics_on_status_failure(self):
        """A failing status event must panic the run, not pass silently."""
        controller = InitTraining()
        controller.log_info = MagicMock()
        controller.log_error = MagicMock()
        controller.log_exception = MagicMock()
        controller.system_panic = MagicMock()
        controller.fire_event = MagicMock(side_effect=RuntimeError("hub unreachable"))

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        controller.control_flow(MagicMock(), fl_ctx)

        controller.system_panic.assert_called()

    def test_stop_controller_cancels_tasks(self):
        controller = InitTraining()
        controller.log_info = MagicMock()
        controller.cancel_all_tasks = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        controller.stop_controller(fl_ctx)

        controller.cancel_all_tasks.assert_called_once()
