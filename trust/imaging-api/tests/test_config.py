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

from imaging_api.config import Settings


def test_internal_topology_vars_default(monkeypatch):
    """The internal trust-network URLs are topology constants (docker service name +
    container port). They must default in Settings rather than be required kit
    fields a kit can omit or set wrong.
    """
    for var in ("XNAT_URL", "DATA_ACCESS_API_URL"):
        monkeypatch.delenv(var, raising=False)

    settings = Settings()

    assert settings.XNAT_URL == "http://xnat-web:8080"
    assert settings.DATA_ACCESS_API_URL == "http://data-access-api:8000"


def test_a_leftover_pacs_id_is_ignored(monkeypatch):
    """There is no PACS id setting any more: DQR queries its default PACS and the import reads that
    PACS's id from XNAT. An older kit or Helm values file that still injects PACS_ID must not stop
    imaging-api booting on upgrade, and must not come back as a setting anything could read.
    """
    monkeypatch.setenv("PACS_ID", "1")

    settings = Settings()

    assert not hasattr(settings, "PACS_ID")


@pytest.mark.parametrize("variable", ["XNAT_DATABASE_URL", "XNAT_DATASOURCE_PASSWORD"])
def test_a_leftover_xnat_database_setting_is_ignored(monkeypatch, variable):
    """imaging-api reads import status through XNAT's REST API and holds no XNAT database settings.

    A kit file keeps XNAT_DATASOURCE_PASSWORD for xnat-web and xnat-db, and an older compose file or
    Helm values file still hands imaging-api one or both. That must not stop imaging-api booting on
    upgrade, and must not come back as a setting anything could read.
    """
    monkeypatch.setenv(variable, "leftover")

    settings = Settings()

    assert not hasattr(settings, variable)
