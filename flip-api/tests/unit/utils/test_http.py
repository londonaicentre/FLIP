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

import os
import ssl
import subprocess
from unittest.mock import MagicMock, patch

import httpx
import pytest

from flip_api.utils.http import http_delete, http_get, http_post, trust_ssl_context


@pytest.fixture
def valid_ca_cert(tmp_path):
    """Generate a self-signed CA certificate PEM file for use in tests."""
    cert_path = tmp_path / "trust-ca.crt"
    key_path = tmp_path / "trust-ca.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/C=GB/CN=Test Trust CA",
        ],
        check=True,
        capture_output=True,
    )
    return cert_path


def test_trust_ssl_context_returns_true_when_env_not_set():
    """When TRUST_CA_BUNDLE is not set, _trust_ssl_context should return True (system CA store)."""
    env_without_bundle = {k: v for k, v in os.environ.items() if k != "TRUST_CA_BUNDLE"}
    with patch.dict("os.environ", env_without_bundle, clear=True):
        result = trust_ssl_context()
        assert result is True


def test_trust_ssl_context_returns_ssl_context_when_env_set(valid_ca_cert):
    """When TRUST_CA_BUNDLE points to a valid PEM file, _trust_ssl_context returns an SSLContext."""
    with patch.dict("os.environ", {"TRUST_CA_BUNDLE": str(valid_ca_cert)}):
        result = trust_ssl_context()
        assert isinstance(result, ssl.SSLContext)


def test_trust_ssl_context_falls_back_to_true_on_bad_pem(tmp_path):
    """When TRUST_CA_BUNDLE points to an invalid/corrupt PEM, _trust_ssl_context falls back to True."""
    bad_file = tmp_path / "bad-ca.crt"
    bad_file.write_text("this is not a valid pem certificate")

    with patch.dict("os.environ", {"TRUST_CA_BUNDLE": str(bad_file)}):
        result = trust_ssl_context()
        assert result is True


def test_trust_ssl_context_falls_back_to_true_when_file_missing():
    """When TRUST_CA_BUNDLE is set to a non-existent path, _trust_ssl_context falls back to True."""
    with patch.dict("os.environ", {"TRUST_CA_BUNDLE": "/nonexistent/path/cert.crt"}):
        result = trust_ssl_context()
        assert result is True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def json_response():
    mock = MagicMock()
    mock.json.return_value = {"status": "ok"}
    mock.raise_for_status.return_value = None
    return mock


@pytest.fixture
def text_response():
    mock = MagicMock()
    mock.json.side_effect = ValueError("not json")
    mock.text = "plain text"
    mock.raise_for_status.return_value = None
    return mock


@pytest.fixture
def error_response():
    mock = MagicMock()
    mock.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404 Not Found", request=MagicMock(), response=MagicMock()
    )
    return mock


# ---------------------------------------------------------------------------
# http_get
# ---------------------------------------------------------------------------


def test_http_get_returns_json(json_response):
    with patch.object(httpx.Client, "get", return_value=json_response):
        result = http_get("http://example.com/resource")
    assert result == {"status": "ok"}


def test_http_get_returns_text_when_no_json(text_response):
    with patch.object(httpx.Client, "get", return_value=text_response):
        result = http_get("http://example.com/resource")
    assert result == "plain text"


def test_http_get_passes_request_id_header(json_response):
    with patch.object(httpx.Client, "get", return_value=json_response) as mock_get:
        http_get("http://example.com/resource", request_id="req-1")
    mock_get.assert_called_once_with("http://example.com/resource", headers={"x-request-id": "req-1"})


def test_http_get_no_request_id_sends_empty_headers(json_response):
    with patch.object(httpx.Client, "get", return_value=json_response) as mock_get:
        http_get("http://example.com/resource")
    mock_get.assert_called_once_with("http://example.com/resource", headers={})


def test_http_get_raises_on_http_status_error(error_response):
    with patch.object(httpx.Client, "get", return_value=error_response):
        with pytest.raises(httpx.HTTPStatusError):
            http_get("http://example.com/resource")


def test_http_get_raises_on_request_error():
    with patch.object(httpx.Client, "get", side_effect=httpx.ConnectError("connection refused")):
        with pytest.raises(httpx.RequestError):
            http_get("http://example.com/resource")


# ---------------------------------------------------------------------------
# http_post
# ---------------------------------------------------------------------------


def test_http_post_returns_json(json_response):
    with patch.object(httpx.Client, "post", return_value=json_response):
        result = http_post("http://example.com/resource", data={"key": "value"})
    assert result == {"status": "ok"}


def test_http_post_returns_text_when_no_json(text_response):
    with patch.object(httpx.Client, "post", return_value=text_response):
        result = http_post("http://example.com/resource")
    assert result == "plain text"


def test_http_post_passes_request_id_header(json_response):
    with patch.object(httpx.Client, "post", return_value=json_response) as mock_post:
        http_post("http://example.com/resource", request_id="req-2", data={"x": 1})
    mock_post.assert_called_once_with(
        "http://example.com/resource",
        headers={"Content-Type": "application/json", "x-request-id": "req-2"},
        json={"x": 1},
    )


def test_http_post_no_request_id_omits_x_request_id_header(json_response):
    with patch.object(httpx.Client, "post", return_value=json_response) as mock_post:
        http_post("http://example.com/resource", data={"x": 1})
    call_headers = mock_post.call_args.kwargs["headers"]
    assert "x-request-id" not in call_headers
    assert call_headers["Content-Type"] == "application/json"


def test_http_post_passes_timeout_when_provided(json_response):
    with patch.object(httpx.Client, "post", return_value=json_response) as mock_post:
        http_post("http://example.com/resource", data={}, timeout=5.0)
    mock_post.assert_called_once_with(
        "http://example.com/resource",
        headers={"Content-Type": "application/json"},
        json={},
        timeout=5.0,
    )


def test_http_post_omits_timeout_when_none(json_response):
    with patch.object(httpx.Client, "post", return_value=json_response) as mock_post:
        http_post("http://example.com/resource", data={}, timeout=None)
    assert "timeout" not in mock_post.call_args.kwargs


def test_http_post_raises_on_http_status_error(error_response):
    with patch.object(httpx.Client, "post", return_value=error_response):
        with pytest.raises(httpx.HTTPStatusError):
            http_post("http://example.com/resource")


def test_http_post_raises_on_request_error():
    with patch.object(httpx.Client, "post", side_effect=httpx.ConnectError("connection refused")):
        with pytest.raises(httpx.RequestError):
            http_post("http://example.com/resource")


# ---------------------------------------------------------------------------
# http_delete
# ---------------------------------------------------------------------------


def test_http_delete_returns_json(json_response):
    with patch.object(httpx.Client, "delete", return_value=json_response):
        result = http_delete("http://example.com/resource/1")
    assert result == {"status": "ok"}


def test_http_delete_returns_text_when_no_json(text_response):
    with patch.object(httpx.Client, "delete", return_value=text_response):
        result = http_delete("http://example.com/resource/1")
    assert result == "plain text"


def test_http_delete_passes_request_id_header(json_response):
    with patch.object(httpx.Client, "delete", return_value=json_response) as mock_del:
        http_delete("http://example.com/resource/1", request_id="req-3")
    mock_del.assert_called_once_with("http://example.com/resource/1", headers={"x-request-id": "req-3"})


def test_http_delete_no_request_id_sends_empty_headers(json_response):
    with patch.object(httpx.Client, "delete", return_value=json_response) as mock_del:
        http_delete("http://example.com/resource/1")
    mock_del.assert_called_once_with("http://example.com/resource/1", headers={})


def test_http_delete_raises_on_http_status_error(error_response):
    with patch.object(httpx.Client, "delete", return_value=error_response):
        with pytest.raises(httpx.HTTPStatusError):
            http_delete("http://example.com/resource/1")


def test_http_delete_raises_on_request_error():
    with patch.object(httpx.Client, "delete", side_effect=httpx.ConnectError("connection refused")):
        with pytest.raises(httpx.RequestError):
            http_delete("http://example.com/resource/1")
