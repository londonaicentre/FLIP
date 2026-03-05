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
from unittest.mock import patch

import pytest

from flip_api.utils.http import _trust_ssl_context


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
        result = _trust_ssl_context()
        assert result is True


def test_trust_ssl_context_returns_ssl_context_when_env_set(valid_ca_cert):
    """When TRUST_CA_BUNDLE points to a valid PEM file, _trust_ssl_context returns an SSLContext."""
    with patch.dict("os.environ", {"TRUST_CA_BUNDLE": str(valid_ca_cert)}):
        result = _trust_ssl_context()
        assert isinstance(result, ssl.SSLContext)


def test_trust_ssl_context_falls_back_to_true_on_bad_pem(tmp_path):
    """When TRUST_CA_BUNDLE points to an invalid/corrupt PEM, _trust_ssl_context falls back to True."""
    bad_file = tmp_path / "bad-ca.crt"
    bad_file.write_text("this is not a valid pem certificate")

    with patch.dict("os.environ", {"TRUST_CA_BUNDLE": str(bad_file)}):
        result = _trust_ssl_context()
        assert result is True


def test_trust_ssl_context_falls_back_to_true_when_file_missing():
    """When TRUST_CA_BUNDLE is set to a non-existent path, _trust_ssl_context falls back to True."""
    with patch.dict("os.environ", {"TRUST_CA_BUNDLE": "/nonexistent/path/cert.crt"}):
        result = _trust_ssl_context()
        assert result is True
