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

"""A StatefulSet's volumeClaimTemplates never carry the chart's version.

Kubernetes refuses any change to that field, and every release stamps a new chart version (FLIP#1752).
With the common labels there, every upgrade of an installed trust failed at the omop-db, xnat-db and loki
StatefulSets while the Deployments moved on, leaving the trust half upgraded. The frozen labels are what
every StatefulSet installed before the chart was versioned carries, so existing trusts match them.

Text checks, as in ``test_chart_template_invariants.py``: ``helm template`` reaches only the branches its
values enable.
"""

import re
from pathlib import Path

CHART_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = CHART_DIR / "templates"
HELPERS = TEMPLATES_DIR / "_helpers.tpl"

# A claim template's metadata block: from `volumeClaimTemplates:` to the first `spec:` under it.
CLAIM_TEMPLATE = re.compile(r"volumeClaimTemplates:(.*?)\n\s+spec:", re.S)


def test_every_claim_template_uses_the_frozen_labels() -> None:
    blocks = [
        (path.name, block)
        for path in TEMPLATES_DIR.glob("*.yaml")
        for block in CLAIM_TEMPLATE.findall(path.read_text())
    ]
    assert blocks, "no volumeClaimTemplates found: the pattern no longer matches the templates"
    for name, block in blocks:
        assert '"flip-trust.labels"' not in block, f"{name}: a claim template carries the chart's version"


def test_frozen_labels_are_what_installed_trusts_carry() -> None:
    definition = '{{- define "flip-trust.claimTemplateLabels" -}}'
    helper = HELPERS.read_text().split(definition, 1)[1].split("{{- end }}", 1)[0]
    assert "helm.sh/chart: flip-trust-0.1.0" in helper
    assert 'app.kubernetes.io/version: "1.0"' in helper
    assert ".Chart.Version" not in helper
    assert ".Chart.AppVersion" not in helper
