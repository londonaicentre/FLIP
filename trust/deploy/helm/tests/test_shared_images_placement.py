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
volume until the rollout timed out. The chart now adds the label, the affinity and Recreate itself,
only while the volume is RWO; tests/templates/test_shared_images_render.py checks the renders.
Text-level, like the other chart tests.
"""

from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[1]
TEMPLATES = CHART_DIR / "templates"
VALUES = yaml.safe_load((CHART_DIR / "values.yaml").read_text())


@pytest.mark.parametrize("template", ["imaging-api.yaml", "fl-client.yaml"])
def test_both_pods_take_their_affinity_through_the_shared_images_helper(template):
    assert 'include "flip-trust.sharedImagesAffinity"' in (TEMPLATES / template).read_text()


@pytest.mark.parametrize("component", ["imagingApi", "flClient"])
def test_values_leave_the_shared_images_term_to_the_chart(component):
    """A term in values would survive an RWX switch, the case the chart now drops it for."""
    assert "podAffinity" not in (VALUES[component]["affinity"] or {})
