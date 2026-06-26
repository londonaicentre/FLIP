# Copyright (c) 2026 Flower Labs GmbH
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
    validate_bundle_url,
    validate_tutorial_folder_name,
)


@pytest.mark.parametrize("name", ["numpy", "3d_spleen_segmentation_evaluation", str(uuid4()), "app-trust1"])
def test_validate_tutorial_folder_name_accepts_valid(name):
    assert validate_tutorial_folder_name(name) == name


@pytest.mark.parametrize("bad", ["", "..", "../etc", "a/b", "a\\b", ".hidden", "a b", "a;b"])
def test_validate_tutorial_folder_name_rejects_traversal_and_illegal(bad):
    with pytest.raises(HTTPException) as exc:
        validate_tutorial_folder_name(bad)
    assert exc.value.status_code == 400


def test_safe_join_returns_contained_path(tmp_path):
    result = safe_join(tmp_path, "app", "config.toml")
    assert result == (tmp_path / "app" / "config.toml").resolve()
    assert result.is_relative_to(tmp_path.resolve())


def test_safe_join_allows_empty_parts(tmp_path):
    assert safe_join(tmp_path, "") == tmp_path.resolve()


@pytest.mark.parametrize("parts", [("..",), ("..", "..", "etc"), ("/etc/passwd",), ("app", "..", "..", "secret")])
def test_safe_join_rejects_escape(tmp_path, parts):
    with pytest.raises(HTTPException) as exc:
        safe_join(tmp_path, *parts)
    assert exc.value.status_code == 400


def test_validate_bundle_url_accepts_https():
    url = "https://example.com/bundle/app/config.toml"
    assert validate_bundle_url(url) == url


@pytest.mark.parametrize("bad", ["http://example.com/x", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"])
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


@pytest.mark.parametrize(
    "bad",
    [
        "https:///bundle/app/config.toml",  # no host
        "https://127.0.0.1/x",  # loopback IP literal
        "https://10.0.0.5/x",  # private IP literal
        "https://169.254.169.254/x",  # link-local IP literal (metadata endpoint over https)
        "https://[::1]/x",  # IPv6 loopback literal
        "https://s3.eu-west-2.amazonaws.com:8080/bucket/key",  # non-443 port
    ],
)
def test_validate_bundle_url_rejects_unsafe_hosts(bad):
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url(bad)
    assert exc.value.status_code == 400


def test_validate_bundle_url_accepts_explicit_443():
    url = "https://s3.eu-west-2.amazonaws.com:443/bucket/key"
    assert validate_bundle_url(url) == url
