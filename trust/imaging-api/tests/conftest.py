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

# Set dummy environment variables required by Settings() before any app code
# is imported.  These are only used in tests; real values come from Docker
# Compose environment or .env files in deployed environments.
_TEST_ENV_DEFAULTS = {
    "PACS_ID": "1",
    "XNAT_URL": "http://localhost:8080",
    "XNAT_SERVICE_USER": "test_user",
    "XNAT_SERVICE_PASSWORD": "test_password",
    "XNAT_DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/xnat",
    "DATA_ACCESS_API_URL": "http://localhost:8001",
    "AES_KEY_BASE64": "QgZ+TBA0lUxcuCiRPLneFe/JjMaUEUJWHACHHGz2gGA=",  # 32-byte key, base64
    "XNAT_PORT": "8080",
    "BASE_IMAGES_DOWNLOAD_DIR": "/tmp/flip-test-images",
}

for key, value in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(key, value)
