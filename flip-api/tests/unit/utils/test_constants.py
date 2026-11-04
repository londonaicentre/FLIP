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
"""The e2e smoke's hub URL follows the instance's API_PORT unless FLIP_E2E_BASE_URL names one (FLIP#1604)."""

import importlib

import pytest

from flip_api.utils import constants


@pytest.fixture
def reload_base_url(monkeypatch):
    """Re-evaluate the module under the patched env, then restore it for every other test."""
    yield lambda: importlib.reload(constants).BASE_URL
    monkeypatch.undo()
    importlib.reload(constants)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, "http://localhost:8080/api"),
        ({"API_PORT": ""}, "http://localhost:8080/api"),
        ({"API_PORT": "18080"}, "http://localhost:18080/api"),
        ({"API_PORT": "18080", "FLIP_E2E_BASE_URL": "https://hub.example/api"}, "https://hub.example/api"),
    ],
)
def test_base_url_follows_api_port(monkeypatch, reload_base_url, env, expected):
    monkeypatch.delenv("API_PORT", raising=False)
    monkeypatch.delenv("FLIP_E2E_BASE_URL", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert reload_base_url() == expected
