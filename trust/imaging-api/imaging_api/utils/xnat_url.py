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

from collections.abc import Mapping
from urllib.parse import quote, urlencode

from imaging_api.config import get_settings

XNAT_URL = get_settings().XNAT_URL


def quote_path_segment(value: str) -> str:
    """Quote one XNAT identifier, rejecting segments that cannot identify a resource.

    Args:
        value (str): The raw identifier or filename, not an already-encoded URL segment.

    Returns:
        str: The identifier encoded as one URL path segment.

    Raises:
        ValueError: If the identifier is empty or a dot-segment. ``quote`` leaves literal
            dots unchanged, allowing HTTP clients to normalize ``.`` or ``..`` away.
    """
    if not value or value in (".", ".."):
        raise ValueError("XNAT path segment must not be empty or a dot-segment")
    return quote(value, safe="")


def xnat_url(*segments: str, query: Mapping[str, str] | None = None) -> str:
    """Build an XNAT URL from raw path segments, quoting each one.

    Args:
        segments (str): Raw path segments, including literal endpoint names.
        query (Mapping[str, str] | None): Optional raw query parameters.

    Returns:
        str: The XNAT URL with encoded path segments and query parameters.

    Raises:
        ValueError: If any path segment is empty or a dot-segment.
    """
    url = f"{XNAT_URL}/{'/'.join(quote_path_segment(segment) for segment in segments)}"
    return f"{url}?{urlencode(query)}" if query else url
