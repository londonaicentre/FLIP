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

from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.main import app

client = TestClient(app)

test_model_id = uuid4()
test_user_id = uuid4()

# Stands in for the Elasticsearch base URL, which is a Secrets Manager value in every deployed
# environment. Asserting on a sentinel rather than on the exact message keeps the test about the
# property (the host must not reach the caller) rather than about the wording.
SECRET_ES_HOST = "vpc-flip-internal-es.eu-west-2.es.amazonaws.example"
SECRET_ES_URL = f"https://{SECRET_ES_HOST}:9200"

MODULE = "flip_api.model_services.retrieve_model_status_from_logs"


@pytest.fixture(autouse=True)
def override_dependencies():
    mock_session = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: test_user_id
    yield mock_session
    app.dependency_overrides.clear()


def _elastic_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Build the error httpx raises for a non-2xx, with the message httpx itself would construct.

    Raising it via ``raise_for_status()`` rather than instantiating it directly is load-bearing:
    ``HTTPStatusError`` carries whatever message it is given, so a hand-written one stringifies to
    just that string and the URL assertions below would pass against the leaky code too. Only
    ``raise_for_status`` builds the "... for url '<url>'" form that actually contains the secret.
    """
    request = httpx.Request("POST", f"{SECRET_ES_URL}/centralhub-eks/_search")
    response = httpx.Response(status_code=status_code, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc

    raise AssertionError(f"status {status_code} did not raise")  # pragma: no cover


@patch(f"{MODULE}.httpx.post")
@patch(f"{MODULE}.get_secret", return_value=SECRET_ES_URL)
@patch(f"{MODULE}.get_model_status")
@patch(f"{MODULE}.can_access_model", return_value=True)
def test_upstream_error_does_not_leak_the_elasticsearch_url(
    mock_can_access, mock_get_model_status, mock_get_secret, mock_post
):
    """A non-404 upstream failure must not put the internal Elasticsearch URL in the response.

    The endpoint is reachable by any authenticated user with access to the model, so anything in
    ``detail`` is disclosed to a non-privileged caller.
    """
    mock_get_model_status.return_value = MagicMock(deleted=False)
    mock_post.return_value.raise_for_status.side_effect = _elastic_status_error(500)

    response = client.get(f"/api/model/{test_model_id}/training/log")

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    detail = response.json()["detail"]
    assert SECRET_ES_HOST not in detail
    assert "centralhub-eks" not in detail
    assert detail == "Internal server error"


@patch(f"{MODULE}.httpx.post")
@patch(f"{MODULE}.get_secret", return_value=SECRET_ES_URL)
@patch(f"{MODULE}.get_model_status")
@patch(f"{MODULE}.can_access_model", return_value=True)
def test_upstream_404_still_reports_logs_not_found(
    mock_can_access, mock_get_model_status, mock_get_secret, mock_post
):
    """The 404 branch was already sanitised — keep it distinguishable from the 500 branch."""
    mock_get_model_status.return_value = MagicMock(deleted=False)
    mock_post.return_value.raise_for_status.side_effect = _elastic_status_error(404)

    response = client.get(f"/api/model/{test_model_id}/training/log")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Logs not found."


@patch(f"{MODULE}.can_access_model", return_value=False)
def test_access_denied_for_unrelated_model(mock_can_access):
    response = client.get(f"/api/model/{test_model_id}/training/log")

    assert response.status_code == status.HTTP_403_FORBIDDEN
