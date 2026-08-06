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

"""Boundary input validation for the FL API.

The FL API performs no authentication of its own — it trusts that the only caller is
flip-api / fl-server on the trust's internal Docker network. To keep that trust from
becoming a path-traversal or SSRF foothold (a compromised trust container, or an
operator with an SSM port-forward, could reach the API directly), every request value
that becomes a filesystem path or an outbound fetch is validated here first.
"""

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, status


def safe_join(base: Path, *parts: str) -> Path:
    """Join untrusted components onto ``base`` and confirm the result stays within it.

    Guards against ``..`` or absolute components in caller-derived relative paths (e.g. a
    bundle URL's path segments) escaping the job directory. ``base`` may not yet exist;
    only the parent-containment relationship is enforced.

    Args:
        base (Path): The trusted base directory the result must stay within.
        *parts (str): Untrusted path components to join onto ``base``.

    Returns:
        Path: The resolved path, guaranteed to be inside ``base``.

    Raises:
        HTTPException: 400 if the joined path resolves outside ``base``.
    """
    base_resolved = base.resolve()
    target = base_resolved.joinpath(*parts).resolve()
    if not target.is_relative_to(base_resolved):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsafe path {Path(*parts)!r} escapes {base}.",
        )
    return target


def validate_bundle_url(url: str) -> str:
    """Reject bundle download URLs that are unsafe to fetch server-side.

    The FL API fetches every ``bundle_urls`` entry server-side, so an unchecked URL is an
    SSRF vector. The URL must be https, carry a host, use the default https port, and not
    name a private / loopback / link-local IP literal. Requiring https blocks the common
    ``http://169.254.169.254`` metadata fetch and non-http schemes (flip-api presigns S3
    over https in every environment); an optional comma-separated ``BUNDLE_URL_ALLOWED_HOSTS``
    pins fetches to the expected object-store origin when configured. DNS names are not
    resolved here — the redirect-disabled fetch and the optional allow-list cover the rest.

    Args:
        url (str): A bundle download URL from the request body.

    Returns:
        str: The validated URL, unchanged.

    Raises:
        HTTPException: 400 if the URL is not https, has no host, uses a non-443 port, names a
            private/loopback/link-local IP literal, or is not on the host allow-list.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL must use https: {url!r}.",
        )

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL has no host: {url!r}.",
        )

    # Presigned S3 URLs are always served on 443; a custom port points at an internal service.
    # ``urlparse`` defers port parsing, so a malformed / out-of-range port raises ValueError on
    # access here (not at ``urlparse`` above) — convert it to a clean 400 rather than a 500.
    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL has an invalid port: {url!r}.",
        ) from None
    if port not in (None, 443):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL port not allowed: {port}.",
        )

    # Refuse a host carrying a NUL or other control character before either parser sees it.
    # urlparse passes NUL straight through to .hostname, and resolvers that truncate at NUL would
    # then reach a different host than the one checked here — "127.0.0.1\x00" is the loopback
    # bypass that shape buys. Neither parser rejects it usefully on its own: inet_aton raises a
    # plain ValueError (not the AddressValueError subclass), which would either escape as a 500 or,
    # if swallowed, leave the range checks below unrun. No legitimate host contains these.
    if any(ch < " " or ch == "\x7f" for ch in hostname):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL host not allowed: {hostname!r}.",
        )

    # Block IP-literal hosts in non-public ranges. Two parsers, because they disagree:
    # ipaddress.ip_address accepts only the canonical dotted-quad form, while the resolver behind
    # the actual fetch (getaddrinfo -> inet_aton) also accepts packed decimal, hex and octal
    # spellings — "2130706433", "0x7f000001", "017700000001", "127.1" and "0" all reach loopback.
    # Parsing with ip_address alone therefore left the checks below unrun for exactly the
    # spellings an attacker would pick. inet_aton raises OSError on a real DNS name, so a host
    # neither parser accepts is left to the https + optional allow-list checks — we still do not
    # resolve DNS here, which would make this function network-dependent without closing the
    # rebinding window anyway.
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(hostname))
        except OSError:
            ip = None
    if ip is not None and (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL host not allowed: {hostname!r}.",
        )

    allowed = {host.strip().lower() for host in os.getenv("BUNDLE_URL_ALLOWED_HOSTS", "").split(",") if host.strip()}
    if allowed and hostname.lower() not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL host not allowed: {hostname!r}.",
        )
    return url
