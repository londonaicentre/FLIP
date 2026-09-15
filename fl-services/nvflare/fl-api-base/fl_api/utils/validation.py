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
import logging
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, status

# A child of uvicorn's logger so the record reaches uvicorn's handler without this leaf module importing the
# app config (fl_api.utils.logger does, and validation must stay importable with no environment at all).
logger = logging.getLogger("uvicorn.fl_api.validation")

# Set once the empty-allow-list warning has been logged, so the boot log carries it exactly once rather than
# once per bundle file. Module state, mirrored by hand in the Flower copy (the sync check compares functions).
_warned_empty_allow_list = False


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


def bundle_url_allowed_hosts() -> set[str]:
    """The hosts ``BUNDLE_URL_ALLOWED_HOSTS`` admits, lower-cased and root-label-stripped.

    Returns:
        set[str]: The comma-separated entries with whitespace and any trailing dot removed, lower-cased so they
        compare against ``urlparse(...).hostname`` (which lower-cases) after ``validate_bundle_url``'s own
        root-label strip. Empty when the variable is unset or holds only separators.
    """
    return {
        host.strip().lower().rstrip(".")
        for host in os.getenv("BUNDLE_URL_ALLOWED_HOSTS", "").split(",")
        if host.strip().rstrip(".")
    }


def warn_if_bundle_url_allow_list_empty() -> None:
    """Log, once per process, that the bundle-fetch host allow-list is off and what that leaves in place.

    Called from each app's startup hook so the boot log carries it where an operator looks, and from
    ``validate_bundle_url`` as the fallback for a process that reached a fetch without the hook. Not fatal: a
    standalone dev harness with no hub legitimately runs without the variable (nothing presigns bundle URLs
    for it), but every deployment that takes uploads from the hub must set it — the deploy composes and the
    ECS task definition derive it from ``AWS_REGION``, so an empty value there is a wiring regression.
    """
    global _warned_empty_allow_list
    if _warned_empty_allow_list or bundle_url_allowed_hosts():
        return
    _warned_empty_allow_list = True
    logger.warning(
        "BUNDLE_URL_ALLOWED_HOSTS is empty: the FL API will fetch an app bundle from ANY public https host a "
        "caller names. Only the non-public address check remains (on the URL's host and on what it resolves "
        "to), and that check is a resolve-then-fetch race a DNS-rebinding attacker can win. Set it to the "
        "object-store host the hub presigns bundle URLs against — s3.<AWS_REGION>.amazonaws.com, which is what "
        "the deploy composes and the ECS task definition derive — unless this is a standalone dev harness with "
        "no hub."
    )


def _is_disallowed_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether ``ip`` lies in a range the server-side bundle fetch must never reach.

    One definition for both the IP-literal host and every address a DNS name resolves to, so the two paths
    cannot drift. An IPv4-mapped IPv6 address (``::ffff:127.0.0.1``) is judged by the IPv4 address it wraps:
    the socket layer delivers it to that IPv4 host, and not every interpreter carries the wrapped address's
    range flags on the IPv6 object.

    Args:
        ip (ipaddress.IPv4Address | ipaddress.IPv6Address): A parsed address.

    Returns:
        bool: True if the address is private, loopback, link-local, reserved, multicast or unspecified.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def resolve_bundle_host(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve ``hostname`` to every address the bundle fetch could connect to, over both families.

    The seam ``validate_bundle_url`` resolves through: tests replace this function on the module so they never
    touch DNS. ``getaddrinfo`` is asked for TCP/443 — the connection the fetch actually opens — and every
    answer in both families is returned so the caller judges all of them, not just the first.

    Args:
        hostname (str): A DNS name (IP literals are handled before this is reached).

    Returns:
        list[ipaddress.IPv4Address | ipaddress.IPv6Address]: Every resolved address, in resolver order.

    Raises:
        OSError: If the name does not resolve (``socket.gaierror`` is a subclass).
        UnicodeError: If the name cannot be IDNA-encoded, e.g. a label longer than 63 characters.
    """
    return [
        ipaddress.ip_address(sockaddr[0])
        for _family, _type, _proto, _canonname, sockaddr in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    ]


def validate_bundle_url(url: str) -> str:
    """Reject bundle download URLs that are unsafe to fetch server-side.

    The FL API fetches every ``bundle_urls`` entry server-side, so an unchecked URL is an SSRF vector. The URL
    must be https, carry a host, use the default https port, and not name a private / loopback / link-local
    address — as an IP literal in any spelling, or as a DNS name that resolves to one. Requiring https blocks
    the common ``http://169.254.169.254`` metadata fetch and non-http schemes (flip-api presigns S3 over https
    in every environment).

    Two controls sit behind those checks, in this order:

    * **The host allow-list is the primary control.** A comma-separated ``BUNDLE_URL_ALLOWED_HOSTS`` pins
      fetches to the object-store origin the hub presigns against. flip-api sets
      ``AWS_ENDPOINT_URL_S3=https://s3.<AWS_REGION>.amazonaws.com`` in every environment, which makes botocore
      emit path-style presigned URLs — bucket in the path, host exactly ``s3.<AWS_REGION>.amazonaws.com`` —
      so the deploy composes and the ECS task definition derive the allow-list from ``AWS_REGION`` alone, and
      the match is exact (case-insensitive, root label ignored): no suffix or wildcard form, because a suffix
      would admit any bucket in the region. It is consulted **before** the name is resolved, so an attacker-
      chosen name is never even looked up — a resolver query to an attacker's nameserver is the classic
      out-of-band confirmation channel of a blind SSRF (FLIP#905). Empty means "any public host", which
      ``warn_if_bundle_url_allow_list_empty`` reports loudly.
    * **Resolve-and-recheck is defence in depth.** A DNS name is resolved through ``resolve_bundle_host`` and
      every returned address, both families, must pass the same range check as an IP literal; a resolution
      error or an empty answer fails closed ("could not verify" must not mean "allowed" — the fetch would
      fail anyway). This closes the ``metadata.internal.attacker.example`` shape (a public name pointing at
      a private address) that no literal parsing can, but it is a resolve-then-fetch pair: the fetch in
      ``fl_api.utils.upload`` resolves the name again through ``requests``, and a DNS-rebinding attacker who
      answers public here and private there wins that race. Pinning the connection to the checked address
      needs a custom transport, which is deliberately not built — with the allow-list set the residual is
      moot, because only the object-store host is ever resolved at all. IP literals are not resolved; their
      range check is complete on its own, and ``localhost`` is a literal loopback alias handled the same way.

    Args:
        url (str): A bundle download URL from the request body.

    Returns:
        str: The validated URL, unchanged.

    Raises:
        HTTPException: 400 if the URL is not https, has no host, uses a non-443 port, names a
            private/loopback/link-local IP literal (in any spelling, root-label included) or ``localhost``,
            is not on the host allow-list, does not resolve, or resolves to any non-public address.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL must use https: {url!r}.",
        )

    # Strip any root label (trailing dots) before the emptiness check and both parsers below. DNS
    # treats "127.0.0.1." as absolute and glibc resolvers reach loopback, while both IP parsers
    # reject the trailing dot — so the unstripped spelling skipped every range check: the same
    # parsers-disagree failure mode as the numeric spellings. Stripping first also keeps a
    # dot-only host on the no-host rejection instead of the "not an IP literal" branch.
    hostname = (parsed.hostname or "").rstrip(".")
    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL has no host: {url!r}.",
        )

    # "localhost" is a literal loopback alias, not a name that needs resolving — the range checks
    # below cover the numeric spellings, this covers the named one. urlparse lowercases .hostname,
    # so one comparison catches every case variant.
    if hostname == "localhost":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL host not allowed: {hostname!r}.",
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
    # neither parser accepts is a DNS name, handled by the allow-list and resolution steps below.
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(hostname))
        except OSError:
            ip = None
    if ip is not None and _is_disallowed_address(ip):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL host not allowed: {hostname!r}.",
        )

    # The allow-list comes before resolution on purpose: an off-list name is refused without a single DNS
    # query, so the API never resolves a name an attacker chose (see the docstring). Exact match only.
    allowed = bundle_url_allowed_hosts()
    if allowed and hostname.lower() not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL host not allowed: {hostname!r}.",
        )
    if not allowed:
        warn_if_bundle_url_allow_list_empty()

    # A DNS name: resolve it and hold every answer to the literal check above. Fail closed on any
    # resolver error — the fetch could not have succeeded either, and an unverifiable host is not a
    # verified one. IP literals were fully judged above and are not resolved.
    if ip is None:
        try:
            addresses = resolve_bundle_host(hostname)
        except (OSError, UnicodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Bundle URL host could not be resolved: {hostname!r}.",
            ) from exc
        if not addresses or any(_is_disallowed_address(address) for address in addresses):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Bundle URL host not allowed: {hostname!r} (resolves to a non-public address).",
            )
    return url
