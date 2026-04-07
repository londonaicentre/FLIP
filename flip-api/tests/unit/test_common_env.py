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
from pathlib import Path


class TestCommonEnvFile:
    """Verify that the common .env.development.example file is loaded for unit tests.

    See https://github.com/londonaicentre/FLIP/issues/231
    """

    _COMMON_ENV_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".env.development.example"

    def test_common_env_file_exists(self) -> None:
        """The source-controlled env example file must exist."""
        assert self._COMMON_ENV_FILE.is_file(), f"Missing {self._COMMON_ENV_FILE}"

    def test_values_from_common_env_file_are_loaded(self) -> None:
        """Non-placeholder values from .env.development.example should be present."""
        # POSTGRES_DB is set to 'centralhub' in .env.development.example and is not
        # overridden by _TEST_ENV_DEFAULTS, so it must come from the common env file.
        assert os.environ.get("POSTGRES_DB") == "centralhub"

    def test_placeholder_values_are_overridden_by_test_defaults(self) -> None:
        """Placeholder values (e.g. <your-...>) must be replaced by test defaults."""
        assert os.environ.get("AES_KEY_BASE64") == "QgZ+TBA0lUxcuCiRPLneFe/JjMaUEUJWHACHHGz2gGA="
        assert os.environ.get("PRIVATE_API_KEY") == "test-key"
        assert os.environ.get("POSTGRES_PASSWORD") == "test_password"
        assert os.environ.get("AWS_SES_ADMIN_EMAIL_ADDRESS") == "test@example.com"
