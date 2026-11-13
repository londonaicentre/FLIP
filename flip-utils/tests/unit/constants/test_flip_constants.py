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

import os
from typing import get_type_hints
from unittest.mock import patch

import pytest
from pydantic import ValidationError

# Import from the new flip package
from flip.constants import flip_constants
from flip.constants.flip_constants import (
    DevSettings,
    FlipConstants,
    FlipEvents,
    FlipMetricsLabel,
    FlipTasks,
    ModelStatus,
    ProdSettings,
    ResourceType,
    _Common,
    _FlipConstantsProxy,
)


class TestDevSettings:
    """Test DevSettings Pydantic model."""

    def test_dev_settings_defaults(self):
        """DevSettings should have LOCAL_DEV=True by default."""
        with patch.dict(os.environ, {"DEV_DATAFRAME": "/test/df.csv", "DEV_IMAGES_DIR": "/test/images"}):
            settings = DevSettings()
            assert settings.LOCAL_DEV is True

    @pytest.mark.parametrize(
        ("missing", "present"),
        [("DEV_DATAFRAME", {"DEV_IMAGES_DIR": "/test/images"}), ("DEV_IMAGES_DIR", {"DEV_DATAFRAME": "/test/df.csv"})],
    )
    def test_dev_settings_defaults_a_missing_path_to_empty(self, missing, present):
        """DevSettings should use an empty string default for DEV_DATAFRAME / DEV_IMAGES_DIR if not provided."""
        with patch.dict(os.environ, present, clear=True):
            assert getattr(DevSettings(), missing) == ""

    def test_dev_settings_accepts_custom_values(self):
        """DevSettings should accept custom values from environment."""
        env = {
            "DEV_DATAFRAME": "/custom/dataframe.csv",
            "DEV_IMAGES_DIR": "/custom/images",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = DevSettings()
            assert settings.DEV_DATAFRAME == "/custom/dataframe.csv"
            assert settings.DEV_IMAGES_DIR == "/custom/images"


class TestProdSettings:
    """Test ProdSettings Pydantic model."""

    def get_valid_prod_env(self):
        """Return a dictionary of valid prod environment variables."""
        return {
            "LOCAL_DEV": "false",
            "FLIP_API_INTERNAL_URL": "https://central-hub.example.com",
            "DATA_ACCESS_API_URL": "https://data-access.example.com",
            "IMAGING_API_URL": "https://imaging.example.com",
            "IMAGES_DIR": "/data/images",
            "NET_ID": "net-1",
        }

    def test_prod_settings_valid_config(self):
        """ProdSettings should accept valid configuration."""
        with patch.dict(os.environ, self.get_valid_prod_env(), clear=True):
            settings = ProdSettings()
            assert settings.LOCAL_DEV is False
            assert str(settings.FLIP_API_INTERNAL_URL) == "https://central-hub.example.com/"
            assert settings.NET_ID == "net-1"

    def test_prod_settings_uses_default_flip_api_internal_url(self):
        """ProdSettings should use default for FLIP_API_INTERNAL_URL if not provided."""
        env = self.get_valid_prod_env()
        del env["FLIP_API_INTERNAL_URL"]
        with patch.dict(os.environ, env, clear=True):
            settings = ProdSettings()
            assert str(settings.FLIP_API_INTERNAL_URL) == "http://localhost:8000/"

    def test_prod_settings_validates_http_urls(self):
        """ProdSettings should validate HTTP URLs."""
        env = self.get_valid_prod_env()
        env["FLIP_API_INTERNAL_URL"] = "not-a-valid-url"
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError):
                ProdSettings()

    def test_prod_settings_server_checkpoint_root_default(self):
        """SERVER_CHECKPOINT_ROOT defaults to the shared-volume mount path the fl-server reads."""
        with patch.dict(os.environ, self.get_valid_prod_env(), clear=True):
            settings = ProdSettings()
            assert settings.SERVER_CHECKPOINT_ROOT == "/app/server-checkpoints"

    def test_prod_settings_server_checkpoint_root_override(self):
        """SERVER_CHECKPOINT_ROOT is overridable via environment."""
        env = self.get_valid_prod_env()
        env["SERVER_CHECKPOINT_ROOT"] = "/custom/checkpoints"
        with patch.dict(os.environ, env, clear=True):
            settings = ProdSettings()
            assert settings.SERVER_CHECKPOINT_ROOT == "/custom/checkpoints"


class TestCommonSettings:
    """Test _Common base settings class."""

    def test_common_uses_default_local_dev(self):
        """_Common should default LOCAL_DEV to True if not set (dev mode by default)."""
        # Explicitly test with minimal environment
        with patch.dict(os.environ, {}, clear=True):  # Don't include LOCAL_DEV to test default
            settings = _Common()
            assert settings.LOCAL_DEV is True


class TestFlipConstantsProxy:
    """Test the lazily-forwarding FlipConstants proxy."""

    def test_annotations_mirror_the_settings_fields(self):
        """The proxy declares exactly the union of the settings fields, each with the field's own type.

        The annotations are what mypy sees for ``FlipConstants.<NAME>``; this pins them to the
        settings classes so adding or retyping a field cannot leave the proxy stale.
        """
        expected = {
            name: field.annotation
            for settings_cls in (DevSettings, ProdSettings)
            for name, field in settings_cls.model_fields.items()
        }
        assert get_type_hints(_FlipConstantsProxy) == expected

    def test_forwards_the_active_settings_only(self):
        """Reads resolve on the active settings instance; the other environment's fields are absent."""
        env = {"LOCAL_DEV": "true", "DEV_DATAFRAME": "/data/df.csv", "DEV_IMAGES_DIR": "/data/images"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(flip_constants, "_flip_constants_instance", DevSettings()),
        ):
            assert FlipConstants.LOCAL_DEV is True
            assert FlipConstants.DEV_DATAFRAME == "/data/df.csv"
            with pytest.raises(AttributeError):
                _ = FlipConstants.NET_ID


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


# A 4-member enum whose 4 names all resolve has exactly those members (the old set-equality check).
@pytest.mark.parametrize(
    ("enum", "values", "count"),
    [
        (ResourceType, {"DICOM": "DICOM", "NIFTI": "NIFTI", "SEGMENTATION": "SEG", "ALL": "ALL"}, 4),
        # 5 members: includes the evaluation-specific tasks
        (FlipTasks, {"INIT_TRAINING": "init_training", "POST_VALIDATION": "post_validation", "CLEANUP": "cleanup"}, 5),
        (
            ModelStatus,
            {
                "PENDING": "PENDING",
                "INITIATED": "INITIATED",
                "PREPARED": "PREPARED",
                "RUNNING": "RUNNING",
                "RESULTS_UPLOADED": "RESULTS_UPLOADED",
                "ERROR": "ERROR",
                "STOPPED": "STOPPED",
                "RESULTS_UPLOAD_FAILED": "RESULTS_UPLOAD_FAILED",
            },
            8,
        ),
        (
            FlipMetricsLabel,
            {"LOSS_FUNCTION": "LOSS_FUNCTION", "DL_RESULT": "DL_RESULT", "AVERAGE_SCORE": "AVERAGE_SCORE"},
            3,
        ),
    ],
    ids=["ResourceType", "FlipTasks", "ModelStatus", "FlipMetricsLabel"],
)
def test_string_enum_values_and_members(enum, values, count):
    """Each string enum has the expected values and member count, and its members are strings."""
    assert len(enum) == count
    for name, value in values.items():
        assert enum[name].value == value
        assert isinstance(enum[name], str)
        assert enum[name] == value
