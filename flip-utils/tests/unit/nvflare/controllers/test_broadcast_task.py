from unittest.mock import MagicMock

import pytest
from nvflare.apis.fl_context import FLContext
from nvflare.apis.signal import Signal

from flip.nvflare.controllers.broadcast_task import BroadcastTask


class TestBroadcastTask:
    def test_validates_arguments(self):
        with pytest.raises(ValueError, match="task_name"):
            BroadcastTask("")
        with pytest.raises(TypeError, match="timeout must be int"):
            BroadcastTask("cleanup", timeout="600")
        with pytest.raises(ValueError, match="greater than or equal"):
            BroadcastTask("cleanup", timeout=-1)

    def test_broadcasts_to_all_configured_clients(self):
        controller = BroadcastTask("post_validation", timeout=42, participating_clients=["site-1", "site-2"])
        controller.broadcast_and_wait = MagicMock()
        signal = Signal()
        fl_ctx = MagicMock(spec=FLContext)

        controller.control_flow(signal, fl_ctx)

        kwargs = controller.broadcast_and_wait.call_args.kwargs
        assert kwargs["task"].name == "post_validation"
        assert kwargs["task"].timeout == 42
        assert kwargs["targets"] == ["site-1", "site-2"]
        assert kwargs["min_responses"] == 0
        assert kwargs["abort_signal"] is signal

    def test_abort_skips_broadcast(self):
        controller = BroadcastTask("post_validation")
        controller.broadcast_and_wait = MagicMock()
        controller.log_info = MagicMock()
        signal = Signal()
        signal.trigger(True)

        controller.control_flow(signal, MagicMock(spec=FLContext))

        controller.broadcast_and_wait.assert_not_called()
