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

"""Tests for flip.core.standard module."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest


class TestFLIPStandardDevGetDataframe:
    """Test FLIPStandardDev get_dataframe method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        from flip.core.standard import FLIPStandardDev

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

    def test_get_dataframe_validates_accession_id_column(self, flip_dev, tmp_path):
        """get_dataframe should raise error if accession_id column is missing."""
        # Create a CSV without accession_id column
        csv_path = tmp_path / "invalid_dataframe.csv"
        test_data = pd.DataFrame({"some_column": ["val1", "val2"], "label": [0, 1]})
        test_data.to_csv(csv_path, index=False)

        with patch("flip.core.standard.FlipConstants") as mock_constants:
            mock_constants.DEV_DATAFRAME = str(csv_path)
            with pytest.raises(ValueError, match="does not contain an 'accession_id' column"):
                flip_dev.get_dataframe(project_id="test-project", query="SELECT * FROM table")


class TestFLIPStandardDevGetByAccessionNumber:
    """Test FLIPStandardDev get_by_accession_number method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        from flip.core.standard import FLIPStandardDev

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
        from flip.core.standard import FLIPStandardDev

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
        from flip.core.standard import FLIPStandardDev

        return FLIPStandardDev()

    def test_update_status_runs_without_error_in_dev_mode(self, flip_dev):
        """update_status should run without error in dev mode (no-op)."""
        from flip.constants import ModelStatus

        # Should not raise any exception - it's a no-op in dev mode
        flip_dev.update_status(model_id="model-123", new_model_status=ModelStatus.TRAINING_STARTED)


class TestFLIPStandardDevSendHandledException:
    """Test FLIPStandardDev send_handled_exception method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        from flip.core.standard import FLIPStandardDev

        return FLIPStandardDev()

    def test_send_handled_exception_runs_without_error_in_dev_mode(self, flip_dev):
        """send_handled_exception should run without error in dev mode (no-op)."""
        # Should not raise any exception - it's a no-op in dev mode
        flip_dev.send_handled_exception(
            formatted_exception="Test exception", client_name="client-1", model_id="model-123"
        )


class TestFLIPStandardProdGetDataframe:
    """Test FLIPStandardProd get_dataframe method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        from flip.core.standard import FLIPStandardProd

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

            df = flip_prod.get_dataframe(project_id="proj-1", query="SELECT * FROM table")

            # Verify API was called
            mock_post.assert_called_once()
            call_args = mock_post.call_args

            # Check that endpoint contains the expected API URL
            assert "cohort/dataframe" in call_args[0][0]

            # Verify result is DataFrame
            assert isinstance(df, pd.DataFrame)
            assert len(df) == 1
            assert df["accession_id"].iloc[0] == "ACC001"


class TestFLIPStandardProdGetByAccessionNumber:
    """Test FLIPStandardProd get_by_accession_number method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        from flip.core.standard import FLIPStandardProd

        return FLIPStandardProd()

    def test_get_by_accession_number_downloads_resources(self, flip_prod, tmp_path):
        """get_by_accession_number should download resources from API."""
        from flip.constants import ResourceType

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

            result = flip_prod.get_by_accession_number(
                project_id="proj-1", accession_id="ACC001", resource_type=ResourceType.DICOM
            )

            # Verify API was called
            mock_post.assert_called_once()
            assert isinstance(result, Path)
            assert str(result) == str(tmp_path / "data")


class TestFLIPStandardProdAddResource:
    """Test FLIPStandardProd add_resource method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        from flip.core.standard import FLIPStandardProd

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


class TestFLIPStandardProdUpdateStatus:
    """Test FLIPStandardProd update_status method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        from flip.core.standard import FLIPStandardProd

        return FLIPStandardProd()

    def test_update_status_calls_api_with_valid_uuid(self, flip_prod):
        """update_status should call API to update model status with valid UUID."""
        from flip.constants import ModelStatus

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
            mock_constants.CENTRAL_HUB_API_URL = "https://hub.example.com"
            mock_constants.PRIVATE_API_KEY_HEADER = "x-api-key"
            mock_constants.PRIVATE_API_KEY = "test-key"

            flip_prod.update_status(valid_model_id, ModelStatus.TRAINING_STARTED)

            # Verify API was called
            mock_put.assert_called_once()
            call_args = mock_put.call_args
            assert valid_model_id in call_args[0][0]
            assert "model" in call_args[0][0]

    def test_update_status_rejects_invalid_uuid(self, flip_prod):
        """update_status should reject invalid model IDs."""
        from flip.constants import ModelStatus

        with pytest.raises(ValueError, match="Invalid model ID"):
            flip_prod.update_status("model-123", ModelStatus.TRAINING_STARTED)


class TestFLIPStandardProdSendHandledException:
    """Test FLIPStandardProd send_handled_exception method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        from flip.core.standard import FLIPStandardProd

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
            mock_constants.CENTRAL_HUB_API_URL = "https://hub.example.com"
            mock_constants.PRIVATE_API_KEY_HEADER = "x-api-key"
            mock_constants.PRIVATE_API_KEY = "test-key"

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


class TestFLIPStandardDevSendMetricsValue:
    """Test FLIPStandardDev.send_metrics_value method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        from flip.core.standard import FLIPStandardDev

        return FLIPStandardDev()

    def test_send_metrics_value_validates_label(self, flip_dev):
        """send_metrics_value should raise TypeError for non-string label."""
        fl_ctx = Mock()
        with pytest.raises(TypeError, match="expect label to be string"):
            flip_dev.send_metrics_value(123, 0.5, 1, fl_ctx)

    def test_send_metrics_value_validates_fl_ctx(self, flip_dev):
        """send_metrics_value should raise TypeError for invalid fl_ctx."""
        with pytest.raises(TypeError, match="expect fl_ctx to be FLContext"):
            flip_dev.send_metrics_value("loss", 0.5, 1, "not_a_context")

    def test_send_metrics_value_handles_missing_engine(self, flip_dev):
        """send_metrics_value should handle missing engine gracefully."""
        from nvflare.apis.fl_context import FLContext

        fl_ctx = Mock(spec=FLContext)
        fl_ctx.get_engine.return_value = None

        # Should not raise, just logs error
        flip_dev.send_metrics_value("loss", 0.5, fl_ctx, round=1)

    def test_send_metrics_value_fires_event(self, flip_dev):
        """send_metrics_value should fire event when engine is available."""
        from nvflare.apis.fl_context import FLContext

        fl_ctx = Mock(spec=FLContext)
        mock_engine = Mock()
        fl_ctx.get_engine.return_value = mock_engine

        flip_dev.send_metrics_value("loss", 0.5, fl_ctx, round=1)

        mock_engine.fire_event.assert_called_once()


class TestFLIPStandardDevHandleMetricsEvent:
    """Test FLIPStandardDev.handle_metrics_event method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        from flip.core.standard import FLIPStandardDev

        return FLIPStandardDev()

    def test_handle_metrics_event_extracts_data(self, flip_dev):
        """handle_metrics_event should extract and process metrics data."""
        from nvflare.apis.dxo import DXO, DataKind
        from nvflare.apis.fl_constant import FedEventHeader

        # Create mock event_data
        dxo = DXO(data_kind=DataKind.METRICS, data={"label": "loss", "value": 0.5, "round": 1})
        event_data = dxo.to_shareable()
        event_data.set_header(FedEventHeader.ORIGIN, "site-1")

        # Should not raise, processes data
        flip_dev.handle_metrics_event(event_data, 1, "123e4567-e89b-12d3-a456-426614174000")


class TestFLIPStandardProdHandleMetricsEvent:
    """Test FLIPStandardProd.handle_metrics_event method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance."""
        from flip.core.standard import FLIPStandardProd

        return FLIPStandardProd()

    def test_handle_metrics_event_validates_model_id(self, flip_prod):
        """handle_metrics_event should validate model_id is a valid UUID."""
        from nvflare.apis.shareable import Shareable

        event_data = Shareable()
        with pytest.raises(ValueError, match="Invalid model ID"):
            flip_prod.handle_metrics_event(event_data, 1, "not-a-uuid")

    def test_handle_metrics_event_validates_global_round(self, flip_prod):
        """handle_metrics_event should validate global_round type."""
        from nvflare.apis.shareable import Shareable

        event_data = Shareable()
        with pytest.raises(TypeError, match="global_round must be type int"):
            flip_prod.handle_metrics_event(event_data, "1", "123e4567-e89b-12d3-a456-426614174000")

    def test_handle_metrics_event_validates_event_data(self, flip_prod):
        """handle_metrics_event should validate event_data type."""
        with pytest.raises(TypeError, match="event_data must be type Shareable"):
            flip_prod.handle_metrics_event("not_shareable", 1, "123e4567-e89b-12d3-a456-426614174000")

    def test_handle_metrics_event_success(self, flip_prod):
        """handle_metrics_event should make API call successfully."""
        from nvflare.apis.dxo import DXO, DataKind
        from nvflare.apis.fl_constant import FedEventHeader

        dxo = DXO(data_kind=DataKind.METRICS, data={"label": "loss", "value": 0.5, "round": 1})
        event_data = dxo.to_shareable()
        event_data.set_header(FedEventHeader.ORIGIN, "site-1")

        mock_response = Mock()
        mock_response.status_code = 200

        with (
            patch("flip.core.standard.FlipConstants") as mock_constants,
            patch("flip.core.standard.requests.post", return_value=mock_response),
        ):
            mock_constants.CENTRAL_HUB_API_URL = "https://hub.example.com"
            mock_constants.PRIVATE_API_KEY_HEADER = "x-api-key"
            mock_constants.PRIVATE_API_KEY = "test-key"

            flip_prod.handle_metrics_event(event_data, 1, "123e4567-e89b-12d3-a456-426614174000")
