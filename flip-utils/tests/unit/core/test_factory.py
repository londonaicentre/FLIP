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

"""Tests for flip.core.factory module."""

from unittest.mock import patch

import pytest

from flip.constants import JobType
from flip.core.factory import FLIP
from flip.core.standard import FLIPStandardDev, FLIPStandardProd


class TestFLIPFactory:
    """Test the FLIP factory function."""

    @patch("flip.core.factory.FlipConstants")
    def test_factory_creates_standard_dev(self, mock_constants):
        """Factory should create FLIPStandardDev for standard job type in dev mode."""
        mock_constants.LOCAL_DEV = True

        result = FLIP(JobType.STANDARD)
        assert isinstance(result, FLIPStandardDev)

    @patch("flip.core.factory.FlipConstants")
    def test_factory_creates_evaluation_dev(self, mock_constants):
        """Factory should create FLIPStandardDev for evaluation job type."""
        mock_constants.LOCAL_DEV = True

        result = FLIP(JobType.EVALUATION)
        assert isinstance(result, FLIPStandardDev)

    @patch("flip.core.factory.FlipConstants")
    def test_factory_creates_fed_opt_dev(self, mock_constants):
        """Factory should create FLIPStandardDev for fed_opt job type."""
        mock_constants.LOCAL_DEV = True

        result = FLIP(JobType.FED_OPT)
        assert isinstance(result, FLIPStandardDev)

    @patch("flip.core.factory.FlipConstants")
    def test_factory_creates_diffusion_dev(self, mock_constants):
        """Factory should create FLIPStandardDev for diffusion_model job type."""
        mock_constants.LOCAL_DEV = True

        result = FLIP(JobType.DIFFUSION)
        assert isinstance(result, FLIPStandardDev)

    @patch("flip.core.factory.FlipConstants")
    def test_factory_creates_prod_when_not_local_dev(self, mock_constants):
        """Factory should create FLIPStandardProd when LOCAL_DEV=false."""

        mock_constants.LOCAL_DEV = False

        result = FLIP(JobType.STANDARD)
        assert isinstance(result, FLIPStandardProd)

    def test_factory_rejects_invalid_job_type(self):
        """Factory should raise ValueError for invalid job type string."""
        with pytest.raises(ValueError, match="Unknown job_type"):
            FLIP("invalid_job_type")

    @patch("flip.core.factory.FlipConstants")
    def test_factory_accepts_string_job_types(self, mock_constants):
        """Factory should accept job type as string."""

        mock_constants.LOCAL_DEV = True

        result = FLIP("standard")
        assert isinstance(result, FLIPStandardDev)

        result = FLIP("evaluation")
        assert isinstance(result, FLIPStandardDev)

        result = FLIP("fed_opt")
        assert isinstance(result, FLIPStandardDev)

        result = FLIP("diffusion_model")
        assert isinstance(result, FLIPStandardDev)
