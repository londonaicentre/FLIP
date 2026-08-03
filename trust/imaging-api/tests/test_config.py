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
from sqlalchemy.engine import make_url

from imaging_api.config import Settings


def test_internal_topology_vars_default(monkeypatch):
    """PACS_ID and the internal trust-network URLs are topology constants (the
    single registered PACS is always id 1; the URLs are docker service name +
    container port). They must default in Settings rather than be required kit
    fields a kit can omit or set wrong — a mis-set PACS_ID 404s every DQR query.
    """
    for var in ("PACS_ID", "XNAT_URL", "DATA_ACCESS_API_URL", "XNAT_DATABASE_URL", "XNAT_DATASOURCE_PASSWORD"):
        monkeypatch.delenv(var, raising=False)

    settings = Settings()

    assert settings.PACS_ID == 1
    assert settings.XNAT_URL == "http://xnat-web:8080"
    assert settings.DATA_ACCESS_API_URL == "http://data-access-api:8000"
    assert settings.XNAT_DATABASE_URL == "postgresql+asyncpg://xnat:xnat@xnat-db:5432/xnat"


def test_minted_xnat_datasource_password_replaces_url_password(monkeypatch):
    """The kit's minted XNAT_DATASOURCE_PASSWORD must land in XNAT_DATABASE_URL
    (FLIP-PT-056): the URL default stays a non-secret topology constant while
    the real DB credential arrives via its own env var.
    """
    monkeypatch.delenv("XNAT_DATABASE_URL", raising=False)
    monkeypatch.setenv("XNAT_DATASOURCE_PASSWORD", "minted-secret")

    settings = Settings()

    assert settings.XNAT_DATABASE_URL == "postgresql+asyncpg://xnat:minted-secret@xnat-db:5432/xnat"


@pytest.mark.parametrize("value", ["", "<run-make-generate-xnat-credentials>"])
def test_unminted_xnat_datasource_password_leaves_url_untouched(monkeypatch, value):
    """Empty (kit omits it) or still the committed kit-template placeholder
    (mint skipped) must not touch the URL, so a pre-mint kit keeps working
    against a legacy weak-credential xnat-db instead of failing differently.
    """
    monkeypatch.delenv("XNAT_DATABASE_URL", raising=False)
    monkeypatch.setenv("XNAT_DATASOURCE_PASSWORD", value)

    settings = Settings()

    assert settings.XNAT_DATABASE_URL == "postgresql+asyncpg://xnat:xnat@xnat-db:5432/xnat"


def test_minted_password_with_url_special_chars_survives_round_trip(monkeypatch):
    """A password containing URL-reserved characters must be escaped into the
    URL such that parsing it back yields the original password — otherwise the
    engine would authenticate with a mangled credential.
    """
    monkeypatch.delenv("XNAT_DATABASE_URL", raising=False)
    monkeypatch.setenv("XNAT_DATASOURCE_PASSWORD", "p@ss:word/1")

    settings = Settings()

    assert make_url(settings.XNAT_DATABASE_URL).password == "p@ss:word/1"
