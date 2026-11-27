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
"""Which XNAT identity each route holds (FLIP#2011).

The download and upload routes run on the data account, a Member of FLIP projects; every other route runs on the
provisioning account, a site administrator. The first two tests read that off the app's dependency graph, so a new
route, or a refactor that moves a dependency, cannot quietly cross over. The last two drive the routes and look at
the session XNAT is actually sent.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.routing import APIRoute

from imaging_api.main import app
from imaging_api.utils.auth import get_xnat_auth_headers, get_xnat_data_auth_headers
from imaging_api.utils.encryption import PROJECT_ID_CONTEXT, encrypt

DATA_PREFIXES = ("/download/", "/upload/")
PROJECT_ID = "6d3c7c9e-7a51-4b1a-9c2e-0d7f2f0f6a11"


def _calls(dependant) -> set:
    found = {dependant.call}
    for sub in dependant.dependencies:
        found |= _calls(sub)
    return found


def _routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


@pytest.mark.parametrize("route", [r for r in _routes() if r.path.startswith(DATA_PREFIXES)], ids=lambda r: r.path)
def test_data_routes_hold_only_the_data_account(route):
    calls = _calls(route.dependant)
    assert get_xnat_data_auth_headers in calls
    assert get_xnat_auth_headers not in calls


@pytest.mark.parametrize("route", [r for r in _routes() if not r.path.startswith(DATA_PREFIXES)], ids=lambda r: r.path)
def test_no_other_route_holds_the_data_account(route):
    assert get_xnat_data_auth_headers not in _calls(route.dependant)


def test_the_data_routes_are_the_ones_the_issue_names():
    """If a data route is added or renamed, the two lists above must be looked at again."""
    paths = sorted({r.path for r in _routes() if r.path.startswith(DATA_PREFIXES)})
    assert paths == [
        "/download/images/{net_id}",
        "/download/images/{net_id}/layout",
        "/download/images/{net_id}/stream",
        "/upload/images/{net_id}",
        "/upload/images/{net_id}/files",
    ]


def _sealed() -> str:
    return encrypt(PROJECT_ID, context=PROJECT_ID_CONTEXT)


def _xnat_response(json_body) -> MagicMock:
    response = MagicMock(status_code=200, text="")
    response.json.return_value = json_body
    return response


def test_a_download_sends_xnat_only_the_data_session(client):
    """Every XNAT request the route makes carries the data account's cookie, none the administrator's."""
    sessions = []

    def record(url, headers, **_):
        sessions.append(headers["Cookie"])
        raise RuntimeError("stop after the first XNAT call")

    with (
        patch("imaging_api.routers.download.is_served", return_value=True),
        patch("imaging_api.services.projects.requests.get", side_effect=record),
        patch("imaging_api.services.download.requests.get", side_effect=record),
    ):
        response = client.post(
            "/download/images/net-1",
            json={"encrypted_central_hub_project_id": _sealed(), "accession_id": "ACC1"},
        )
    assert response.status_code >= 400
    assert sessions
    assert set(sessions) == {"JSESSIONID=data"}


def test_an_upload_sends_xnat_only_the_data_session(client):
    import os

    from imaging_api.services.upload import BASE_IMAGES_DOWNLOAD_DIR

    upload_dir = os.path.join(BASE_IMAGES_DOWNLOAD_DIR, "net-1", "upload")
    os.makedirs(upload_dir, exist_ok=True)
    with open(os.path.join(upload_dir, "out.nii.gz"), "wb") as fh:
        fh.write(b"result")
    sessions = []

    def record(url, headers, **_):
        sessions.append(headers["Cookie"])
        raise RuntimeError("stop after the first XNAT call")

    with (
        patch("imaging_api.services.projects.requests.get", side_effect=record),
        patch("imaging_api.services.upload.requests.get", side_effect=record),
        patch("imaging_api.services.upload.requests.put", side_effect=record),
    ):
        response = client.put(
            "/upload/images/net-1",
            json={
                "encrypted_central_hub_project_id": _sealed(),
                "accession_id": "ACC1",
                "scan_id": "1",
                "resource_id": "RESULTS",
                "files": ["out.nii.gz"],
            },
        )
    assert response.status_code >= 400
    assert sessions
    assert set(sessions) == {"JSESSIONID=data"}
