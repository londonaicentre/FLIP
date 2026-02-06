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

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

# Import from the new flip package
from flip.constants.flip_constants import (
    DevSettings,
    FlipEvents,
    FlipMetricsLabel,
    FlipTasks,
    ModelStatus,
    ProdSettings,
    ResourceType,
    _Common,
)


class TestDevSettings:
    """Test DevSettings Pydantic model."""

    def test_dev_settings_defaults(self):
        """DevSettings should have LOCAL_DEV=True by default."""
        with patch.dict(os.environ, {"DEV_DATAFRAME": "/test/df.csv", "DEV_IMAGES_DIR": "/test/images"}):
            settings = DevSettings()
            assert settings.LOCAL_DEV is True
            assert settings.MIN_CLIENTS == 1

    def test_dev_settings_requires_dataframe_path(self):
        """DevSettings should require DEV_DATAFRAME."""
        with patch.dict(os.environ, {"DEV_IMAGES_DIR": "/test/images"}, clear=True):
            # Remove LOCAL_DEV to avoid interference
            os.environ.pop("DEV_DATAFRAME", None)
            with pytest.raises(ValidationError):
                DevSettings()

    def test_dev_settings_requires_images_dir(self):
        """DevSettings should require DEV_IMAGES_DIR."""
        with patch.dict(os.environ, {"DEV_DATAFRAME": "/test/df.csv"}, clear=True):
            os.environ.pop("DEV_IMAGES_DIR", None)
            with pytest.raises(ValidationError):
                DevSettings()

    def test_dev_settings_accepts_custom_values(self):
        """DevSettings should accept custom values from environment."""
        env = {
            "DEV_DATAFRAME": "/custom/dataframe.csv",
            "DEV_IMAGES_DIR": "/custom/images",
            "MIN_CLIENTS": "3",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = DevSettings()
            assert settings.DEV_DATAFRAME == "/custom/dataframe.csv"
            assert settings.DEV_IMAGES_DIR == "/custom/images"
            assert settings.MIN_CLIENTS == 3


class TestProdSettings:
    """Test ProdSettings Pydantic model."""

    def get_valid_prod_env(self):
        """Return a dictionary of valid prod environment variables."""
        return {
            "LOCAL_DEV": "false",
            "CENTRAL_HUB_API_URL": "https://central-hub.example.com",
            "DATA_ACCESS_API_URL": "https://data-access.example.com",
            "IMAGING_API_URL": "https://imaging.example.com",
            "IMAGES_DIR": "/data/images",
            "PRIVATE_API_KEY_HEADER": "X-API-Key",
            "PRIVATE_API_KEY": "secret-key-123",
            "NET_ID": "net-1",
            "UPLOADED_FEDERATED_DATA_BUCKET": "s3://my-bucket",
        }

    def test_prod_settings_valid_config(self):
        """ProdSettings should accept valid configuration."""
        with patch.dict(os.environ, self.get_valid_prod_env(), clear=True):
            settings = ProdSettings()
            assert settings.LOCAL_DEV is False
            assert str(settings.CENTRAL_HUB_API_URL) == "https://central-hub.example.com/"
            assert settings.NET_ID == "net-1"

    def test_prod_settings_requires_central_hub_url(self):
        """ProdSettings should require CENTRAL_HUB_API_URL."""
        env = self.get_valid_prod_env()
        del env["CENTRAL_HUB_API_URL"]
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError):
                ProdSettings()

    def test_prod_settings_validates_http_urls(self):
        """ProdSettings should validate HTTP URLs."""
        env = self.get_valid_prod_env()
        env["CENTRAL_HUB_API_URL"] = "not-a-valid-url"
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError):
                ProdSettings()

    def test_prod_settings_validates_s3_bucket_url(self):
        """ProdSettings should validate S3 bucket URL starts with s3://."""
        env = self.get_valid_prod_env()
        env["UPLOADED_FEDERATED_DATA_BUCKET"] = "https://not-s3-bucket"
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError, match="Invalid S3 URL"):
                ProdSettings()

    def test_prod_settings_accepts_valid_s3_bucket(self):
        """ProdSettings should accept valid S3 bucket URL."""
        env = self.get_valid_prod_env()
        env["UPLOADED_FEDERATED_DATA_BUCKET"] = "s3://my-federated-bucket/path"
        with patch.dict(os.environ, env, clear=True):
            settings = ProdSettings()
            assert settings.UPLOADED_FEDERATED_DATA_BUCKET == "s3://my-federated-bucket/path"


class TestCommonSettings:
    """Test _Common base settings class."""

    def test_common_requires_local_dev(self):
        """_Common should require LOCAL_DEV to be set."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValidationError):
                _Common()

    def test_common_min_clients_default(self):
        """_Common should default MIN_CLIENTS to 1."""
        with patch.dict(os.environ, {"LOCAL_DEV": "true"}, clear=True):
            settings = _Common()
            assert settings.MIN_CLIENTS == 1

    def test_common_min_clients_must_be_positive(self):
        """_Common should reject non-positive MIN_CLIENTS."""
        with patch.dict(os.environ, {"LOCAL_DEV": "true", "MIN_CLIENTS": "0"}, clear=True):
            with pytest.raises(ValidationError):
                _Common()

        with patch.dict(os.environ, {"LOCAL_DEV": "true", "MIN_CLIENTS": "-1"}, clear=True):
            with pytest.raises(ValidationError):
                _Common()


class TestResourceType:
    """Test ResourceType enum."""

    def test_resource_type_values(self):
        """ResourceType enum should have expected values."""
        assert ResourceType.DICOM.value == "DICOM"
        assert ResourceType.NIFTI.value == "NIFTI"
        assert ResourceType.SEGMENTATION.value == "SEG"
        assert ResourceType.ALL.value == "ALL"

    def test_resource_type_is_string_enum(self):
        """ResourceType should be a string enum."""
        assert isinstance(ResourceType.DICOM, str)
        assert ResourceType.DICOM == "DICOM"

    def test_resource_type_all_members(self):
        """ResourceType should have exactly 4 members."""
        assert len(ResourceType) == 4
        assert set(ResourceType) == {
            ResourceType.DICOM,
            ResourceType.NIFTI,
            ResourceType.SEGMENTATION,
            ResourceType.ALL,
        }


class TestFlipTasks:
    """Test FlipTasks enum."""

    def test_flip_tasks_values(self):
        """FlipTasks enum should have expected values."""
        assert FlipTasks.INIT_TRAINING.value == "init_training"
        assert FlipTasks.POST_VALIDATION.value == "post_validation"
        assert FlipTasks.CLEANUP.value == "cleanup"

    def test_flip_tasks_is_string_enum(self):
        """FlipTasks should be a string enum."""
        assert isinstance(FlipTasks.INIT_TRAINING, str)
        assert FlipTasks.INIT_TRAINING == "init_training"

    def test_flip_tasks_all_members(self):
        """FlipTasks should have exactly 5 members (includes evaluation-specific tasks)."""
        assert len(FlipTasks) == 5


class TestFlipEvents:
    """Test FlipEvents class constants."""

    def test_flip_events_values(self):
        """FlipEvents should have expected constant values."""
        assert FlipEvents.TRAINING_INITIATED == "_training_initiated"
        assert FlipEvents.RESULTS_UPLOAD_STARTED == "_results_upload_started"
        assert FlipEvents.RESULTS_UPLOAD_COMPLETED == "_results_upload_completed"
        assert FlipEvents.SEND_RESULT == "_send_result"
        assert FlipEvents.LOG_EXCEPTION == "_log_exception"
        assert FlipEvents.ABORTED == "_aborted"

    def test_flip_events_are_strings(self):
        """FlipEvents constants should be strings."""
        assert isinstance(FlipEvents.TRAINING_INITIATED, str)
        assert isinstance(FlipEvents.SEND_RESULT, str)


class TestModelStatus:
    """Test ModelStatus enum."""

    def test_model_status_values(self):
        """ModelStatus enum should have expected values."""
        assert ModelStatus.PENDING.value == "PENDING"
        assert ModelStatus.INITIATED.value == "INITIATED"
        assert ModelStatus.PREPARED.value == "PREPARED"
        assert ModelStatus.TRAINING_STARTED.value == "TRAINING_STARTED"
        assert ModelStatus.RESULTS_UPLOADED.value == "RESULTS_UPLOADED"
        assert ModelStatus.ERROR.value == "ERROR"
        assert ModelStatus.STOPPED.value == "STOPPED"

    def test_model_status_is_string_enum(self):
        """ModelStatus should be a string enum."""
        assert isinstance(ModelStatus.PENDING, str)
        assert ModelStatus.PENDING == "PENDING"

    def test_model_status_all_members(self):
        """ModelStatus should have exactly 7 members."""
        assert len(ModelStatus) == 7


class TestFlipMetricsLabel:
    """Test FlipMetricsLabel enum."""

    def test_flip_metrics_label_values(self):
        """FlipMetricsLabel enum should have expected values."""
        assert FlipMetricsLabel.LOSS_FUNCTION.value == "LOSS_FUNCTION"
        assert FlipMetricsLabel.DL_RESULT.value == "DL_RESULT"
        assert FlipMetricsLabel.AVERAGE_SCORE.value == "AVERAGE_SCORE"

    def test_flip_metrics_label_is_string_enum(self):
        """FlipMetricsLabel should be a string enum."""
        assert isinstance(FlipMetricsLabel.LOSS_FUNCTION, str)
        assert FlipMetricsLabel.LOSS_FUNCTION == "LOSS_FUNCTION"

    def test_flip_metrics_label_all_members(self):
        """FlipMetricsLabel should have exactly 3 members."""
        assert len(FlipMetricsLabel) == 3
