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

from flip.core.factory import FLIP
from flip.core.standard import FLIPStandardDev, FLIPStandardProd


class TestFLIPFactory:
    """Test the FLIP factory function.

    The implementation is selected by environment alone. ``job_type`` was removed because it
    discriminated nothing — every branch returned the same object — and the set of valid job types
    is defined by the per-backend manifest, not by this package.
    """

    @patch("flip.core.factory.FlipConstants")
    def test_factory_creates_dev_under_local_dev(self, mock_constants):
        """Factory should create FLIPStandardDev when LOCAL_DEV=true."""
        mock_constants.LOCAL_DEV = True

        assert isinstance(FLIP(), FLIPStandardDev)

    @patch("flip.core.factory.FlipConstants")
    def test_factory_creates_prod_when_not_local_dev(self, mock_constants):
        """Factory should create FLIPStandardProd when LOCAL_DEV=false."""
        mock_constants.LOCAL_DEV = False

        assert isinstance(FLIP(), FLIPStandardProd)

    def test_factory_takes_no_arguments(self):
        """job_type is gone rather than ignored, so passing it fails loudly and names FLIP()."""
        with pytest.raises(TypeError, match="job_type"):
            FLIP(job_type="standard")  # type: ignore[call-arg]
