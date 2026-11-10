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

"""The service-key guard in front of every route but /health (fl_api/utils/auth.py)."""

import logging

from fl_api.utils import auth

PROTECTED = "/check_server_status"  # cheap: answers STOPPED with no SuperLink configured
OPEN_PATHS = ("/health",)
UUID = "00000000-0000-0000-0000-000000000000"


def test_right_key_passes(client):
    assert client.get(PROTECTED).status_code == 200


def test_missing_header_is_401(client):
    client.headers.pop("X-FL-API-Key")
    response = client.get(PROTECTED)
    assert response.status_code == 401
    assert "missing" in response.json()["detail"]


def test_wrong_key_is_401(client):
    client.headers["X-FL-API-Key"] = "not-the-key"
    assert client.get(PROTECTED).status_code == 401


def test_unconfigured_key_fails_closed(client, monkeypatch):
    """No key on the server refuses every caller, the right-looking one included."""
    monkeypatch.delenv("FL_API_KEY")
    response = client.get(PROTECTED)
    assert response.status_code == 401
    assert "not configured" in response.json()["detail"]


def test_every_route_but_health_is_guarded(client):
    """A route added without the guard is the whole finding again, so enumerate the app."""
    client.headers.pop("X-FL-API-Key")
    for route in client.app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if not methods or path in OPEN_PATHS or path.startswith(("/docs", "/redoc", "/openapi")):
            continue
        method = next(iter(methods - {"HEAD", "OPTIONS"}), None)
        if method is None:
            continue
        probe = path.replace("{job_folder}", UUID).replace("{model_id}", UUID)
        probe = probe.replace("{", "").replace("}", "")  # any other segment: a plain word is enough to route
        assert client.request(method, probe).status_code == 401, f"{method} {path} answered without the key"


def test_health_needs_no_key(client):
    client.headers.pop("X-FL-API-Key")
    for path in OPEN_PATHS:
        assert client.get(path).status_code == 200, path


def test_startup_warns_once_when_unconfigured(monkeypatch, caplog):
    monkeypatch.delenv("FL_API_KEY")
    with caplog.at_level(logging.WARNING, logger=auth.logger.name):
        auth.warn_if_fl_api_key_missing()
    assert "FL_API_KEY is not set" in caplog.text
