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

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pandas as pd
import pytest

# Import from the new flip package
from flip.core.base import FLIPBase
from flip.core.standard import FLIPStandardDev, FLIPStandardProd
from flip.constants.flip_constants import FlipConstants, ModelStatus, ResourceType

# Aliases for backward compatibility with test names
FLIP_Parent = FLIPBase
_FLIPDev = FLIPStandardDev
_FLIPProd = FLIPStandardProd


class TestFLIPParentValidation:
    """Test validation methods in FLIP_Parent base class."""

    @pytest.fixture
    def flip_dev(self):
        """Create a _FLIPDev instance for testing inherited methods."""
        return _FLIPDev()

    def test_check_query_valid_string(self, flip_dev):
        """check_query should accept valid string queries."""
        # Should not raise
        flip_dev.check_query("SELECT * FROM table")
        flip_dev.check_query("")
        flip_dev.check_query("complex query with 'quotes'")

    def test_check_query_invalid_type(self, flip_dev):
        """check_query should raise TypeError for non-string inputs."""
        with pytest.raises(TypeError, match="expect query to be string"):
            flip_dev.check_query(123)

        with pytest.raises(TypeError, match="expect query to be string"):
            flip_dev.check_query(None)

        with pytest.raises(TypeError, match="expect query to be string"):
            flip_dev.check_query(["query"])

    def test_check_project_id_valid_string(self, flip_dev):
        """check_project_id should accept valid string project IDs."""
        flip_dev.check_project_id("project-123")
        flip_dev.check_project_id("abc-def-ghi")
        flip_dev.check_project_id("")

    def test_check_project_id_invalid_type(self, flip_dev):
        """check_project_id should raise TypeError for non-string inputs."""
        with pytest.raises(TypeError, match="expect project_id to be string"):
            flip_dev.check_project_id(123)

        with pytest.raises(TypeError, match="expect project_id to be string"):
            flip_dev.check_project_id(None)

    def test_check_accession_id_valid_string(self, flip_dev):
        """check_accession_id should accept valid string accession IDs."""
        flip_dev.check_accession_id("ACC123")
        flip_dev.check_accession_id("accession-456")

    def test_check_accession_id_invalid_type(self, flip_dev):
        """check_accession_id should raise TypeError for non-string inputs."""
        with pytest.raises(TypeError, match="expect accession_id to be string"):
            flip_dev.check_accession_id(123)

        with pytest.raises(TypeError, match="expect accession_id to be string"):
            flip_dev.check_accession_id(None)

    def test_check_resource_type_single_resource(self, flip_dev):
        """check_resource_type should convert single ResourceType to list."""
        result = flip_dev.check_resource_type(ResourceType.NIFTI)
        assert result == [ResourceType.NIFTI]

        result = flip_dev.check_resource_type(ResourceType.DICOM)
        assert result == [ResourceType.DICOM]

    def test_check_resource_type_list_of_resources(self, flip_dev):
        """check_resource_type should accept list of ResourceTypes."""
        resources = [ResourceType.NIFTI, ResourceType.DICOM]
        result = flip_dev.check_resource_type(resources)
        assert result == resources

    def test_check_resource_type_invalid_type(self, flip_dev):
        """check_resource_type should raise TypeError for invalid types."""
        with pytest.raises(TypeError, match="resource_type must be ResourceType"):
            flip_dev.check_resource_type("NIFTI")

        with pytest.raises(TypeError, match="resource_type must be ResourceType"):
            flip_dev.check_resource_type(123)

    def test_check_resource_type_invalid_list_item(self, flip_dev):
        """check_resource_type should raise TypeError if list contains non-ResourceType."""
        with pytest.raises(TypeError, match="Each item in resource_type list must be a ResourceType"):
            flip_dev.check_resource_type([ResourceType.NIFTI, "DICOM"])


class TestFLIPDevGetDataframe:
    """Test _FLIPDev.get_dataframe method."""

    @pytest.fixture
    def flip_dev(self):
        return _FLIPDev()

    def test_get_dataframe_success(self, flip_dev, tmp_path):
        """get_dataframe should return DataFrame from CSV file."""
        # Create a test CSV file
        csv_path = tmp_path / "test_dataframe.csv"
        test_data = pd.DataFrame({"accession_id": ["ACC001", "ACC002"], "label": [0, 1]})
        test_data.to_csv(csv_path, index=False)

        with patch.object(FlipConstants, "DEV_DATAFRAME", str(csv_path)):
            result = flip_dev.get_dataframe("project-id", "SELECT *")

        assert isinstance(result, pd.DataFrame)
        assert "accession_id" in result.columns
        assert len(result) == 2

    def test_get_dataframe_missing_accession_id_column(self, flip_dev, tmp_path):
        """get_dataframe should raise ValueError if CSV lacks accession_id column."""
        csv_path = tmp_path / "bad_dataframe.csv"
        test_data = pd.DataFrame({"other_column": ["A", "B"]})
        test_data.to_csv(csv_path, index=False)

        with patch.object(FlipConstants, "DEV_DATAFRAME", str(csv_path)):
            with pytest.raises(ValueError, match="does not contain an 'accession_id' column"):
                flip_dev.get_dataframe("project-id", "SELECT *")

    def test_get_dataframe_validates_inputs(self, flip_dev):
        """get_dataframe should validate project_id and query types."""
        with pytest.raises(TypeError, match="expect project_id to be string"):
            flip_dev.get_dataframe(123, "SELECT *")

        with pytest.raises(TypeError, match="expect query to be string"):
            flip_dev.get_dataframe("project-id", 123)


class TestFLIPDevGetByAccessionNumber:
    """Test _FLIPDev.get_by_accession_number method."""

    @pytest.fixture
    def flip_dev(self):
        return _FLIPDev()

    def test_get_by_accession_number_creates_directory(self, flip_dev, tmp_path):
        """get_by_accession_number should create directory if it doesn't exist."""
        with patch.object(FlipConstants, "DEV_IMAGES_DIR", str(tmp_path)):
            result = flip_dev.get_by_accession_number("project-id", "ACC123")

        expected_path = tmp_path / "ACC123"
        assert result == expected_path
        assert expected_path.exists()
        assert expected_path.is_dir()

    def test_get_by_accession_number_existing_directory(self, flip_dev, tmp_path):
        """get_by_accession_number should return existing directory path."""
        accession_dir = tmp_path / "ACC456"
        accession_dir.mkdir()

        with patch.object(FlipConstants, "DEV_IMAGES_DIR", str(tmp_path)):
            result = flip_dev.get_by_accession_number("project-id", "ACC456")

        assert result == accession_dir


class TestFLIPDevOtherMethods:
    """Test other _FLIPDev methods that are no-ops or stubs in dev mode."""

    @pytest.fixture
    def flip_dev(self):
        return _FLIPDev()

    def test_add_resource_logs_only(self, flip_dev):
        """add_resource should only log in dev mode, not raise errors."""
        # Should not raise, just logs
        flip_dev.add_resource("project", "accession", "scan", "resource", ["file1.nii"])

    def test_update_status_logs_only(self, flip_dev):
        """update_status should only log in dev mode."""
        flip_dev.update_status("123e4567-e89b-12d3-a456-426614174000", ModelStatus.TRAINING_STARTED)

    def test_send_handled_exception_logs_only(self, flip_dev):
        """send_handled_exception should only log in dev mode."""
        flip_dev.send_handled_exception("Error message", "client-1", "123e4567-e89b-12d3-a456-426614174000")


class TestFLIPDevSendMetricsValue:
    """Test _FLIPDev.send_metrics_value method."""

    @pytest.fixture
    def flip_dev(self):
        return _FLIPDev()

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
        flip_dev.send_metrics_value("loss", 0.5, 1, fl_ctx)

    def test_send_metrics_value_fires_event(self, flip_dev):
        """send_metrics_value should fire event when engine is available."""
        from nvflare.apis.fl_context import FLContext

        fl_ctx = Mock(spec=FLContext)
        mock_engine = Mock()
        fl_ctx.get_engine.return_value = mock_engine

        flip_dev.send_metrics_value("loss", 0.5, 1, fl_ctx)

        mock_engine.fire_event.assert_called_once()


class TestFLIPDevHandleMetricsEvent:
    """Test _FLIPDev.handle_metrics_event method."""

    @pytest.fixture
    def flip_dev(self):
        return _FLIPDev()

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


class MockProdSettings:
    """Mock production settings for testing _FLIPProd."""

    LOCAL_DEV = False
    CENTRAL_HUB_API_URL = "https://central-hub.example.com"
    DATA_ACCESS_API_URL = "https://data-access.example.com"
    IMAGING_API_URL = "https://imaging.example.com"
    IMAGES_DIR = "/data/images"
    PRIVATE_API_KEY_HEADER = "private-api-key"
    PRIVATE_API_KEY = "test-api-key"
    NET_ID = "net-1"
    UPLOADED_FEDERATED_DATA_BUCKET = "s3://test-bucket"


class TestFLIPProdGetDataframe:
    """Test _FLIPProd.get_dataframe method with mocked HTTP requests."""

    @pytest.fixture
    def flip_prod(self):
        return _FLIPProd()

    def test_get_dataframe_success(self, flip_prod):
        """get_dataframe should make API call and return DataFrame."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json.dumps([{"accession_id": "ACC001", "label": 0}])

        with patch("flip.core.standard.FlipConstants", MockProdSettings()):
            with patch("flip.core.standard.requests.post", return_value=mock_response):
                result = flip_prod.get_dataframe("project-id", "SELECT *")

        assert isinstance(result, pd.DataFrame)
        assert "accession_id" in result.columns

    def test_get_dataframe_validates_inputs(self, flip_prod):
        """get_dataframe should validate inputs before making API call."""
        with pytest.raises(TypeError, match="expect query to be string"):
            flip_prod.get_dataframe("project-id", 123)

    def test_get_dataframe_raises_on_http_error(self, flip_prod):
        """get_dataframe should raise on HTTP errors."""
        from requests.exceptions import HTTPError

        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = HTTPError("Server error")

        with patch("flip.core.standard.FlipConstants", MockProdSettings()):
            with patch("flip.core.standard.requests.post", return_value=mock_response):
                with pytest.raises(HTTPError):
                    flip_prod.get_dataframe("project-id", "SELECT *")


class TestFLIPProdGetByAccessionNumber:
    """Test _FLIPProd.get_by_accession_number method."""

    @pytest.fixture
    def flip_prod(self):
        return _FLIPProd()

    def test_get_by_accession_number_success(self, flip_prod):
        """get_by_accession_number should make API call and return Path."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"path": "/data/images/ACC001"}

        with patch("flip.core.standard.FlipConstants", MockProdSettings()):
            with patch("flip.core.standard.requests.post", return_value=mock_response):
                result = flip_prod.get_by_accession_number("project-id", "ACC001")

        assert result == Path("/data/images/ACC001")

    def test_get_by_accession_number_validates_inputs(self, flip_prod):
        """get_by_accession_number should validate inputs."""
        with pytest.raises(TypeError, match="expect project_id to be string"):
            flip_prod.get_by_accession_number(123, "ACC001")

        with pytest.raises(TypeError, match="expect accession_id to be string"):
            flip_prod.get_by_accession_number("project-id", 123)


class TestFLIPProdAddResource:
    """Test _FLIPProd.add_resource method."""

    @pytest.fixture
    def flip_prod(self):
        return _FLIPProd()

    def test_add_resource_validates_project_id(self, flip_prod):
        """add_resource should validate project_id type."""
        with pytest.raises(TypeError, match="expect project id to be string"):
            flip_prod.add_resource(123, "accession", "scan", "resource", ["file"])

    def test_add_resource_validates_accession_id(self, flip_prod):
        """add_resource should validate accession_id type."""
        with pytest.raises(TypeError, match="expect accession_id to be string"):
            flip_prod.add_resource("project", 123, "scan", "resource", ["file"])

    def test_add_resource_validates_scan_id(self, flip_prod):
        """add_resource should validate scan_id type."""
        with pytest.raises(TypeError, match="expect scan_id to be string"):
            flip_prod.add_resource("project", "accession", 123, "resource", ["file"])

    def test_add_resource_validates_resource_id(self, flip_prod):
        """add_resource should validate resource_id type."""
        with pytest.raises(TypeError, match="expect resource_id to be string"):
            flip_prod.add_resource("project", "accession", "scan", 123, ["file"])

    def test_add_resource_validates_files(self, flip_prod):
        """add_resource should validate files type."""
        with pytest.raises(TypeError, match="expect files to be List"):
            flip_prod.add_resource("project", "accession", "scan", "resource", "file")

    def test_add_resource_success(self, flip_prod):
        """add_resource should make API call successfully."""
        mock_response = Mock()
        mock_response.status_code = 200

        with patch("flip.core.standard.FlipConstants", MockProdSettings()):
            with patch("flip.core.standard.requests.put", return_value=mock_response):
                flip_prod.add_resource("project", "accession", "scan", "resource", ["file.nii"])


class TestFLIPProdUpdateStatus:
    """Test _FLIPProd.update_status method."""

    @pytest.fixture
    def flip_prod(self):
        return _FLIPProd()

    def test_update_status_validates_model_id(self, flip_prod):
        """update_status should validate model_id is a valid UUID."""
        with pytest.raises(ValueError, match="Invalid model ID"):
            flip_prod.update_status("not-a-uuid", ModelStatus.TRAINING_STARTED)

    def test_update_status_success(self, flip_prod):
        """update_status should make API call successfully."""
        mock_response = Mock()
        mock_response.status_code = 200

        with patch("flip.core.standard.FlipConstants", MockProdSettings()):
            with patch("flip.core.standard.requests.put", return_value=mock_response):
                flip_prod.update_status("123e4567-e89b-12d3-a456-426614174000", ModelStatus.TRAINING_STARTED)


class TestFLIPProdSendHandledException:
    """Test _FLIPProd.send_handled_exception method."""

    @pytest.fixture
    def flip_prod(self):
        return _FLIPProd()

    def test_send_handled_exception_validates_formatted_exception(self, flip_prod):
        """send_handled_exception should validate formatted_exception type."""
        with pytest.raises(TypeError, match="formatted_exception must be type str"):
            flip_prod.send_handled_exception(123, "client", "123e4567-e89b-12d3-a456-426614174000")

    def test_send_handled_exception_validates_client_name(self, flip_prod):
        """send_handled_exception should validate client_name type."""
        with pytest.raises(TypeError, match="client_name must be type str"):
            flip_prod.send_handled_exception("error", 123, "123e4567-e89b-12d3-a456-426614174000")

    def test_send_handled_exception_validates_model_id(self, flip_prod):
        """send_handled_exception should validate model_id is a valid UUID."""
        with pytest.raises(ValueError, match="Invalid model ID"):
            flip_prod.send_handled_exception("error", "client", "not-a-uuid")

    def test_send_handled_exception_success(self, flip_prod):
        """send_handled_exception should make API call successfully."""
        mock_response = Mock()
        mock_response.status_code = 200

        with patch("flip.core.standard.FlipConstants", MockProdSettings()):
            with patch("flip.core.standard.requests.post", return_value=mock_response):
                flip_prod.send_handled_exception("Error message", "client-1", "123e4567-e89b-12d3-a456-426614174000")


class TestFLIPProdHandleMetricsEvent:
    """Test _FLIPProd.handle_metrics_event method."""

    @pytest.fixture
    def flip_prod(self):
        return _FLIPProd()

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

        with patch("flip.core.standard.FlipConstants", MockProdSettings()):
            with patch("flip.core.standard.requests.post", return_value=mock_response):
                flip_prod.handle_metrics_event(event_data, 1, "123e4567-e89b-12d3-a456-426614174000")
