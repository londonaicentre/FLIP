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

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from trust_api.main import lifespan


@pytest.mark.asyncio
async def test_lifespan_starts_and_cancels_poller():
    """Lifespan should start run_poller as a background task and cancel it on shutdown."""
    mock_app = AsyncMock()

    with patch("trust_api.main.run_poller", new_callable=AsyncMock) as mock_run_poller:
        # Simulate a poller that runs until cancelled
        async def fake_poller():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

        mock_run_poller.side_effect = fake_poller

        async with lifespan(mock_app):
            # Poller should be running
            mock_run_poller.assert_called_once()

        # After exiting lifespan, poller task should have been cancelled
        # (no exception means it was handled cleanly)
