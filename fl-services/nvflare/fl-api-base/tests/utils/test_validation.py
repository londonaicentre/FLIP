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

import pytest
from fastapi import HTTPException

from fl_api.utils.validation import (
    safe_join,
    validate_bundle_url,
)


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


@pytest.mark.parametrize(
    "bad",
    [
        "https:///bundle/app/config.json",  # no host
        "https://127.0.0.1/x",  # loopback IP literal
        "https://10.0.0.5/x",  # private IP literal
        "https://169.254.169.254/x",  # link-local IP literal (metadata endpoint over https)
        "https://[::1]/x",  # IPv6 loopback literal
        "https://s3.eu-west-2.amazonaws.com:8080/bucket/key",  # non-443 port
        "https://s3.eu-west-2.amazonaws.com:bad/bucket/key",  # non-numeric port -> clean 400, not 500
        "https://s3.eu-west-2.amazonaws.com:99999/bucket/key",  # out-of-range port -> clean 400, not 500
    ],
)
def test_validate_bundle_url_rejects_unsafe_hosts(bad):
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url(bad)
    assert exc.value.status_code == 400


def test_validate_bundle_url_accepts_explicit_443():
    url = "https://s3.eu-west-2.amazonaws.com:443/bucket/key"
    assert validate_bundle_url(url) == url


# FLIP#893: ipaddress.ip_address only accepts the canonical dotted-quad form, so every other
# numeric spelling used to fall into the "not an IP literal" branch and skip the range checks
# entirely — while glibc's resolver resolves all of them to loopback or a private address.
@pytest.mark.parametrize(
    ("host", "resolves_to"),
    [
        ("2130706433", "127.0.0.1"),      # packed decimal
        ("0x7f000001", "127.0.0.1"),      # hex
        ("017700000001", "127.0.0.1"),    # octal
        ("127.1", "127.0.0.1"),           # short form
        ("0", "0.0.0.0"),                 # unspecified
        ("167772165", "10.0.0.5"),        # packed decimal, private range
    ],
)
def test_validate_bundle_url_rejects_numeric_encoded_private_hosts(host, resolves_to):
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url(f"https://{host}/bundle/app/custom/train.py")
    assert exc.value.status_code == 400


@pytest.mark.parametrize("host", ["test.local", "s3.eu-west-2.amazonaws.com", "1.1.1.1"])
def test_validate_bundle_url_still_accepts_public_hosts(host):
    """The second parser must not turn a DNS name into a rejection, or resolve anything.

    ``test.local`` deliberately does not resolve: if this function ever starts calling
    getaddrinfo, this case fails and the network dependency is caught here rather than in
    production.
    """
    url = f"https://{host}/bundle/app/custom/train.py"
    assert validate_bundle_url(url) == url


@pytest.mark.parametrize("host", ["127.0.0.1\x00", "example.com\x00", "2130706433\x00"])
def test_validate_bundle_url_rejects_hosts_with_embedded_nul(host):
    """An embedded NUL must stay a 400, not escape as an uncaught ValueError.

    urlparse passes NUL through to .hostname and socket.inet_aton raises a plain ValueError on it —
    which ipaddress.AddressValueError does not cover, since it is a subclass rather than a parent.
    """
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url(f"https://{host}/bundle/app/custom/train.py")
    assert exc.value.status_code == 400
