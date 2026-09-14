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

"""Unit tests for scripts/generate_values.py's env → Helm-values mapping, for the one
value whose absence the chart refuses to render: flClient.kitHostPath (FLIP#965/#1009).

generate_values.py and sync_k8s_kit.py both turn a kit file into a Helm override, so they
must agree on what a kit without FL_KIT_DIR means — the canonical /opt/flip/fl-kit that
every shipped kit sets and the default `make stage-kit` writes to — rather than one
falling back and the other dropping the key.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_values.py"
_spec = importlib.util.spec_from_file_location("generate_values", _SCRIPT)
generate_values = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generate_values)


_KIT = {
    "TRUST_NAME": "Kubernetes Trust",
    "TRUST_NUMBER": "2",
    "CENTRAL_HUB_API_URL": "https://stag.flip.aicentre.co.uk/api",
    "FL_BACKEND": "Flower",
    "FL_KIT_DIR": "/srv/kits/Trust_K8s",
}


def kit_host_path(env):
    overrides, _secrets = generate_values.build_values(env)
    return overrides["flClient"]["kitHostPath"]


def test_kit_host_path_comes_from_fl_kit_dir():
    assert kit_host_path(_KIT) == "/srv/kits/Trust_K8s"


def test_kit_host_path_falls_back_to_the_canonical_path_when_fl_kit_dir_is_absent():
    """The chart's `required` guard would otherwise refuse the render for a kit that
    simply never carried the field — the same default sync_k8s_kit.py renders."""
    kit = {k: v for k, v in _KIT.items() if k != "FL_KIT_DIR"}
    assert kit_host_path(kit) == generate_values.DEFAULT_KIT_HOST_PATH == "/opt/flip/fl-kit"


def test_kit_host_path_falls_back_when_fl_kit_dir_is_blank():
    """A commented-out or cleared value must not surface as an empty path either."""
    assert kit_host_path({**_KIT, "FL_KIT_DIR": "   "}) == "/opt/flip/fl-kit"


def test_fallback_does_not_touch_the_other_mapped_values():
    overrides, _secrets = generate_values.build_values({k: v for k, v in _KIT.items() if k != "FL_KIT_DIR"})
    assert overrides["flBackend"] == "flower"
    assert overrides["trustNumber"] == "2"
    assert overrides["trustApi"]["env"]["CENTRAL_HUB_API_URL"] == _KIT["CENTRAL_HUB_API_URL"]


def test_both_override_generators_share_the_fallback():
    """sync_k8s_kit.render_override is the other writer of flClient.kitHostPath."""
    sync_spec = importlib.util.spec_from_file_location("sync_k8s_kit", Path(__file__).resolve().parents[1] / "sync_k8s_kit.py")
    sync_k8s_kit = importlib.util.module_from_spec(sync_spec)
    sync_spec.loader.exec_module(sync_k8s_kit)
    out = sync_k8s_kit.render_override({"FL_BACKEND": "nvflare"}, "Trust_K8s", "eu-west-2")
    assert f"\nflClient:\n  kitHostPath: {generate_values.DEFAULT_KIT_HOST_PATH}\n" in out
