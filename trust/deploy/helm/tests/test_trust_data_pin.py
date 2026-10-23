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

"""The chart seeds the same trust-data version as every other deployment.

``trust/.data_version`` is the pin Compose, Ansible and the dataset tooling read; the chart carries its
own copy in ``trustData.version``. A bump that misses the chart leaves Kubernetes trusts seeding the
previous version with nothing to say so.
"""

from pathlib import Path

import yaml

CHART_DIR = Path(__file__).resolve().parents[1]
DATA_VERSION = CHART_DIR.parents[1] / ".data_version"


def test_chart_trust_data_version_matches_the_repo_pin():
    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text())
    assert values["trustData"]["version"] == DATA_VERSION.read_text().strip()


def test_chart_readme_documents_the_pinned_version():
    readme = (CHART_DIR / "README.md").read_text()
    assert f"| `trustData.version` | `{DATA_VERSION.read_text().strip()}` |" in readme
