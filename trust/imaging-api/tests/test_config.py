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

from imaging_api.config import Settings


def test_internal_topology_vars_default(monkeypatch):
    """PACS_ID and the internal trust-network URLs are topology constants (the
    single registered PACS is always id 1; the URLs are docker service name +
    container port). They must default in Settings rather than be required kit
    fields a kit can omit or set wrong — a mis-set PACS_ID 404s every DQR query.
    """
    for var in ("PACS_ID", "XNAT_URL", "DATA_ACCESS_API_URL", "XNAT_DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)

    settings = Settings()

    assert settings.PACS_ID == 1
    assert settings.XNAT_URL == "http://xnat-web:8080"
    assert settings.DATA_ACCESS_API_URL == "http://data-access-api:8000"
    assert settings.XNAT_DATABASE_URL == "postgresql+asyncpg://xnat:xnat@xnat-db:5432/xnat"
