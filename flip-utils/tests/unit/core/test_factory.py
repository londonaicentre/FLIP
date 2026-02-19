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

"""Tests for flip.core.factory module."""

from unittest.mock import patch

import pytest


class TestFLIPFactory:
    """Test the FLIP factory function."""

    @patch.dict("os.environ", {"LOCAL_DEV": "true"})
    def test_factory_creates_standard_dev(self):
        """Factory should create FLIPStandardDev for standard job type in dev mode."""
        import importlib

        import flip.constants.flip_constants

        importlib.reload(flip.constants.flip_constants)

        from flip.constants import JobType
        from flip.core.factory import FLIP
        from flip.core.standard import FLIPStandardDev

        result = FLIP(JobType.STANDARD)
        assert isinstance(result, FLIPStandardDev)

    @patch.dict("os.environ", {"LOCAL_DEV": "true"})
    def test_factory_creates_evaluation_dev(self):
        """Factory should create FLIPStandardDev for evaluation job type."""
        import importlib

        import flip.constants.flip_constants

        importlib.reload(flip.constants.flip_constants)

        from flip.constants import JobType
        from flip.core.factory import FLIP
        from flip.core.standard import FLIPStandardDev

        result = FLIP(JobType.EVALUATION)
        assert isinstance(result, FLIPStandardDev)

    @patch.dict("os.environ", {"LOCAL_DEV": "true"})
    def test_factory_creates_fed_opt_dev(self):
        """Factory should create FLIPStandardDev for fed_opt job type."""
        import importlib

        import flip.constants.flip_constants

        importlib.reload(flip.constants.flip_constants)

        from flip.constants import JobType
        from flip.core.factory import FLIP
        from flip.core.standard import FLIPStandardDev

        result = FLIP(JobType.FED_OPT)
        assert isinstance(result, FLIPStandardDev)

    @patch.dict("os.environ", {"LOCAL_DEV": "true"})
    def test_factory_creates_diffusion_dev(self):
        """Factory should create FLIPStandardDev for diffusion_model job type."""
        import importlib

        import flip.constants.flip_constants

        importlib.reload(flip.constants.flip_constants)

        from flip.constants import JobType
        from flip.core.factory import FLIP
        from flip.core.standard import FLIPStandardDev

        result = FLIP(JobType.DIFFUSION)
        assert isinstance(result, FLIPStandardDev)

    @pytest.mark.skip(reason="Production mode tests require complex singleton reloading")
    @patch.dict(
        "os.environ",
        {
            "LOCAL_DEV": "false",
            "CENTRAL_HUB_API_URL": "https://hub.example.com",
            "DATA_ACCESS_API_URL": "https://data.example.com",
            "IMAGING_API_URL": "https://imaging.example.com",
            "IMAGES_DIR": "/images",
            "PRIVATE_API_KEY_HEADER": "x-api-key",
            "PRIVATE_API_KEY": "test-key",
            "NET_ID": "net-1",
            "UPLOADED_FEDERATED_DATA_BUCKET": "s3://bucket",
            "MIN_CLIENTS": "1",
        },
    )
    def test_factory_creates_prod_when_not_local_dev(self):
        """Factory should create FLIPStandardProd when LOCAL_DEV=false."""
        import importlib

        import flip.constants.flip_constants

        importlib.reload(flip.constants.flip_constants)

        from flip.constants import JobType
        from flip.core.factory import FLIP
        from flip.core.standard import FLIPStandardProd

        result = FLIP(JobType.STANDARD)
        assert isinstance(result, FLIPStandardProd)

        # Reset to dev mode
        with patch.dict("os.environ", {"LOCAL_DEV": "true"}):
            importlib.reload(flip.constants.flip_constants)

    def test_factory_rejects_invalid_job_type(self):
        """Factory should raise ValueError for invalid job type string."""
        from flip.core.factory import FLIP

        with pytest.raises(ValueError, match="Unknown job_type"):
            FLIP("invalid_job_type")

    @patch.dict("os.environ", {"LOCAL_DEV": "true"})
    def test_factory_accepts_string_job_types(self):
        """Factory should accept job type as string."""
        import importlib

        import flip.constants.flip_constants

        importlib.reload(flip.constants.flip_constants)

        from flip.core.factory import FLIP
        from flip.core.standard import FLIPStandardDev

        result = FLIP("standard")
        assert isinstance(result, FLIPStandardDev)

        result = FLIP("evaluation")
        assert isinstance(result, FLIPStandardDev)

        result = FLIP("fed_opt")
        assert isinstance(result, FLIPStandardDev)

        result = FLIP("diffusion_model")
        assert isinstance(result, FLIPStandardDev)
