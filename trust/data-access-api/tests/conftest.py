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

import hashlib
import os

# Plaintext test key used by tests to authenticate against the /cohort router.
# Provisioned via TRUST_INTERNAL_SERVICE_KEY below so Settings() picks it up at
# app-import time.
TEST_TRUST_INTERNAL_SERVICE_KEY = "test-trust-internal-key"
AUTH_HEADERS = {"X-Trust-Internal-Service-Key": TEST_TRUST_INTERNAL_SERVICE_KEY}

# 32-byte AES key (base64), single source for both the env default below and the
# cohort-admin proof the snapshot WRITE routes require (FLIP#857).
TEST_AES_KEY_BASE64 = "QgZ+TBA0lUxcuCiRPLneFe/JjMaUEUJWHACHHGz2gGA="  # pragma: allowlist secret

# Cohort-admin authorisation for the snapshot create/delete routes: proof of possessing
# AES_KEY_BASE64, sent as the SHA-256 of the key. The read routes never need this header.
COHORT_ADMIN_PROOF = hashlib.sha256(TEST_AES_KEY_BASE64.encode()).hexdigest()
COHORT_ADMIN_HEADERS = {"X-Cohort-Admin-Key": COHORT_ADMIN_PROOF}
# The write routes require BOTH the trust-internal key AND the cohort-admin proof.
WRITE_AUTH_HEADERS = {**AUTH_HEADERS, **COHORT_ADMIN_HEADERS}

# Set dummy environment variables required by Settings() before any app code
# is imported.  These are only used in tests; real values come from Docker
# Compose environment or .env files in deployed environments.
_TEST_ENV_DEFAULTS = {
    "DATA_ACCESS_POSTGRES_USER": "test_user",
    "DATA_ACCESS_POSTGRES_PASSWORD": "test_password",
    "OMOP_POSTGRES_DB": "test_omop_db",
    "AES_KEY_BASE64": TEST_AES_KEY_BASE64,
    "TRUST_INTERNAL_SERVICE_KEY": TEST_TRUST_INTERNAL_SERVICE_KEY,
}

for key, value in _TEST_ENV_DEFAULTS.items():
    # Treat unset *and* unreplaced `<placeholder>` values from .env.development.example
    # as missing — otherwise the placeholder leaks through and base64-decoding the
    # AES key fails with "Incorrect padding" mid-test.
    current = os.environ.get(key, "")
    if not current or (current.startswith("<") and current.endswith(">")):
        os.environ[key] = value
