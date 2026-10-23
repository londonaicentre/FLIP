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
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flip_api.domain.schemas.types import TrimStr


class UpdateTrustStatusSchema(BaseModel):
    fl_client_endpoint: TrimStr = Field(..., description="'fl_client_endpoint' is required")


# ---------------------------------------------------------------------------
# Automatic upgrades (trust auto-upgrade)
# ---------------------------------------------------------------------------

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

UPGRADE_LEASE_STATES = ("STARTING", "SUCCEEDED", "FAILED", "BLOCKED")


def _check_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"'{name}' is not a known IANA time zone, e.g. Europe/London") from e
    return name


class AutoUpgradeSettings(BaseModel):
    """What a Trust Admin edits: the switch and the optional maintenance window."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    window_start: str | None = Field(default=None, alias="windowStart")
    window_end: str | None = Field(default=None, alias="windowEnd")
    timezone: str | None = None

    @model_validator(mode="after")
    def _window_is_whole(self) -> "AutoUpgradeSettings":
        times = (self.window_start, self.window_end)
        if any(t is not None and not _HHMM.match(t) for t in times):
            raise ValueError("The window times must be HH:MM, 24-hour")
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("Set both the start and the end of the window, or neither")
        if self.window_start is not None and self.window_start == self.window_end:
            raise ValueError("The window must not start and end at the same time")
        if self.window_start is not None and not self.timezone:
            raise ValueError("A window needs the Trust's time zone")
        if self.timezone:
            self.timezone = _check_timezone(self.timezone)
        return self


class AutoUpgradeLastAttempt(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    state: str
    release: str
    at: str
    detail: str | None = None


class AutoUpgradeView(BaseModel):
    """A trust's auto-upgrade settings and status as its Trust Admin sees them (``GET /trust/{id}/auto-upgrade``)."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    target_release: str | None = Field(default=None, alias="targetRelease")
    current_release: str | None = Field(default=None, alias="currentRelease")
    window_start: str | None = Field(default=None, alias="windowStart")
    window_end: str | None = Field(default=None, alias="windowEnd")
    timezone: str | None = None
    state: str
    blockers: list[str] = Field(default_factory=list)
    pending_since: str | None = Field(default=None, alias="pendingSince")
    last_attempt: AutoUpgradeLastAttempt | None = Field(default=None, alias="lastAttempt")


class UpgradeWindow(BaseModel):
    start: str
    end: str


class UpgradeLease(BaseModel):
    """The lease a host agent holds while it upgrades; absent when none is held."""

    release: str
    expires_at: str


class UpgradePolicy(BaseModel):
    """What the host agent is told on each tick (``GET /trust/upgrade-policy``). snake_case: it is machine-read."""

    enabled: bool
    target_release: str | None
    maintenance_window: UpgradeWindow | None
    timezone: str | None
    is_busy: bool
    lease: UpgradeLease | None
    # A release whose last attempt FAILED and has not been cleared by an operator: never retried.
    failed_release: str | None


class UpgradeStatusInput(BaseModel):
    """What the host agent reports (``POST /trust/upgrade-status``)."""

    state: Literal["STARTING", "SUCCEEDED", "FAILED", "BLOCKED"]
    release: str = Field(min_length=1, max_length=64)
    # A captured log tail on FAILED, or the reason on BLOCKED.
    detail: str | None = Field(default=None, max_length=8000)


class UpgradeEnableInput(BaseModel):
    """The install step's one setting (``PUT /trust/upgrade-policy``): the trust's own operator switching it."""

    enabled: bool
