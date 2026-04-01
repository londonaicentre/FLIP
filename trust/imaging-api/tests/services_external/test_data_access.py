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

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from imaging_api.services_external.data_access import get_dataframe


class TestGetDataframe:
    @pytest.mark.asyncio
    @patch("imaging_api.services_external.data_access.httpx.AsyncClient")
    async def test_success(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"accession_id": "ACC001", "patient_id": "P1"},
            {"accession_id": "ACC002", "patient_id": "P2"},
        ]
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        df = await get_dataframe("encrypted-proj-id", "SELECT * FROM cohort")

        assert len(df) == 2
        assert list(df.columns) == ["accession_id", "patient_id"]
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    @patch("imaging_api.services_external.data_access.httpx.AsyncClient")
    async def test_http_error_raises_runtime_error(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.HTTPError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="HTTP error occurred"):
            await get_dataframe("encrypted-proj-id", "SELECT * FROM cohort")
