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


import re

from pydantic import BaseModel, field_validator

# Banner links are rendered as anchor `href`s in every user's session. Only
# http(s) URLs are permitted — schemes such as `javascript:` or `data:` would
# otherwise allow stored XSS via the site banner. An empty string / None means
# "no link" and is preserved as-is.
_SAFE_HTTP_URL = re.compile(r"^https?://", re.IGNORECASE)


def is_safe_http_url(value: str | None) -> bool:
    """Return True if ``value`` is a non-empty http(s) URL safe to use as an href.

    Args:
        value (str | None): Candidate URL string.

    Returns:
        bool: True only when the (stripped) value begins with ``http://`` or ``https://``.
    """
    if value is None:
        return False
    return _SAFE_HTTP_URL.match(value.strip()) is not None


class ISiteBanner(BaseModel):
    message: str
    link: str | None = None
    enabled: bool

    @field_validator("link")
    @classmethod
    def _validate_link_scheme(cls, value: str | None) -> str | None:
        """Reject banner links that are not http(s) URLs (defeats `javascript:`/`data:` XSS).

        An empty string or None is allowed and denotes "no link".
        """
        if value is None or value.strip() == "":
            return value
        if not is_safe_http_url(value):
            raise ValueError("Banner link must be an http(s) URL")
        return value


class ISiteDetails(BaseModel):
    deploymentMode: bool
    banner: ISiteBanner | None = None
    # Cap on automatic reimport retries for failed studies. Sourced from
    # Settings.MAX_REIMPORT_COUNT (env-driven) rather than the DB — the
    # backend enforces the cap via the SQL query in
    # get_reimport_queries_service, so exposing the same number here lets
    # the UI's status display and the backend's enforcement agree without
    # a duplicate frontend env var. Optional because the PUT endpoint
    # only updates banner/deploymentMode (maxReimportCount is not DB
    # state to mutate) — GET always populates it.
    maxReimportCount: int | None = None
