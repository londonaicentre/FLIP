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

import atexit
import os
import shutil
import tempfile

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
}

for key, value in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(key, value)

# BASE_IMAGES_DOWNLOAD_DIR must point at a writable directory: download_and_unzip_images
# now calls os.makedirs() on it before any mocks can intercept. The service Makefile
# injects /tmp/images for `local_test`, which on hosts where an earlier docker-as-root
# run created /tmp/images is root-owned and refuses writes from the test process. Force
# a fresh per-session tempdir so make-driven and bare-pytest runs both work regardless
# of host state.
_test_images_dir = tempfile.mkdtemp(prefix="flip-test-images-")
atexit.register(shutil.rmtree, _test_images_dir, ignore_errors=True)
os.environ["BASE_IMAGES_DOWNLOAD_DIR"] = _test_images_dir
