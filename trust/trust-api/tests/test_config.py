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
from pydantic import ValidationError

from trust_api.config import Settings


def test_internal_url_vars_default(monkeypatch):
    """The sibling-service URLs are docker-topology constants (service name +
    container port), so Settings supplies them when the kit omits them — the
    kits no longer carry these."""
    monkeypatch.delenv("DATA_ACCESS_API_URL", raising=False)
    monkeypatch.delenv("IMAGING_API_URL", raising=False)

    settings = Settings()

    assert settings.DATA_ACCESS_API_URL == "http://data-access-api:8000"
    assert settings.IMAGING_API_URL == "http://imaging-api:8000"


def test_pacs_ping_interval_defaults_to_five_minutes(monkeypatch):
    """Five minutes is 288 C-ECHOs a day against the hospital PACS, down from the 2,880 a
    ping on every 30 s collection cost (FLIP#1456 E3)."""
    monkeypatch.delenv("PACS_PING_INTERVAL_SECONDS", raising=False)

    assert Settings().PACS_PING_INTERVAL_SECONDS == 300


@pytest.mark.parametrize("value", ["30", "3600"])
def test_pacs_ping_interval_accepts_its_bounds(monkeypatch, value):
    monkeypatch.setenv("PACS_PING_INTERVAL_SECONDS", value)

    assert Settings().PACS_PING_INTERVAL_SECONDS == int(value)


@pytest.mark.parametrize("value", ["0", "-300", "29", "3601", "five minutes"])
def test_pacs_ping_interval_rejects_values_outside_its_bounds(monkeypatch, value):
    """Below 30 s it pings on every collection, the load the setting exists to remove; above
    an hour an outage would hide behind an old "healthy" for longer than that. Both fail
    at startup and name the setting, rather than running with a value nobody meant."""
    monkeypatch.setenv("PACS_PING_INTERVAL_SECONDS", value)

    with pytest.raises(ValidationError, match="PACS_PING_INTERVAL_SECONDS"):
        Settings()
