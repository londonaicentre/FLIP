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

"""Tests for the flip package."""

import importlib
from unittest.mock import patch

import pandas as pd
import pytest

import flip.constants.flip_constants
from flip import FLIP, FLIPBase
from flip.constants import FlipConstants, JobType, ModelStatus, PTConstants, ResourceType
from flip.core.standard import FLIPStandardDev
from flip.utils import Utils


class TestFlipPackageImports:
    """Test that the flip package imports work correctly."""

    def test_import_flip_module(self):
        """Should be able to import from flip module."""
        assert FLIP is not None
        assert FLIPBase is not None

    def test_import_constants(self):
        """Should be able to import constants."""
        assert FlipConstants is not None
        assert ResourceType.NIFTI.value == "NIFTI"
        assert ModelStatus.PENDING.value == "PENDING"
        assert JobType.STANDARD.value == "standard"

    def test_import_utils(self):
        """Should be able to import utils."""
        assert Utils.is_valid_uuid("123e4567-e89b-12d3-a456-426614174000") is True


class TestFlipFactory:
    """Test the FLIP factory function."""

    def test_factory_creates_dev_instance(self):
        """Factory should create dev instance when LOCAL_DEV=true."""
        # In test environment, LOCAL_DEV should be true
        flip = FLIP()
        assert isinstance(flip, FLIPStandardDev)

    def test_factory_accepts_string_job_type(self):
        """Factory should accept string job types."""
        flip = FLIP(job_type="standard")
        assert flip is not None

        flip = FLIP(job_type="evaluation")
        assert flip is not None

        flip = FLIP(job_type="fed_opt")
        assert flip is not None

    def test_factory_accepts_enum_job_type(self):
        """Factory should accept JobType enum."""
        flip = FLIP(job_type=JobType.STANDARD)
        assert flip is not None

        flip = FLIP(job_type=JobType.EVALUATION)
        assert flip is not None

    def test_factory_raises_on_invalid_job_type(self):
        """Factory should raise ValueError for invalid job types."""
        with pytest.raises(ValueError, match="Unknown job_type"):
            FLIP(job_type="invalid_type")


class TestJobTypeEnum:
    """Test the JobType enum."""

    def test_job_type_values(self):
        """JobType enum should have expected values."""
        assert JobType.STANDARD.value == "standard"
        assert JobType.EVALUATION.value == "evaluation"
        assert JobType.FED_OPT.value == "fed_opt"
        assert JobType.DIFFUSION.value == "diffusion_model"

    def test_job_type_all_members(self):
        """JobType should have exactly 4 members."""
        assert len(JobType) == 4


class TestFLIPStandardDev:
    """Test FLIPStandardDev class."""

    @pytest.fixture
    def flip_dev(self):
        """Create a FLIPStandardDev instance."""
        return FLIPStandardDev()

    @pytest.mark.skip(reason="FlipConstants is a singleton that cannot be easily reloaded in tests")
    def test_get_dataframe_success(self, flip_dev, tmp_path):
        """get_dataframe should return DataFrame from CSV file."""
        # Create a test CSV file
        csv_path = tmp_path / "test_dataframe.csv"
        test_data = pd.DataFrame({"accession_id": ["ACC001", "ACC002"], "label": [0, 1]})
        test_data.to_csv(csv_path, index=False)

        # Need to reload constants to pick up new DEV_DATAFRAME value
        with patch.dict("os.environ", {"DEV_DATAFRAME": str(csv_path)}):
            importlib.reload(flip.constants.flip_constants)

            flip_dev_new = FLIPStandardDev()
            result = flip_dev_new.get_dataframe("project-id", "SELECT *")

        assert isinstance(result, pd.DataFrame)
        assert "accession_id" in result.columns
        assert len(result) == 2

    def test_validation_methods(self, flip_dev):
        """Validation methods should work correctly."""
        # check_query
        flip_dev.check_query("SELECT *")
        with pytest.raises(TypeError):
            flip_dev.check_query(123)

        # check_project_id
        flip_dev.check_project_id("project-123")
        with pytest.raises(TypeError):
            flip_dev.check_project_id(123)

        # check_resource_type
        result = flip_dev.check_resource_type(ResourceType.NIFTI)
        assert result == [ResourceType.NIFTI]


class TestPTConstants:
    """Test PTConstants from the flip package."""

    def test_pt_constants_values(self):
        """PTConstants should have expected values."""
        assert PTConstants.PTServerName == "server"
        assert PTConstants.PTFileModelName == "FL_global_model.pt"
        assert PTConstants.PTLocalModelName == "local_model.pt"
        assert PTConstants.PTLocalModelName == "local_model.pt"
        assert PTConstants.PTLocalModelName == "local_model.pt"
