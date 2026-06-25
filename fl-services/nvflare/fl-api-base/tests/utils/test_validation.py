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

from uuid import uuid4

import pytest
from fastapi import HTTPException

from fl_api.utils.validation import (
    safe_join,
    validate_app_folder_name,
    validate_bundle_url,
    validate_model_id,
)


def test_validate_model_id_accepts_uuid():
    model_id = str(uuid4())
    assert validate_model_id(model_id) == model_id


@pytest.mark.parametrize("bad", ["", "not-a-uuid", "../etc", "abc/def", "test-model-123"])
def test_validate_model_id_rejects_non_uuid(bad):
    with pytest.raises(HTTPException) as exc:
        validate_model_id(bad)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("name", ["app", "app-trust1", "3d_spleen_segmentation_evaluation", str(uuid4())])
def test_validate_app_folder_name_accepts_valid(name):
    assert validate_app_folder_name(name) == name


@pytest.mark.parametrize("bad", ["", "..", "../etc", "a/b", "a\\b", ".hidden", "a b", "a;b"])
def test_validate_app_folder_name_rejects_traversal_and_illegal(bad):
    with pytest.raises(HTTPException) as exc:
        validate_app_folder_name(bad)
    assert exc.value.status_code == 400


def test_safe_join_returns_contained_path(tmp_path):
    result = safe_join(tmp_path, "app", "config", "config_fed_server.json")
    assert result == (tmp_path / "app" / "config" / "config_fed_server.json").resolve()
    assert result.is_relative_to(tmp_path.resolve())


def test_safe_join_allows_empty_parts(tmp_path):
    assert safe_join(tmp_path, "") == tmp_path.resolve()


@pytest.mark.parametrize("parts", [("..",), ("..", "..", "etc"), ("/etc/passwd",), ("app", "..", "..", "secret")])
def test_safe_join_rejects_escape(tmp_path, parts):
    with pytest.raises(HTTPException) as exc:
        safe_join(tmp_path, *parts)
    assert exc.value.status_code == 400


def test_validate_bundle_url_accepts_https():
    url = "https://test.local/bundles/app/config.json"
    assert validate_bundle_url(url) == url


@pytest.mark.parametrize("bad", ["http://test.local/x", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"])
def test_validate_bundle_url_rejects_non_https(bad):
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url(bad)
    assert exc.value.status_code == 400


def test_validate_bundle_url_enforces_host_allow_list(monkeypatch):
    monkeypatch.setenv("BUNDLE_URL_ALLOWED_HOSTS", "objectstore.internal, s3.eu-west-2.amazonaws.com")
    assert validate_bundle_url("https://s3.eu-west-2.amazonaws.com/bucket/key")
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url("https://evil.example.com/key")
    assert exc.value.status_code == 400
