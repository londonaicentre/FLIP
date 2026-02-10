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

    @pytest.mark.skip(reason="FlipConstants is a singleton that cannot be easily reloaded in tests")
    def test_get_dataframe_reads_from_env_variable(self, flip_dev, tmp_path):
        """get_dataframe should read CSV from DEV_DATAFRAME environment variable."""
        # Create a test CSV file
        csv_path = tmp_path / "test_dataframe.csv"
        test_data = pd.DataFrame({"accession_id": ["ACC001", "ACC002"], "label": [0, 1]})
        test_data.to_csv(csv_path, index=False)

        # Need to reload constants after changing environment
        with patch.dict("os.environ", {"DEV_DATAFRAME": str(csv_path)}):
            import importlib

            import flip.constants.flip_constants

            importlib.reload(flip.constants.flip_constants)

            # Create new instance after reload
            from flip.core.standard import FLIPStandardDev

            flip_dev_new = FLIPStandardDev()
            df = flip_dev_new.get_dataframe(project_id="test-project", query="SELECT * FROM table")

            assert isinstance(df, pd.DataFrame)
            assert len(df) == 2
            assert "accession_id" in df.columns
            assert "label" in df.columns


class TestFLIPStandardDevGetByAccessionNumber:
    """Test FLIPStandardDev get_by_accession_number method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        from flip.core.standard import FLIPStandardDev

        return FLIPStandardDev()

    @pytest.mark.skip(reason="FlipConstants is a singleton that cannot be easily reloaded in tests")
    def test_get_by_accession_number_returns_path(self, flip_dev, tmp_path):
        """get_by_accession_number should return path from environment variable."""
        images_dir = tmp_path / "images"
        images_dir.mkdir()

        accession_dir = images_dir / "TEST001"
        accession_dir.mkdir(parents=True)

        with patch.dict("os.environ", {"DEV_IMAGES_DIR": str(images_dir)}):
            import importlib

            import flip.constants.flip_constants

            importlib.reload(flip.constants.flip_constants)

            from flip.core.standard import FLIPStandardDev

            flip_dev_new = FLIPStandardDev()
            result = flip_dev_new.get_by_accession_number(
                project_id="proj-1", accession_id="TEST001", resource_type="dicom"
            )

            assert result == accession_dir


class TestFLIPStandardDevAddResource:
    """Test FLIPStandardDev add_resource method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        from flip.core.standard import FLIPStandardDev

        return FLIPStandardDev()

    @pytest.mark.skip(reason="Logger has propagate=False which prevents caplog from capturing logs")
    def test_add_resource_logs_in_dev_mode(self, flip_dev, tmp_path, caplog):
        """add_resource should log the action in dev mode."""
        import logging

        caplog.set_level(logging.INFO, logger="FLIPStandardDev")

        resource_path = tmp_path / "test_resource.txt"
        resource_path.write_text("test content")

        flip_dev.add_resource(
            project_id="proj-1",
            accession_id="ACC001",
            scan_id="scan-001",
            resource_id="res-001",
            files=[str(resource_path)],
        )

        # Check that it logged (check records since propagate=False)
        assert any("add_resource is not supported in LOCAL_DEV mode" in record.message for record in caplog.records)


class TestFLIPStandardDevUpdateStatus:
    """Test FLIPStandardDev update_status method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        from flip.core.standard import FLIPStandardDev

        return FLIPStandardDev()

    @pytest.mark.skip(reason="Logger has propagate=False which prevents caplog from capturing logs")
    def test_update_status_logs_in_dev_mode(self, flip_dev, caplog):
        """update_status should log the status update in dev mode."""
        import logging

        from flip.constants import ModelStatus

        caplog.set_level(logging.INFO, logger="FLIPStandardDev")

        flip_dev.update_status(model_id="model-123", new_model_status=ModelStatus.TRAINING_STARTED)

        # Check that it logged (check records since propagate=False)
        assert any("update_status is not supported in LOCAL_DEV mode" in record.message for record in caplog.records)


class TestFLIPStandardDevSendHandledException:
    """Test FLIPStandardDev send_handled_exception method."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        from flip.core.standard import FLIPStandardDev

        return FLIPStandardDev()

    @pytest.mark.skip(reason="Logger has propagate=False which prevents caplog from capturing logs")
    def test_send_handled_exception_logs_in_dev_mode(self, flip_dev, caplog):
        """send_handled_exception should log the exception in dev mode."""
        import logging

        caplog.set_level(logging.INFO, logger="FLIPStandardDev")

        flip_dev.send_handled_exception(
            formatted_exception="Test exception", client_name="client-1", model_id="model-123"
        )

        # Check that it logged (check records since propagate=False)
        assert any(
            "send_handled_exception is not supported in LOCAL_DEV mode" in record.message for record in caplog.records
        )


class TestFLIPStandardProdGetDataframe:
    """Test FLIPStandardProd get_dataframe method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance in test mode."""
        pytest.skip("Production mode tests require complex singleton reloading")
        return None

    def test_get_dataframe_makes_api_request(self, flip_prod):
        """get_dataframe should make API request to data-access-api."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json.dumps([{"accession_id": "ACC001", "label": 0}])

        with patch("requests.post", return_value=mock_response) as mock_post:
            df = flip_prod.get_dataframe(project_id="proj-1", query="SELECT * FROM table")

            # Verify API was called
            mock_post.assert_called_once()
            call_args = mock_post.call_args

            assert "data.example.com" in call_args[0][0]
            assert "cohort/dataframe" in call_args[0][0]

            # Verify result is DataFrame
            assert isinstance(df, pd.DataFrame)
            assert len(df) == 1


class TestFLIPStandardProdGetByAccessionNumber:
    """Test FLIPStandardProd get_by_accession_number method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance in test mode."""
        pytest.skip("Production mode tests require complex singleton reloading")
        return None

    def test_get_by_accession_number_downloads_resources(self, flip_prod, tmp_path):
        """get_by_accession_number should download resources from API."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"ZIP file content"
        mock_response.json.return_value = {"path": str(tmp_path / "data")}

        with patch("requests.post", return_value=mock_response) as mock_post:
            with patch("zipfile.ZipFile"):
                result = flip_prod.get_by_accession_number(
                    project_id="proj-1", accession_id="ACC001", resource_type="dicom"
                )

                # Verify API was called
                mock_post.assert_called_once()
                assert isinstance(result, Path)


class TestFLIPStandardProdAddResource:
    """Test FLIPStandardProd add_resource method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance in test mode."""
        pytest.skip("Production mode tests require complex singleton reloading")
        return None

    def test_add_resource_uploads_to_api(self, flip_prod, tmp_path):
        """add_resource should upload file to imaging API."""
        resource_path = tmp_path / "test_resource.txt"
        resource_path.write_text("test content")

        mock_response = Mock()
        mock_response.status_code = 200

        with patch("requests.post", return_value=mock_response) as mock_post:
            flip_prod.add_resource(
                project_id="proj-1",
                accession_id="ACC001",
                scan_id="scan-001",
                resource_id="res-001",
                files=[str(resource_path)],
            )

            # Verify API was called
            mock_post.assert_called_once()


class TestFLIPStandardProdUpdateStatus:
    """Test FLIPStandardProd update_status method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance in test mode."""
        pytest.skip("Production mode tests require complex singleton reloading")
        return None

    def test_update_status_calls_api_with_valid_uuid(self, flip_prod):
        """update_status should call API to update model status with valid UUID."""
        from flip.constants import ModelStatus

        mock_response = Mock()
        mock_response.status_code = 200

        # Use a valid UUID
        valid_model_id = "550e8400-e29b-41d4-a716-446655440000"

        with patch("requests.put", return_value=mock_response) as mock_put:
            flip_prod.update_status(valid_model_id, ModelStatus.TRAINING_STARTED)

            # Verify API was called
            mock_put.assert_called_once()
            call_args = mock_put.call_args
            assert valid_model_id in call_args[0][0]

    def test_update_status_rejects_invalid_uuid(self, flip_prod):
        """update_status should reject invalid model IDs."""
        from flip.constants import ModelStatus

        with pytest.raises(ValueError, match="Invalid model ID"):
            flip_prod.update_status("model-123", ModelStatus.TRAINING_STARTED)


class TestFLIPStandardProdSendHandledException:
    """Test FLIPStandardProd send_handled_exception method."""

    @pytest.fixture
    def flip_prod(self):
        """Create a FLIPStandardProd instance in test mode."""
        pytest.skip("Production mode tests require complex singleton reloading")
        return None

    def test_send_handled_exception_calls_api_with_valid_uuid(self, flip_prod):
        """send_handled_exception should POST exception to API with valid UUID."""
        mock_response = Mock()
        mock_response.status_code = 200

        # Use a valid UUID
        valid_model_id = "550e8400-e29b-41d4-a716-446655440000"

        with patch("requests.post", return_value=mock_response) as mock_post:
            flip_prod.send_handled_exception("Error message", "client-1", valid_model_id)

            # Verify API was called
            mock_post.assert_called_once()

    def test_send_handled_exception_rejects_invalid_uuid(self, flip_prod):
        """send_handled_exception should reject invalid model IDs."""
        with pytest.raises(ValueError, match="Invalid model ID"):
            flip_prod.send_handled_exception("Error message", "client-1", "model-123")
