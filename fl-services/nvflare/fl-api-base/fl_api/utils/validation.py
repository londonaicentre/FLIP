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

import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, status

# app_folder is either an uploaded-model UUID or a pre-baked tutorial folder
# (e.g. "numpy", "3d_spleen_segmentation_evaluation"), so it cannot be UUID-only.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_model_id(model_id: str) -> str:
    """Reject any ``model_id`` that is not a canonical UUID.

    flip-api (the only caller) always sends a ``uuid4``; a non-UUID value is either a bug
    or an attempt to smuggle path-traversal sequences into the upload directory name.

    Args:
        model_id (str): The model identifier taken from the request path.

    Returns:
        str: The validated ``model_id``, unchanged.

    Raises:
        HTTPException: 400 if ``model_id`` is not a valid UUID.
    """
    try:
        uuid.UUID(model_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid model_id: {model_id!r} is not a valid UUID.",
        ) from None
    return model_id


def validate_app_folder_name(name: str) -> str:
    """Reject app/job folder names that could escape the source root.

    Accepts uploaded-model UUIDs and the pre-baked tutorial folder names; rejects path
    separators, parent references, hidden names and empty values.

    Args:
        name (str): The app folder name taken from the request path.

    Returns:
        str: The validated name, unchanged.

    Raises:
        HTTPException: 400 if the name contains traversal sequences or illegal characters.
    """
    if not name or ".." in name or name.startswith(".") or not _SAFE_NAME.match(name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid app folder name: {name!r}.",
        )
    return name


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
    """Reject bundle download URLs that are not https or not on the host allow-list.

    The FL API fetches every ``bundle_urls`` entry server-side, so an unchecked URL is an
    SSRF vector. Requiring https blocks the common ``http://169.254.169.254`` metadata
    fetch and non-http schemes (flip-api presigns S3 over https in every environment); an
    optional comma-separated ``BUNDLE_URL_ALLOWED_HOSTS`` pins fetches to the expected
    object-store origin when configured.

    Args:
        url (str): A bundle download URL from the request body.

    Returns:
        str: The validated URL, unchanged.

    Raises:
        HTTPException: 400 if the scheme is not https or the host is not allow-listed.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL must use https: {url!r}.",
        )
    allowed = {host.strip().lower() for host in os.getenv("BUNDLE_URL_ALLOWED_HOSTS", "").split(",") if host.strip()}
    if allowed and (parsed.hostname or "").lower() not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle URL host not allowed: {parsed.hostname!r}.",
        )
    return url
