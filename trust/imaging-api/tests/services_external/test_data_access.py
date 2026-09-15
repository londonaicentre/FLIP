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

from imaging_api.config import get_settings
from imaging_api.services_external.data_access import get_accession_ids
from imaging_api.utils.exceptions import CohortBelowThresholdError


class TestGetAccessionIds:
    @pytest.mark.asyncio
    @patch("imaging_api.services_external.data_access.httpx.AsyncClient")
    async def test_success(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {"accession_ids": ["ACC001", "ACC002"]}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(get_settings(), "TRUST_INTERNAL_SERVICE_KEY", "outbound-test-key"):
            accession_ids = await get_accession_ids("encrypted-proj-id", "SELECT * FROM cohort")

        assert accession_ids == ["ACC001", "ACC002"]
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args.kwargs
        assert call_kwargs["json"] == {
            "encrypted_project_id": "encrypted-proj-id",
            "query": "SELECT * FROM cohort",
        }
        # Endpoint must be the projected accession-ids one, not the raw dataframe one.
        assert mock_client.post.call_args.args[0].endswith("/cohort/accession-ids")
        # data-access-api now requires the trust-internal service key on every /cohort
        # route; imaging-api is one of those callers and must forward the plaintext key.
        assert (
            call_kwargs["headers"][get_settings().TRUST_INTERNAL_SERVICE_KEY_HEADER]
            == "outbound-test-key"  # pragma: allowlist secret
        )

    @pytest.mark.asyncio
    @patch("imaging_api.services_external.data_access.httpx.AsyncClient")
    async def test_rows_of_the_same_study_collapse_to_one_accession_in_query_order(self, mock_client_cls):
        """One image_occurrence per series gives the same accession several times; a study imports once."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"accession_ids": ["ACC2", "ACC2", "ACC1", "ACC2", "ACC3", "ACC1"]}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(get_settings(), "TRUST_INTERNAL_SERVICE_KEY", "outbound-test-key"):
            accession_ids = await get_accession_ids("encrypted-proj-id", "SELECT * FROM cohort")

        assert accession_ids == ["ACC2", "ACC1", "ACC3"]

    @pytest.mark.asyncio
    @patch("imaging_api.services_external.data_access.logger")
    @patch("imaging_api.services_external.data_access.httpx.AsyncClient")
    async def test_blank_accession_ids_are_dropped_and_warned_about_not_sent_to_the_pacs(
        self, mock_client_cls, mock_logger
    ):
        """A blank accession number is universal matching to a PACS (every study), so it never leaves here."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"accession_ids": ["ACC1", "", "   ", "ACC2", None, "ACC1"]}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(get_settings(), "TRUST_INTERNAL_SERVICE_KEY", "outbound-test-key"):
            accession_ids = await get_accession_ids("encrypted-proj-id", "SELECT * FROM cohort")

        assert accession_ids == ["ACC1", "ACC2"]
        mock_logger.warning.assert_called_once()
        assert "dropping 3 accession id(s)" in mock_logger.warning.call_args.args[0]

    @pytest.mark.asyncio
    @patch("imaging_api.services_external.data_access.logger")
    @patch("imaging_api.services_external.data_access.httpx.AsyncClient")
    async def test_a_cohort_of_only_blank_accession_ids_pulls_nothing(self, mock_client_cls, mock_logger):
        """All-blank is an empty pull list — the same outcome as a cohort with no imaging rows — not an error."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"accession_ids": ["", " "]}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(get_settings(), "TRUST_INTERNAL_SERVICE_KEY", "outbound-test-key"):
            accession_ids = await get_accession_ids("encrypted-proj-id", "SELECT * FROM cohort")

        assert accession_ids == []
        mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    @patch("imaging_api.services_external.data_access.logger")
    @patch("imaging_api.services_external.data_access.httpx.AsyncClient")
    async def test_wildcard_and_multi_value_accession_ids_are_dropped_like_blanks(self, mock_client_cls, mock_logger):
        """`*` and `?` are C-FIND wildcards and `\\` is DICOM's value delimiter: each is a multi-study query."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "accession_ids": ["ACC1", "ACC*", "*", "AC?1", "ACC1\\ACC2", "ACC2"],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(get_settings(), "TRUST_INTERNAL_SERVICE_KEY", "outbound-test-key"):
            accession_ids = await get_accession_ids("encrypted-proj-id", "SELECT * FROM cohort")

        assert accession_ids == ["ACC1", "ACC2"]
        mock_logger.warning.assert_called_once()
        assert "dropping 4 accession id(s)" in mock_logger.warning.call_args.args[0]

    @pytest.mark.asyncio
    @patch("imaging_api.services_external.data_access.httpx.AsyncClient")
    async def test_empty_response(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {"accession_ids": []}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        accession_ids = await get_accession_ids("encrypted-proj-id", "SELECT * FROM cohort")

        assert accession_ids == []

    @pytest.mark.asyncio
    @patch("imaging_api.services_external.data_access.httpx.AsyncClient")
    async def test_http_error_raises_runtime_error(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.HTTPError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="HTTP error occurred"):
            await get_accession_ids("encrypted-proj-id", "SELECT * FROM cohort")

    @staticmethod
    def _client_returning_status(mock_client_cls, status_code: int):
        """Wires the mocked client so ``raise_for_status`` raises for ``status_code``."""
        request = httpx.Request("POST", "http://data-access-api/cohort/accession-ids")
        response = httpx.Response(status_code, request=request)

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("error", request=request, response=response)
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

    @pytest.mark.asyncio
    @patch("imaging_api.services_external.data_access.httpx.AsyncClient")
    async def test_403_raises_cohort_below_threshold(self, mock_client_cls):
        """A 403 is the trust refusing to release identifiers for a too-small cohort.

        It must be typed distinctly from a transport failure: callers report it as a settled
        outcome rather than an error to retry, and retrying cannot change the answer.
        """
        self._client_returning_status(mock_client_cls, 403)

        with pytest.raises(CohortBelowThresholdError, match="below the trust's minimum size"):
            await get_accession_ids("encrypted-proj-id", "SELECT * FROM cohort")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [400, 401, 500, 503])
    @patch("imaging_api.services_external.data_access.httpx.AsyncClient")
    async def test_other_status_errors_still_raise_runtime_error(self, mock_client_cls, status_code):
        """Only 403 is special-cased; every other non-2xx stays a RuntimeError."""
        self._client_returning_status(mock_client_cls, status_code)

        with pytest.raises(RuntimeError, match="HTTP error occurred"):
            await get_accession_ids("encrypted-proj-id", "SELECT * FROM cohort")
