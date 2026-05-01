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

"""Unit tests for the trust-internal service auth dependency.

End-to-end coverage on routers lives in tests/routers/test_cohort.py
(parametrised missing-key / wrong-key / valid-key cases on /cohort,
/cohort/dataframe, /cohort/accession-ids, plus a /health regression).
This file covers the auth function directly, including the fail-closed
branch when TRUST_INTERNAL_SERVICE_KEY is unconfigured — that branch is
unreachable through the router tests because the test conftest always
provisions a key before the app imports.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from data_access_api.config import get_settings
from data_access_api.utils.internal_auth import authenticate_internal_service

VALID_KEY = "trust-internal-key-abc123"


def _patch_key(value: str):
    """Patch the configured key without rebuilding Settings."""
    return patch.object(get_settings(), "TRUST_INTERNAL_SERVICE_KEY", value)


def test_valid_key_passes():
    with _patch_key(VALID_KEY):
        # No exception means success
        authenticate_internal_service(api_key=VALID_KEY)


def test_missing_key_rejected():
    with _patch_key(VALID_KEY):
        with pytest.raises(HTTPException) as exc_info:
            authenticate_internal_service(api_key=None)
        assert exc_info.value.status_code == 401
        assert "missing" in exc_info.value.detail.lower()


def test_empty_key_rejected():
    with _patch_key(VALID_KEY):
        with pytest.raises(HTTPException) as exc_info:
            authenticate_internal_service(api_key="")
        assert exc_info.value.status_code == 401
        assert "missing" in exc_info.value.detail.lower()


def test_invalid_key_rejected():
    with _patch_key(VALID_KEY):
        with pytest.raises(HTTPException) as exc_info:
            authenticate_internal_service(api_key="wrong-key")
        assert exc_info.value.status_code == 401
        assert "invalid" in exc_info.value.detail.lower()


def test_unconfigured_key_rejects_all_callers():
    """Fail-closed: even with a valid-looking key, a missing configured key blocks access.

    This is the operator-misconfiguration path — if the data-access-api container
    boots without TRUST_INTERNAL_SERVICE_KEY in its env, every /cohort call must
    return 401 instead of silently authorising any caller.
    """
    with _patch_key(""):
        with pytest.raises(HTTPException) as exc_info:
            authenticate_internal_service(api_key=VALID_KEY)
        assert exc_info.value.status_code == 401
        assert "not configured" in exc_info.value.detail.lower()
