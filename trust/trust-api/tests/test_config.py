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
