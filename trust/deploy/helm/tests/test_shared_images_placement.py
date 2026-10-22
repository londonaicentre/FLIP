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

"""imaging-api and fl-client stay on one node through restarts (FLIP#1439).

They share sharedImagesPvc, ReadWriteOnce by default. The fl-client used to follow imaging-api, but
nothing held imaging-api: on a two-node EKS cluster ``patch-kit-secrets`` restarted it, the surge pod
went to the other node (its own anti-affinity and the full node sent it there) and waited on the
volume until the rollout timed out. Text-level, like the other chart tests.
"""

from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[1]
TEMPLATES = CHART_DIR / "templates"
VALUES = yaml.safe_load((CHART_DIR / "values.yaml").read_text())
SHARED = {"flip/shared-images": "true"}


@pytest.mark.parametrize("template", ["imaging-api.yaml", "fl-client.yaml"])
def test_both_pods_carry_the_shared_label(template):
    assert 'flip/shared-images: "true"' in (TEMPLATES / template).read_text()


@pytest.mark.parametrize("component", ["imagingApi", "flClient"])
def test_both_pods_require_a_node_with_the_shared_label(component):
    """Each follows the other; at install the first matches its own term and goes anywhere."""
    terms = VALUES[component]["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
    assert {"labelSelector": {"matchLabels": SHARED}, "topologyKey": "kubernetes.io/hostname"} in terms


@pytest.mark.parametrize("template", ["imaging-api.yaml", "fl-client.yaml"])
def test_both_are_recreated_while_the_volume_is_read_write_once(template):
    text = (TEMPLATES / template).read_text()
    guard = '{{- if and .Values.sharedImagesPvc.enabled (eq .Values.sharedImagesPvc.accessMode "ReadWriteOnce") }}'
    assert guard in text
    assert "type: Recreate" in text.split(guard, 1)[1].split("{{- end }}", 1)[0]
