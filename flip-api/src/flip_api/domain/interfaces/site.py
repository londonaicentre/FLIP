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


from typing import Any

from pydantic import BaseModel, HttpUrl, field_validator


class ISiteBanner(BaseModel):
    message: str
    # HttpUrl restricts the scheme to http/https, which is what matters here: this value is
    # rendered into an href that every user sees, so a `javascript:` (or `data:`) URL would
    # execute in the visitor's session. Defence in depth behind the frontend's own guard — the
    # API is reachable directly, so the UI check alone protects nothing.
    link: HttpUrl | None = None
    enabled: bool

    @field_validator("link", mode="before")
    @classmethod
    def _empty_string_is_no_link(cls, v: Any) -> Any:
        """Treat an empty or whitespace-only link as absent.

        ``update_site_details`` persists ``""`` rather than NULL when a banner has no link, so
        every such row would otherwise fail ``HttpUrl`` validation on read. Coercing here keeps
        those rows valid instead of routing them through the read path's error fallback.
        """
        if isinstance(v, str) and v.strip() == "":
            return None

        return v


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
