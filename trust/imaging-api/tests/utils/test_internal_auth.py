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

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from imaging_api.config import get_settings
from imaging_api.main import app
from imaging_api.utils.internal_auth import authenticate_internal_service

VALID_KEY = "trust-internal-key-abc123"
HEADER_NAME = get_settings().TRUST_INTERNAL_SERVICE_KEY_HEADER


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
    """Fail-closed: even with a valid-looking key, a missing configured key blocks access."""
    with _patch_key(""):
        with pytest.raises(HTTPException) as exc_info:
            authenticate_internal_service(api_key=VALID_KEY)
        assert exc_info.value.status_code == 401
        assert "not configured" in exc_info.value.detail.lower()


# ---- End-to-end: routers reject unauthenticated requests; /health stays open ----


def test_health_endpoint_does_not_require_auth():
    """/health must remain reachable for Docker healthchecks."""
    client = TestClient(app)
    with _patch_key(VALID_KEY):
        resp = client.get("/health")
    assert resp.status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/projects/"),
        ("GET", "/users"),
        ("GET", "/imaging/ping_pacs/1"),
        ("GET", "/retrieval/import_status_count/abc?encoded_query=eA=="),
    ],
)
def test_protected_endpoints_reject_missing_header(method, path):
    """Routers must 401 when no header is sent — even before any handler logic runs."""
    client = TestClient(app)
    with _patch_key(VALID_KEY):
        resp = client.request(method, path)
    assert resp.status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/projects/"),
        ("GET", "/users"),
    ],
)
def test_protected_endpoints_reject_wrong_key(method, path):
    client = TestClient(app)
    with _patch_key(VALID_KEY):
        resp = client.request(method, path, headers={HEADER_NAME: "wrong-key"})
    assert resp.status_code == 401
