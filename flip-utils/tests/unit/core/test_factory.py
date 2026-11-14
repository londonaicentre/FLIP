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

    @pytest.mark.parametrize(
        "job_type",
        [JobType.STANDARD, JobType.EVALUATION, JobType.FED_OPT, "standard", "evaluation", "fed_opt"],
    )
    @patch("flip.core.factory.FlipConstants")
    def test_factory_creates_dev(self, mock_constants, job_type):
        """Factory should create FLIPStandardDev in dev mode for every job type, enum or string."""
        mock_constants.LOCAL_DEV = True

        assert isinstance(FLIP(job_type), FLIPStandardDev)

    @patch("flip.core.factory.FlipConstants")
    def test_factory_creates_prod_when_not_local_dev(self, mock_constants):
        """Factory should create FLIPStandardProd when LOCAL_DEV=false."""

        mock_constants.LOCAL_DEV = False

        result = FLIP(JobType.STANDARD)
        assert isinstance(result, FLIPStandardProd)

    # diffusion_model was a two-stage job type that is gone: naming it fails rather than running standard.
    @pytest.mark.parametrize(
        "job_type", ["invalid_job_type", "diffusion_model"], ids=["unknown", "removed-diffusion-model"]
    )
    def test_factory_rejects_unknown_job_type(self, job_type):
        """Factory should raise ValueError for an unknown job type string."""
        with pytest.raises(ValueError, match="Unknown job_type"):
            FLIP(job_type)
