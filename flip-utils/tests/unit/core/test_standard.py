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

"""Tests for flip.core.standard module."""

import io
import json
import logging
import math
import re
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest
import requests
from pydantic import HttpUrl
from requests import HTTPError, Request

from flip.constants import ModelStatus, ResourceType
from flip.core.standard import FLIPStandardDev, FLIPStandardProd
from flip.exceptions import ResultsUploadError
from flip.schemas import FLLogEvent


@pytest.fixture
def hub_constants():
    """Provide production reporting settings independently of the local test config."""
    with patch("flip.core.standard.FlipConstants") as constants:
        constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
        constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
        constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret
        yield constants


class TestFLIPStandardDevGetDataframe:
    """Test FLIPStandardDev get_dataframe method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        return FLIPStandardDev()

    def test_get_dataframe_reads_from_csv(self, flip_dev, tmp_path):
        """get_dataframe should read CSV from DEV_DATAFRAME path."""
        # Create a test CSV file
        csv_path = tmp_path / "test_dataframe.csv"
        test_data = pd.DataFrame({"accession_id": ["ACC001", "ACC002"], "label": [0, 1]})
        test_data.to_csv(csv_path, index=False)

        # Patch FlipConstants at the module where it's used
        with patch("flip.core.standard.FlipConstants") as mock_constants:
            mock_constants.DEV_DATAFRAME = str(csv_path)
            df = flip_dev.get_dataframe(project_id="test-project", query="SELECT * FROM table")

            assert isinstance(df, pd.DataFrame)
            assert len(df) == 2
            assert "accession_id" in df.columns
            assert "label" in df.columns

    def test_get_dataframe_accepts_a_frame_without_accession_id(self, flip_dev, tmp_path):
        """Tabular-only cohorts (FLIP#1071) have no accession_id; the dev client must not require one.

        The prod client never did — it returns whatever columns the trust's cohort query projects.
        """
        csv_path = tmp_path / "tabular_dataframe.csv"
        pd.DataFrame({"person_id": [1, 2], "label": [0, 1]}).to_csv(csv_path, index=False)

        with patch("flip.core.standard.FlipConstants") as mock_constants:
            mock_constants.DEV_DATAFRAME = str(csv_path)
            df = flip_dev.get_dataframe(project_id="test-project", query="SELECT person_id, label FROM omop.person")

        assert list(df.columns) == ["person_id", "label"]
        assert len(df) == 2


class TestFLIPStandardDevGetByAccessionNumber:
    """Test FLIPStandardDev get_by_accession_number method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        return FLIPStandardDev()

    def test_get_by_accession_number_returns_path(self, flip_dev, tmp_path):
        """get_by_accession_number should return path from DEV_IMAGES_DIR."""
        images_dir = tmp_path / "images"
        images_dir.mkdir()

        accession_dir = images_dir / "TEST001"
        accession_dir.mkdir(parents=True)

        with patch("flip.core.standard.FlipConstants") as mock_constants:
            mock_constants.DEV_IMAGES_DIR = str(images_dir)
            result = flip_dev.get_by_accession_number(
                project_id="proj-1", accession_id="TEST001", resource_type="dicom"
            )

            assert result == accession_dir

    def test_get_by_accession_number_creates_missing_directory(self, flip_dev, tmp_path):
        """get_by_accession_number should create directory if it doesn't exist."""
        images_dir = tmp_path / "images"
        images_dir.mkdir()

        with patch("flip.core.standard.FlipConstants") as mock_constants:
            mock_constants.DEV_IMAGES_DIR = str(images_dir)
            result = flip_dev.get_by_accession_number(
                project_id="proj-1", accession_id="TEST002", resource_type="dicom"
            )

            assert result.exists()
            assert result == images_dir / "TEST002"


class TestFLIPStandardDevAddResource:
    """Test FLIPStandardDev add_resource method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        return FLIPStandardDev()

    def test_add_resource_runs_without_error_in_dev_mode(self, flip_dev, tmp_path):
        """add_resource should run without error in dev mode (no-op)."""
        resource_path = tmp_path / "test_resource.txt"
        resource_path.write_text("test content")

        # Should not raise any exception - it's a no-op in dev mode
        flip_dev.add_resource(
            project_id="proj-1",
            accession_id="ACC001",
            scan_id="scan-001",
            resource_id="res-001",
            files=[str(resource_path)],
        )


class TestFLIPStandardDevUpdateStatus:
    """Test FLIPStandardDev update_status method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        return FLIPStandardDev()

    def test_update_status_runs_without_error_in_dev_mode(self, flip_dev):
        """update_status should run without error in dev mode (no-op)."""
        # Should not raise any exception - it's a no-op in dev mode, and reports it as accepted
        assert flip_dev.update_status(model_id="model-123", new_model_status=ModelStatus.RUNNING) is True


class TestFLIPStandardDevSendHandledException:
    """Test FLIPStandardDev send_handled_exception method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        return FLIPStandardDev()

    def test_send_handled_exception_runs_without_error_in_dev_mode(self, flip_dev):
        """send_handled_exception should run without error in dev mode (no-op)."""
        # Should not raise any exception - it's a no-op in dev mode
        flip_dev.send_handled_exception(
            formatted_exception="Test exception", client_name="client-1", model_id="model-123"
        )


class TestFLIPStandardDevSendEvent:
    """Test FLIPStandardDev send_event method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        return FLIPStandardDev()

    def test_send_event_runs_without_error_in_dev_mode(self, flip_dev):
        """send_event should run without error in dev mode (local log only)."""
        flip_dev.send_event(
            model_id="model-123",
            event_type=FLLogEvent.ROUND_STARTED,
            global_round=1,
            details={"total_rounds": 3},
        )


class TestFLIPStandardProdSendEvent:
    """Test FLIPStandardProd send_event method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        return FLIPStandardProd()

    def test_hub_event_posts_facts_with_null_client(self, flip_prod):
        """A hub-attributed event posts the typed payload with no client name and no text."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "OK"
        valid_model_id = "550e8400-e29b-41d4-a716-446655440000"

        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", return_value=mock_response) as mock_post,
        ):
            mock_constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
            mock_constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
            mock_constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret

            flip_prod.send_event(
                model_id=valid_model_id,
                event_type=FLLogEvent.ROUND_STARTED,
                global_round=7,
                details={"total_rounds": 15},
            )

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert valid_model_id in call_args[0][0]
            assert "logs" in call_args[0][0]
            payload = call_args.kwargs["json"]
            assert payload["event_type"] == "ROUND_STARTED"
            assert payload["global_round"] == 7
            assert payload["details"] == {"total_rounds": 15}
            assert payload["fl_client_name"] is None
            assert payload["log"] is None

    def test_trust_event_carries_client_name(self, flip_prod):
        """A trust-attributed event forwards the FL client identity for hub-side resolution."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "OK"
        valid_model_id = "550e8400-e29b-41d4-a716-446655440000"

        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", return_value=mock_response) as mock_post,
        ):
            mock_constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
            mock_constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
            mock_constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret

            flip_prod.send_event(
                model_id=valid_model_id,
                event_type=FLLogEvent.CLIENT_RESULT_RECEIVED,
                global_round=7,
                client_name="Trust_2",
                details={"size_bytes": 2411725},
            )

            payload = mock_post.call_args.kwargs["json"]
            assert payload["fl_client_name"] == "Trust_2"
            assert payload["event_type"] == "CLIENT_RESULT_RECEIVED"

    def test_send_event_rejects_invalid_uuid(self, flip_prod):
        """send_event should reject invalid model IDs."""
        with pytest.raises(ValueError, match="Invalid model ID"):
            flip_prod.send_event(model_id="model-123", event_type=FLLogEvent.ROUND_STARTED, global_round=1)

    def test_send_event_swallows_http_errors(self, flip_prod):
        """Telemetry is best-effort: an unreachable hub must never break training."""
        valid_model_id = "550e8400-e29b-41d4-a716-446655440000"

        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", side_effect=ConnectionError("down")),
        ):
            mock_constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
            mock_constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
            mock_constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret

            flip_prod.send_event(model_id=valid_model_id, event_type=FLLogEvent.ROUND_STARTED, global_round=1)

    def test_send_event_swallows_payload_validation_errors(self, flip_prod):
        """A malformed fact (e.g. an un-normalised 0-based round) must be logged and
        dropped, not raised — send_event runs inside result acceptance."""
        valid_model_id = "550e8400-e29b-41d4-a716-446655440000"

        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post") as mock_post,
        ):
            mock_constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
            mock_constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
            mock_constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret

            flip_prod.send_event(model_id=valid_model_id, event_type=FLLogEvent.ROUND_STARTED, global_round=0)

            mock_post.assert_not_called()

    @pytest.mark.parametrize(
        ("verb", "call"),
        [
            ("put", lambda flip: flip.update_status("550e8400-e29b-41d4-a716-446655440000", ModelStatus.ERROR)),
            ("post", lambda flip: flip.send_metrics("Trust_1", "550e8400-e29b-41d4-a716-446655440000", "loss", 0.5, 1)),
            (
                "post",
                lambda flip: flip.send_handled_exception("boom", "Trust_1", "550e8400-e29b-41d4-a716-446655440000"),
            ),
            (
                "post",
                lambda flip: flip.send_event(
                    model_id="550e8400-e29b-41d4-a716-446655440000",
                    event_type=FLLogEvent.ROUND_STARTED,
                    global_round=1,
                ),
            ),
        ],
        ids=["update_status", "send_metrics", "send_handled_exception", "send_event"],
    )
    def test_every_hub_call_is_bounded_by_a_timeout(self, flip_prod, verb, call):
        """The try/except around a hub call cannot catch a hang: a hub that accepts
        TCP and stalls would otherwise freeze the FL loop (send_event/send_metrics
        run inside result acceptance)."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch(f"flip.core.standard.requests.{verb}", return_value=mock_response) as mock_request,
        ):
            mock_constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
            mock_constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
            mock_constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret

            call(flip_prod)

            assert mock_request.call_args.kwargs.get("timeout") is not None, "hub call must carry a timeout"


class TestFLIPStandardProdSendHandledExceptionSuccessFlag:
    """The exception path must persist as a failure row (red dot), not the success default."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        return FLIPStandardProd()

    def test_exception_payload_carries_success_false(self, flip_prod):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "OK"
        valid_model_id = "550e8400-e29b-41d4-a716-446655440000"

        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", return_value=mock_response) as mock_post,
        ):
            mock_constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
            mock_constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
            mock_constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret

            flip_prod.send_handled_exception("Error message", "client-1", valid_model_id)

            payload = mock_post.call_args.kwargs["json"]
            assert payload["success"] is False


class TestFLIPStandardProdGetDataframe:
    """Test FLIPStandardProd get_dataframe method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        return FLIPStandardProd()

    def test_get_dataframe_makes_api_request(self, flip_prod):
        """get_dataframe should make API request to data-access-api."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json.dumps([{"accession_id": "ACC001", "label": 0}])

        # Mock FlipConstants at the module level where it's used
        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", return_value=mock_response) as mock_post,
        ):
            mock_constants.DATA_ACCESS_API_URL = "https://data.example.com"
            # pragma: allowlist nextline secret
            mock_constants.TRUST_INTERNAL_SERVICE_KEY_HEADER = "x-trust-internal-service-key"
            mock_constants.TRUST_INTERNAL_SERVICE_KEY = "test-trust-internal-key"  # pragma: allowlist secret

            df = flip_prod.get_dataframe(project_id="proj-1", query="SELECT * FROM table")

            # Verify API was called
            mock_post.assert_called_once()
            call_args = mock_post.call_args

            # Check that endpoint contains the expected API URL
            assert "cohort/dataframe" in call_args[0][0]

            # data-access-api requires the trust-internal service key on every /cohort
            # route — fl-client must forward the header from its container env.
            assert call_args.kwargs["headers"]["x-trust-internal-service-key"] == "test-trust-internal-key"

            # Verify result is DataFrame
            assert isinstance(df, pd.DataFrame)
            assert len(df) == 1
            assert df["accession_id"].iloc[0] == "ACC001"

    def test_get_dataframe_endpoint_has_no_double_slash_with_httpurl(self, flip_prod):
        """Regression (FLIP#652): a host-only pydantic HttpUrl must not yield a double slash.

        In production DATA_ACCESS_API_URL is a pydantic v2 ``HttpUrl``, which serializes a
        host-only URL with a trailing slash (``http://data-access-api:8000/``). Naive
        f-string concatenation then built ``.../8000//cohort/dataframe`` — a path Starlette
        does not route, so data-access-api returned 404 and the trainer crashed before any
        image fetch (a root cause of the num_samples=0 / NaN clog). The earlier test mocked
        the URL as a bare ``str`` (no trailing slash) and only substring-checked the path, so
        it never caught this. Pin the real ``HttpUrl`` type and assert the exact endpoint.
        """
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json.dumps([{"accession_id": "ACC001", "label": 0}])

        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", return_value=mock_response) as mock_post,
        ):
            mock_constants.DATA_ACCESS_API_URL = HttpUrl("http://data-access-api:8000")
            # pragma: allowlist nextline secret
            mock_constants.TRUST_INTERNAL_SERVICE_KEY_HEADER = "x-trust-internal-service-key"
            mock_constants.TRUST_INTERNAL_SERVICE_KEY = "test-trust-internal-key"  # pragma: allowlist secret

            flip_prod.get_dataframe(project_id="proj-1", query="SELECT * FROM table")

            called_url = mock_post.call_args[0][0]
            assert called_url == "http://data-access-api:8000/cohort/dataframe"
            # No empty path segment anywhere after the scheme.
            assert "//" not in called_url.split("://", 1)[1]

    def test_get_dataframe_forwards_empty_key_header(self, flip_prod):
        """Header is always sent, even when TRUST_INTERNAL_SERVICE_KEY is the default empty string.

        Mirrors the receiver-side fail-closed test in FLIP: if the env var is unset, the
        sender still emits the header (empty), so the receiver rejects with 401 rather than
        silently bypassing auth via a missing-header code path.
        """
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "unauthorized"
        mock_response.raise_for_status.side_effect = HTTPError("401")

        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", return_value=mock_response) as mock_post,
        ):
            mock_constants.DATA_ACCESS_API_URL = "https://data.example.com"
            # pragma: allowlist nextline secret
            mock_constants.TRUST_INTERNAL_SERVICE_KEY_HEADER = "x-trust-internal-service-key"
            mock_constants.TRUST_INTERNAL_SERVICE_KEY = ""

            with pytest.raises(HTTPError):
                flip_prod.get_dataframe(project_id="proj-1", query="SELECT * FROM table")

            mock_post.assert_called_once()
            headers = mock_post.call_args.kwargs["headers"]
            assert "x-trust-internal-service-key" in headers
            assert headers["x-trust-internal-service-key"] == ""

    def test_get_dataframe_does_not_log_cohort_rows(self, flip_prod):
        """FLIP-SR-051: the response body is the cohort's patient rows. These INFO lines are shipped to
        the trust's shared log store, where a later job at the same trust could read an earlier project's
        cohort. get_dataframe must log only the status code and the row count, never the body.
        """
        rows = [
            {"accession_id": "ACC-SECRET-001", "label": 1},
            {"accession_id": "ACC-SECRET-002", "label": 0},
        ]
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json.dumps(rows)

        # The prod logger sets propagate = False, so caplog (which captures via the root logger) never
        # sees these records — attach a capture handler directly to the instance logger instead.
        captured: list[str] = []
        handler = logging.Handler()
        handler.emit = lambda record: captured.append(record.getMessage())
        flip_prod.logger.addHandler(handler)
        try:
            with (
                patch("flip.core.standard.FlipConstants") as mock_constants,
                patch("flip.core.standard.requests.post", return_value=mock_response),
            ):
                mock_constants.DATA_ACCESS_API_URL = "https://data.example.com"
                # pragma: allowlist nextline secret
                mock_constants.TRUST_INTERNAL_SERVICE_KEY_HEADER = "x-trust-internal-service-key"
                mock_constants.TRUST_INTERNAL_SERVICE_KEY = "test-trust-internal-key"  # pragma: allowlist secret

                df = flip_prod.get_dataframe(project_id="proj-1", query="SELECT * FROM table")
        finally:
            flip_prod.logger.removeHandler(handler)

        logged = "\n".join(captured)
        # No cohort content, in any field, reaches the log.
        assert "ACC-SECRET-001" not in logged
        assert "ACC-SECRET-002" not in logged
        assert mock_response.text not in logged
        # The useful operational signal is still logged: the status and the row count.
        assert "Received response status code: 200" in logged
        assert f"Successfully fetched dataframe ({len(df)} rows)" in logged


class TestFLIPStandardProdGetByAccessionNumber:
    """Test FLIPStandardProd get_by_accession_number method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        return FLIPStandardProd()

    def test_get_by_accession_number_downloads_resources(self, flip_prod, tmp_path):
        """get_by_accession_number should download resources from API."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"path": str(tmp_path / "data")}

        # Mock FlipConstants at the module level where it's used
        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", return_value=mock_response) as mock_post,
        ):
            mock_constants.IMAGING_API_URL = "https://imaging.example.com"
            mock_constants.NET_ID = "net-1"
            # pragma: allowlist nextline secret
            mock_constants.TRUST_INTERNAL_SERVICE_KEY_HEADER = "x-trust-internal-service-key"
            mock_constants.TRUST_INTERNAL_SERVICE_KEY = "test-trust-internal-key"  # pragma: allowlist secret

            result = flip_prod.get_by_accession_number(
                project_id="proj-1", accession_id="ACC001", resource_type=ResourceType.DICOM
            )

            # Verify API was called
            mock_post.assert_called_once()
            # imaging-api requires the trust-internal service key on every router except
            # /health — fl-client must forward the header from its container env.
            call_kwargs = mock_post.call_args.kwargs
            assert call_kwargs["headers"]["x-trust-internal-service-key"] == "test-trust-internal-key"
            assert isinstance(result, Path)
            assert str(result) == str(tmp_path / "data")


class TestFLIPStandardProdAddResource:
    """Test FLIPStandardProd add_resource method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        return FLIPStandardProd()

    def test_add_resource_uploads_to_api(self, flip_prod, tmp_path):
        """add_resource should upload file to imaging API."""
        resource_path = tmp_path / "test_resource.txt"
        resource_path.write_text("test content")

        mock_response = Mock()
        mock_response.status_code = 200

        # Mock FlipConstants at the module level where it's used
        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.put", return_value=mock_response) as mock_put,
        ):
            mock_constants.IMAGING_API_URL = "https://imaging.example.com"
            mock_constants.NET_ID = "net-1"
            # pragma: allowlist nextline secret
            mock_constants.TRUST_INTERNAL_SERVICE_KEY_HEADER = "x-trust-internal-service-key"
            mock_constants.TRUST_INTERNAL_SERVICE_KEY = "test-trust-internal-key"  # pragma: allowlist secret

            flip_prod.add_resource(
                project_id="proj-1",
                accession_id="ACC001",
                scan_id="scan-001",
                resource_id="res-001",
                files=[str(resource_path)],
            )

            # Verify API was called
            mock_put.assert_called_once()
            call_args = mock_put.call_args
            assert "upload/images" in call_args[0][0]
            # imaging-api requires the trust-internal service key on every router except
            # /health — fl-client must forward the header from its container env.
            assert call_args.kwargs["headers"]["x-trust-internal-service-key"] == "test-trust-internal-key"


class TestFLIPStandardProdUpdateStatus:
    """Test FLIPStandardProd update_status method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        return FLIPStandardProd()

    def test_update_status_calls_api_with_valid_uuid(self, flip_prod):
        """update_status should call API to update model status with valid UUID."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        # Use a valid UUID
        valid_model_id = "550e8400-e29b-41d4-a716-446655440000"

        # Mock FlipConstants at the module level where it's used
        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.put", return_value=mock_response) as mock_put,
        ):
            mock_constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
            mock_constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
            mock_constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret

            flip_prod.update_status(valid_model_id, ModelStatus.RUNNING)

            # Verify API was called
            mock_put.assert_called_once()
            call_args = mock_put.call_args
            assert valid_model_id in call_args[0][0]
            assert "model" in call_args[0][0]

    def test_update_status_rejects_invalid_uuid(self, flip_prod):
        """update_status should reject invalid model IDs."""
        with pytest.raises(ValueError, match="Invalid model ID"):
            flip_prod.update_status("model-123", ModelStatus.RUNNING)

    @pytest.mark.parametrize(
        ("put", "accepted"),
        [
            ({"return_value": Mock(status_code=200, text="OK")}, True),
            (
                {
                    "return_value": Mock(
                        status_code=500,
                        text="boom",
                        raise_for_status=Mock(side_effect=HTTPError(response=Mock(status_code=500))),
                    )
                },
                False,
            ),
            ({"side_effect": ConnectionError("hub unreachable")}, False),
        ],
        ids=["accepted", "http-error", "unreachable"],
    )
    def test_update_status_says_whether_the_hub_accepted_it(self, flip_prod, put, accepted):
        """A failed update is logged, not raised, so a caller that cannot lose it needs the answer to retry on."""
        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.put", **put),
        ):
            mock_constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
            mock_constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
            mock_constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret

            assert flip_prod.update_status("550e8400-e29b-41d4-a716-446655440000", ModelStatus.ERROR) is accepted


class TestFLIPStandardProdSendHandledException:
    """Test FLIPStandardProd send_handled_exception method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        return FLIPStandardProd()

    def test_send_handled_exception_calls_api_with_valid_uuid(self, flip_prod):
        """send_handled_exception should POST exception to API with valid UUID."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        # Use a valid UUID
        valid_model_id = "550e8400-e29b-41d4-a716-446655440000"

        # Mock FlipConstants at the module level where it's used
        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", return_value=mock_response) as mock_post,
        ):
            mock_constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
            mock_constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
            mock_constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret

            flip_prod.send_handled_exception("Error message", "client-1", valid_model_id)

            # Verify API was called
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert valid_model_id in call_args[0][0]
            assert "logs" in call_args[0][0]

    def test_send_handled_exception_rejects_invalid_uuid(self, flip_prod):
        """send_handled_exception should reject invalid model IDs."""
        with pytest.raises(ValueError, match="Invalid model ID"):
            flip_prod.send_handled_exception("Error message", "client-1", "model-123")


class TestFLIPStandardProdSendMetrics:
    """Test FLIPStandardProd send_metrics method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        return FLIPStandardProd()

    def test_send_metrics_posts_to_flip_api_internal_url(self, flip_prod):
        """send_metrics should POST to the internal flip-api URL with auth header + payload."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        model_id = "550e8400-e29b-41d4-a716-446655440000"

        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", return_value=mock_response) as mock_post,
        ):
            mock_constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
            mock_constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
            mock_constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret

            flip_prod.send_metrics(
                client_name="client-1",
                model_id=model_id,
                label="LOSS_FUNCTION",
                value=0.42,
                global_round=3,
            )

            mock_post.assert_called_once()
            url = mock_post.call_args[0][0]
            assert url == f"https://hub.example.com/model/{model_id}/metrics"
            assert mock_post.call_args.kwargs["json"] == {
                "fl_client_name": "client-1",
                "global_round": 3,
                "label": "LOSS_FUNCTION",
                "result": 0.42,
                "x_value": 3.0,
                "x_label": "Global Rounds",
            }
            assert mock_post.call_args.kwargs["headers"] == {
                "x-internal-service-key": "test-internal-key",
            }

    def test_send_metrics_includes_custom_x_label_in_payload(self, flip_prod):
        """A custom x_label is sent in the payload; omitting it defaults to "Global Rounds" (FLIP#148)."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        model_id = "550e8400-e29b-41d4-a716-446655440000"

        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", return_value=mock_response) as mock_post,
        ):
            mock_constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
            mock_constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
            mock_constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret

            flip_prod.send_metrics(
                client_name="client-1",
                model_id=model_id,
                label="LOSS_FUNCTION",
                value=0.42,
                global_round=3,
                x_label="epoch",
            )

            assert mock_post.call_args.kwargs["json"]["x_label"] == "epoch"

    def test_send_metrics_carries_custom_x_value_and_true_global_round(self, flip_prod):
        """A custom x_value is the plot coordinate; global_round stays as provenance (FLIP#148)."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        model_id = "550e8400-e29b-41d4-a716-446655440000"

        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", return_value=mock_response) as mock_post,
        ):
            mock_constants.FLIP_API_INTERNAL_URL = "https://hub.example.com"
            mock_constants.INTERNAL_SERVICE_KEY_HEADER = "x-internal-service-key"  # pragma: allowlist secret
            mock_constants.INTERNAL_SERVICE_KEY = "test-internal-key"  # pragma: allowlist secret

            flip_prod.send_metrics(
                client_name="client-1",
                model_id=model_id,
                label="VAL_LOSS",
                value=0.42,
                global_round=2,
                x_value=7.5,
                x_label="epoch",
            )

            payload = mock_post.call_args.kwargs["json"]
            assert payload["x_value"] == 7.5
            assert payload["global_round"] == 2

    @pytest.mark.parametrize("field", ["value", "x_value"])
    @pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "minus-inf"])
    def test_non_finite_metric_is_dropped_and_next_metric_sends(self, flip_prod, hub_constants, field, non_finite):
        response = Mock(status_code=204, text="")
        kwargs = {"value": 0.5, "x_value": 2.0, field: non_finite}
        with (
            patch("flip.core.standard.requests.post", return_value=response) as post,
            patch.object(flip_prod.logger, "warning") as warning,
        ):
            flip_prod.send_metrics("Trust_1", "550e8400-e29b-41d4-a716-446655440000", "loss", global_round=2, **kwargs)
            post.assert_not_called()
            warning.assert_called_once()
            assert "Dropping invalid metric" in warning.call_args.args[0]
            assert warning.call_args.args[1:4] == ("loss", "Trust_1", "550e8400-e29b-41d4-a716-446655440000")

            flip_prod.send_metrics("Trust_1", "550e8400-e29b-41d4-a716-446655440000", "loss", 0.25, 2)
            post.assert_called_once()
            assert post.call_args.kwargs["json"]["result"] == 0.25
            # Exercise the real requests serializer; it refuses NaN/Inf before sending.
            Request("POST", "https://hub.example.com/metrics", json=post.call_args.kwargs["json"]).prepare()

    def test_http_error_without_response_is_non_fatal(self, flip_prod, hub_constants):
        failure = HTTPError("no response")
        with (
            patch("flip.core.standard.requests.post", side_effect=failure),
            patch.object(flip_prod.logger, "error") as error,
            patch.object(flip_prod.logger, "exception") as logged,
        ):
            flip_prod.send_metrics("Trust_1", "550e8400-e29b-41d4-a716-446655440000", "loss", 0.5, 2)
            assert "status code unknown" in error.call_args.args[0]
            logged.assert_called_once_with(failure)

    def test_schema_validation_failure_is_non_fatal(self, flip_prod):
        with patch("flip.core.standard.requests.post") as post, patch.object(flip_prod.logger, "warning") as warning:
            flip_prod.send_metrics("Trust_1", "550e8400-e29b-41d4-a716-446655440000", "loss", 0.5, -1)
            post.assert_not_called()
            warning.assert_called_once()


class TestFLIPStandardProdUploadResultsToS3:
    """Test FLIPStandardProd upload_results_to_s3 method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        return FLIPStandardProd()

    @pytest.fixture
    def results_folder(self, tmp_path):
        folder = tmp_path / "results"
        folder.mkdir()
        (folder / "metrics.json").write_text("{}")
        return folder

    @pytest.fixture
    def hub(self):
        """A hub that starts an upload in 100-byte parts, and an object store that takes every part."""
        sent = {"parts": []}

        def post(url, json, headers, timeout):
            sent.setdefault("posts", []).append((url, json, headers))
            body = {"upload": "handle-1", "part_size": 100}
            if url.endswith("/part"):
                body = {"url": f"https://store.test/part-{json['part_number']}?X-Amz-Signature=secret"}
            return Mock(json=Mock(return_value=body))

        def put(url, data, timeout):
            sent["parts"].append((url, len(data), b"".join(data)))
            return Mock(headers={"ETag": f'"etag-{len(sent["parts"])}"'})

        with (
            patch("flip.core.standard.FlipConstants") as constants,
            patch("flip.core.standard._job_credential", return_value="job-credential"),
            patch("flip.core.standard.requests.post", side_effect=post) as mock_post,
            patch("flip.core.standard.requests.put", side_effect=put) as mock_put,
        ):
            constants.FLIP_API_INTERNAL_URL = "http://flip-api:8000/api/"
            constants.INTERNAL_SERVICE_KEY_HEADER = "X-Internal-Service-Key"  # pragma: allowlist secret
            constants.INTERNAL_SERVICE_KEY = "internal-key"  # pragma: allowlist secret
            sent["post"], sent["put"] = mock_post, mock_put
            yield sent

    def test_upload_results_to_s3_sends_the_zip_in_parts_through_the_hub(self, flip_prod, results_folder, hub):
        """The zip is started on the hub, PUT part by part to the URLs it signs, then completed."""
        flip_prod.upload_results_to_s3(results_folder, "model-123")

        base = "http://flip-api:8000/api/model/model-123/results-upload"
        (start_url, start, headers), *parts, (complete_url, complete, _) = hub["posts"]
        size = start["size"]
        assert start_url == base
        assert re.fullmatch(r"\d{8}_\d{6}\.zip", start["file_name"])
        assert headers == {"X-Internal-Service-Key": "internal-key", "X-Job-Credential": "job-credential"}

        assert size > 100, "the fixture's zip must span several parts"
        assert [(url, body["part_number"]) for url, body, _ in parts] == [
            (f"{base}/part", number) for number in range(1, math.ceil(size / 100) + 1)
        ]
        assert [length for _, length, _ in hub["parts"]] == [100] * (size // 100) + [size % 100]
        assert all(len(body) == length for _, length, body in hub["parts"])
        assert zipfile.ZipFile(io.BytesIO(b"".join(body for _, _, body in hub["parts"]))).namelist() == ["metrics.json"]

        assert complete_url == f"{base}/complete"
        assert all(body["upload"] == "handle-1" for _, body, _ in parts)
        assert complete == {
            "upload": "handle-1",
            "etags": [f'"etag-{number}"' for number in range(1, len(parts) + 1)],
        }

    def test_upload_results_to_s3_retries_a_failed_part_with_a_fresh_url(self, flip_prod, results_folder, hub):
        """A part that fails once is sent again from its own offset."""
        put = hub["put"].side_effect
        failures = iter([requests.ConnectionError("reset")])

        def flaky_put(url, data, timeout):
            for failure in failures:
                raise failure
            return put(url, data, timeout)

        hub["put"].side_effect = flaky_put

        flip_prod.upload_results_to_s3(results_folder, "model-123")

        part_requests = [body["part_number"] for url, body, _ in hub["posts"] if url.endswith("/part")]
        assert part_requests[:2] == [1, 1]
        assert zipfile.ZipFile(io.BytesIO(b"".join(body for _, _, body in hub["parts"]))).namelist() == ["metrics.json"]

    def test_upload_results_to_s3_raises_when_a_part_keeps_failing(
        self, flip_prod, results_folder, hub, caplog, monkeypatch
    ):
        """A part that never lands fails the upload, uncompleted, without logging the signed URL."""
        response = Mock(status_code=403)
        response.raise_for_status.side_effect = HTTPError("403 for url: https://store.test/?X-Amz-Signature=secret")
        hub["put"].side_effect = None
        hub["put"].return_value = response
        monkeypatch.setattr(flip_prod.logger, "propagate", True)

        with pytest.raises(ResultsUploadError):
            flip_prod.upload_results_to_s3(results_folder, "model-123")

        assert hub["put"].call_count == 3
        last_url, last_body, _ = hub["posts"][-1]
        assert (last_url.rsplit("/", 1)[1], last_body) == ("abort", {"upload": "handle-1"})
        assert not any(url.endswith("/complete") for url, _, _ in hub["posts"])
        assert "X-Amz-Signature" not in caplog.text

    def test_upload_results_to_s3_raises_when_the_hub_refuses(self, flip_prod, results_folder, hub):
        """A hub refusal (another job's model, a finished job) fails the upload before any part is sent."""
        refusal = Mock(status_code=403)
        refusal.raise_for_status.side_effect = HTTPError("403", response=refusal)
        hub["post"].side_effect = None
        hub["post"].return_value = refusal

        with pytest.raises(ResultsUploadError):
            flip_prod.upload_results_to_s3(results_folder, "model-123")

        hub["put"].assert_not_called()

    def test_upload_results_to_s3_raises_when_archive_creation_fails(self, flip_prod, tmp_path):
        """upload_results_to_s3 should raise ResultsUploadError when archiving fails."""
        results_folder = tmp_path / "results"
        results_folder.mkdir()

        with patch("flip.core.standard.shutil.make_archive", side_effect=RuntimeError("archive failed")):
            with pytest.raises(ResultsUploadError, match="Unexpected failure uploading results to S3"):
                flip_prod.upload_results_to_s3(results_folder, "model-123")


class TestFLIPStandardProdCleanup:
    """Test FLIPStandardProd cleanup method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        return FLIPStandardProd()

    def test_cleanup_removes_existing_path(self, flip_prod, tmp_path):
        """cleanup should remove the provided path recursively."""
        cleanup_dir = tmp_path / "to_cleanup"
        cleanup_dir.mkdir()
        (cleanup_dir / "file.txt").write_text("data")

        flip_prod.cleanup(cleanup_dir)

        assert not cleanup_dir.exists()

    def test_cleanup_raises_when_rmtree_fails(self, flip_prod, tmp_path):
        """cleanup should raise when underlying removal fails."""
        cleanup_dir = tmp_path / "to_cleanup"

        with patch("flip.core.standard.shutil.rmtree", side_effect=OSError("permission denied")) as mock_rmtree:
            with pytest.raises(Exception, match="Failed to clean up path"):
                flip_prod.cleanup(cleanup_dir)

            mock_rmtree.assert_called_once_with(cleanup_dir)


class TestFLIPStandardDevUploadAndCleanup:
    """Test FLIPStandardDev upload_results_to_s3 and cleanup methods."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        return FLIPStandardDev()

    def test_upload_results_to_s3_runs_without_error_in_dev_mode(self, flip_dev, tmp_path):
        """upload_results_to_s3 should run without error in dev mode (no-op)."""
        results_folder = tmp_path / "results"
        results_folder.mkdir()

        flip_dev.upload_results_to_s3(results_folder, "model-123")

    def test_cleanup_does_not_delete_path_in_dev_mode(self, flip_dev, tmp_path):
        """cleanup should not delete files in dev mode."""
        cleanup_dir = tmp_path / "to_cleanup"
        cleanup_dir.mkdir()
        (cleanup_dir / "file.txt").write_text("data")

        flip_dev.cleanup(cleanup_dir)

        assert cleanup_dir.exists()


class TestJobCredential:
    """Every hub call about a model presents the credential of that model's job."""

    MODEL_ID = "550e8400-e29b-41d4-a716-446655440000"
    OTHER_MODEL_ID = "650e8400-e29b-41d4-a716-446655440000"
    CREDENTIAL = "job-credential-sentinel"  # pragma: allowlist secret (synthetic sentinel)

    @pytest.fixture
    def credential_dir(self, hub_constants, tmp_path):
        hub_constants.JOB_CREDENTIAL_DIR = str(tmp_path)
        (tmp_path / self.MODEL_ID).write_text(self.CREDENTIAL)
        return tmp_path

    @pytest.mark.parametrize(
        ("method", "call"),
        [
            ("put", lambda flip, model_id: flip.update_status(model_id, ModelStatus.RUNNING)),
            ("post", lambda flip, model_id: flip.send_metrics("Trust_1", model_id, "loss", 0.5, 1)),
            ("post", lambda flip, model_id: flip.send_handled_exception("boom", "Trust_1", model_id)),
            ("post", lambda flip, model_id: flip.send_event(model_id, FLLogEvent.ROUND_STARTED, 1)),
        ],
    )
    def test_hub_calls_present_the_models_job_credential(self, credential_dir, method, call):
        with patch(f"flip.core.standard.requests.{method}", return_value=Mock(status_code=200, text="OK")) as sent:
            call(FLIPStandardProd(), self.MODEL_ID)

        assert sent.call_args.kwargs["headers"] == {
            "x-internal-service-key": "test-internal-key",  # pragma: allowlist secret
            "X-Job-Credential": self.CREDENTIAL,
        }

    def test_another_models_credential_is_never_sent(self, credential_dir):
        with patch("flip.core.standard.requests.put", return_value=Mock(status_code=200, text="OK")) as sent:
            FLIPStandardProd().update_status(self.OTHER_MODEL_ID, ModelStatus.RUNNING)

        assert "X-Job-Credential" not in sent.call_args.kwargs["headers"]

    def test_no_credential_directory_sends_the_internal_key_alone(self, hub_constants):
        hub_constants.JOB_CREDENTIAL_DIR = ""
        with patch("flip.core.standard.requests.put", return_value=Mock(status_code=200, text="OK")) as sent:
            FLIPStandardProd().update_status(self.MODEL_ID, ModelStatus.RUNNING)

        assert sent.call_args.kwargs["headers"] == {
            "x-internal-service-key": "test-internal-key"  # pragma: allowlist secret
        }
