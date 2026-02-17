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

from unittest.mock import MagicMock

from flip.nvflare.components.flip_client_event_handler import ClientEventHandler


class TestClientEventHandler:
    def test_init(self):
        """Test that ClientEventHandler initializes correctly"""
        handler = ClientEventHandler()
        assert handler is not None
        assert isinstance(handler, ClientEventHandler)

    def test_handle_event_does_nothing(self):
        """Test that handle_event method executes without error"""
        handler = ClientEventHandler()
        fl_ctx = MagicMock()

        # Should not raise any exception
        result = handler.handle_event("some_event", fl_ctx)
        assert result is None
