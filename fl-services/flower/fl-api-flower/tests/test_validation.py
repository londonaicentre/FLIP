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

import ipaddress
import logging
import socket
from uuid import uuid4

import pytest
from fastapi import HTTPException

from fl_api.utils import validation
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
        "https://127.0.0.1./x",  # root-label spelling: both parsers reject it, the resolver accepts it
        "https://169.254.169.254./x",  # root-label link-local (metadata endpoint over https)
        "https://localhost/x",  # literal loopback alias
        "https://./x",  # dot-only host strips to empty -> no host
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
    """The second parser must not turn a DNS name into a rejection.

    ``test.local`` deliberately does not resolve: the DNS names here reach the resolver seam, which the
    conftest stubs to a public answer for every test, so a real ``getaddrinfo`` call would fail this case
    here rather than in production (FLIP#893 kept resolution out entirely; FLIP#905 moved it behind the
    seam). ``1.1.1.1`` is an IP literal and never reaches the seam at all.
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


# FLIP#905 (review finding M-5): a DNS name is resolved and every answer is held to the same range check as
# an IP literal, failing closed on a resolver error; the allow-list is consulted before any lookup and is
# populated per deployment from AWS_REGION. None of these tests performs a real lookup: the conftest stubs
# the resolver seam for every test, and the cases below re-patch it with the answer they need.


def _resolver(monkeypatch, *answers, error=None):
    """Patch the resolver seam to answer ``answers`` (parsed) or raise ``error``, recording each lookup.

    ``raising=False`` so that, run against a validator without the seam, a case fails as ``DID NOT RAISE``
    rather than erroring on the missing attribute — the shape that shows the resolution check is what
    rejects the URL.
    """
    calls = []

    def fake(hostname):
        calls.append(hostname)
        if error is not None:
            raise error
        return [ipaddress.ip_address(answer) for answer in answers]

    monkeypatch.setattr(validation, "resolve_bundle_host", fake, raising=False)
    return calls


@pytest.mark.parametrize(
    "resolved",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # private
        "169.254.169.254",  # link-local: the metadata endpoint behind a public-looking name
        "0.0.0.0",  # unspecified
        "::1",  # IPv6 loopback
        "fd00::1",  # IPv6 unique-local
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
    ],
)
def test_validate_bundle_url_rejects_name_resolving_to_non_public_address(monkeypatch, resolved):
    """``metadata.internal.attacker.example`` is a public name; what it points at is not."""
    calls = _resolver(monkeypatch, resolved)
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url("https://metadata.internal.attacker.example/bundle/app/custom/train.py")
    assert exc.value.status_code == 400
    assert "resolves to a non-public address" in exc.value.detail
    assert calls == ["metadata.internal.attacker.example"]


def test_validate_bundle_url_rejects_mixed_public_and_private_answers(monkeypatch):
    """One private answer among public ones is enough: the fetch may connect to any of them."""
    _resolver(monkeypatch, "52.95.150.1", "2600:1f18::1", "10.0.0.5")
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url("https://s3.evil.example.com/bucket/key")
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "error",
    [
        socket.gaierror(-2, "Name or service not known"),
        OSError("resolver unreachable"),
        UnicodeError("label empty or too long"),  # getaddrinfo's IDNA encode step; not an OSError
    ],
    ids=["nxdomain", "oserror", "idna"],
)
def test_validate_bundle_url_fails_closed_on_resolution_error(monkeypatch, error):
    """A resolver error is "could not verify", which must not mean "allowed"; the fetch would have failed too."""
    _resolver(monkeypatch, error=error)
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url("https://does-not-resolve.example/bundle/app/custom/train.py")
    assert exc.value.status_code == 400
    assert "could not be resolved" in exc.value.detail


def test_validate_bundle_url_fails_closed_on_empty_answer(monkeypatch):
    _resolver(monkeypatch)  # a resolver that returns no addresses at all
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url("https://empty-answer.example/bundle/app/custom/train.py")
    assert exc.value.status_code == 400


def test_validate_bundle_url_accepts_name_resolving_to_public_addresses(monkeypatch):
    calls = _resolver(monkeypatch, "52.95.150.1", "2600:1f18::1")
    url = "https://s3.eu-west-2.amazonaws.com/bucket/key"
    assert validate_bundle_url(url) == url
    assert calls == ["s3.eu-west-2.amazonaws.com"]


def test_validate_bundle_url_accepts_public_ip_literal_without_resolving(monkeypatch):
    """An IP literal is judged in full by the range check; the resolver is never consulted for it."""
    calls = _resolver(monkeypatch, error=AssertionError("resolver must not be called for an IP literal"))
    url = "https://1.1.1.1/bundle/app/custom/train.py"
    assert validate_bundle_url(url) == url
    assert calls == []


@pytest.mark.parametrize("host", ["127.0.0.1", "2130706433", "[::1]", "[::ffff:169.254.169.254]", "localhost"])
def test_validate_bundle_url_rejects_non_public_ip_literal_without_resolving(monkeypatch, host):
    """The FLIP#893 literal rejections stand on their own and still cost no lookup."""
    calls = _resolver(monkeypatch, error=AssertionError("resolver must not be called for an IP literal"))
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url(f"https://{host}/bundle/app/custom/train.py")
    assert exc.value.status_code == 400
    assert calls == []


def test_validate_bundle_url_checks_allow_list_before_resolving(monkeypatch):
    """An off-list name is refused without a lookup, so an attacker-chosen name is never resolved.

    The resolver query itself is the out-of-band signal of a blind SSRF: a name under an attacker's zone
    tells them the API looked. With the list set, only the listed host is ever resolved — and it still is,
    because the allow-list does not skip the resolution recheck.
    """
    monkeypatch.setenv("BUNDLE_URL_ALLOWED_HOSTS", "s3.eu-west-2.amazonaws.com")
    calls = _resolver(monkeypatch, "52.95.150.1")
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url("https://exfil.attacker.example/bundle/app/custom/train.py")
    assert exc.value.status_code == 400
    assert calls == []
    assert validate_bundle_url("https://s3.eu-west-2.amazonaws.com/bucket/key")
    assert calls == ["s3.eu-west-2.amazonaws.com"]


def test_validate_bundle_url_rechecks_resolution_of_allowed_host(monkeypatch):
    """Defence in depth: an allow-listed name that resolves to a non-public address is still refused."""
    monkeypatch.setenv("BUNDLE_URL_ALLOWED_HOSTS", "s3.eu-west-2.amazonaws.com")
    _resolver(monkeypatch, "10.0.0.5")
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url("https://s3.eu-west-2.amazonaws.com/bucket/key")
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "host",
    [
        "s3.eu-west-2.amazonaws.com.attacker.example",  # listed host as a prefix
        "evil-s3.eu-west-2.amazonaws.com",  # listed host as a suffix
        "s3.eu-west-1.amazonaws.com",  # another region
        "bucket.s3.eu-west-2.amazonaws.com",  # virtual-hosted form: a suffix match would admit any bucket
    ],
)
def test_validate_bundle_url_allow_list_match_is_exact(monkeypatch, host):
    """No suffix or wildcard form: the presigned origin is one exact host, s3.<region>.amazonaws.com."""
    monkeypatch.setenv("BUNDLE_URL_ALLOWED_HOSTS", "s3.eu-west-2.amazonaws.com")
    with pytest.raises(HTTPException) as exc:
        validate_bundle_url(f"https://{host}/bucket/key")
    assert exc.value.status_code == 400


@pytest.mark.parametrize("host", ["S3.EU-WEST-2.AMAZONAWS.COM", "s3.eu-west-2.amazonaws.com."])
def test_validate_bundle_url_allow_list_ignores_case_and_root_label(monkeypatch, host):
    """Case and a trailing root label are spelling, not identity: neither dodges nor defeats the list."""
    monkeypatch.setenv("BUNDLE_URL_ALLOWED_HOSTS", " s3.eu-west-2.amazonaws.com. ")
    url = f"https://{host}/bucket/key"
    assert validate_bundle_url(url) == url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", set()),
        (" , ,", set()),
        (". ,", set()),
        ("A.Example., b.example", {"a.example", "b.example"}),
    ],
)
def test_bundle_url_allowed_hosts_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("BUNDLE_URL_ALLOWED_HOSTS", raw)
    assert validation.bundle_url_allowed_hosts() == expected


def test_resolve_bundle_host_returns_every_answer_in_both_families(monkeypatch, stub_public_resolver):
    """The real seam asks getaddrinfo for TCP/443 and parses every sockaddr, IPv4 and IPv6 alike."""
    seen = {}

    def fake_getaddrinfo(host, port, **kwargs):
        seen.update(host=host, port=port, **kwargs)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("52.95.150.1", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2600:1f18::1", 443, 0, 0)),
        ]

    monkeypatch.setattr(validation.socket, "getaddrinfo", fake_getaddrinfo)
    real_resolve_bundle_host = stub_public_resolver
    assert real_resolve_bundle_host("s3.eu-west-2.amazonaws.com") == [
        ipaddress.ip_address("52.95.150.1"),
        ipaddress.ip_address("2600:1f18::1"),
    ]
    assert seen == {"host": "s3.eu-west-2.amazonaws.com", "port": 443, "type": socket.SOCK_STREAM}


def _allow_list_warnings(caplog):
    return [record for record in caplog.records if "BUNDLE_URL_ALLOWED_HOSTS" in record.getMessage()]


def test_warn_if_bundle_url_allow_list_empty_logs_once_per_process(monkeypatch, caplog):
    monkeypatch.delenv("BUNDLE_URL_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(validation, "_warned_empty_allow_list", False)
    with caplog.at_level(logging.WARNING, logger=validation.logger.name):
        validation.warn_if_bundle_url_allow_list_empty()
        validation.warn_if_bundle_url_allow_list_empty()
    (warning,) = _allow_list_warnings(caplog)
    assert warning.levelno == logging.WARNING
    assert "ANY public https host" in warning.getMessage()
    assert "s3.<AWS_REGION>.amazonaws.com" in warning.getMessage()


def test_warn_if_bundle_url_allow_list_empty_is_silent_when_set(monkeypatch, caplog):
    monkeypatch.setenv("BUNDLE_URL_ALLOWED_HOSTS", "s3.eu-west-2.amazonaws.com")
    monkeypatch.setattr(validation, "_warned_empty_allow_list", False)
    with caplog.at_level(logging.WARNING, logger=validation.logger.name):
        validation.warn_if_bundle_url_allow_list_empty()
    assert _allow_list_warnings(caplog) == []


def test_validate_bundle_url_warns_on_first_validation_when_allow_list_empty(monkeypatch, caplog):
    """The fallback for a process that reached a fetch without the startup hook: warn on the first URL only."""
    monkeypatch.delenv("BUNDLE_URL_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(validation, "_warned_empty_allow_list", False)
    with caplog.at_level(logging.WARNING, logger=validation.logger.name):
        validate_bundle_url("https://s3.eu-west-2.amazonaws.com/bucket/key")
        validate_bundle_url("https://s3.eu-west-2.amazonaws.com/bucket/other-key")
    assert len(_allow_list_warnings(caplog)) == 1
