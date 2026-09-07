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

"""Drift guard: the committed fl-apps templates must equal their recipes' export output.

Production deploys the *committed* JSONs (the ``fl-apps/`` tree baked into the flip-api image),
while every other unit test exercises the *recipes* — without this guard, a template whose
aggregator silently regained ``expected_data_kind: "WEIGHTS"`` (or drifted from its recipe in any
other way) would pass the whole suite while every live client contribution is rejected and the
"trained" model never moves off round 0. Regenerate with each template's ``recipe.py`` driver
(see its module docstring) and commit the result whenever a recipe changes.

Comparison is on parsed JSON trees, so it is key-order-insensitive, with one canonicalisation:
the filter entries' ``tasks`` lists are set-derived inside NVFLARE, so their order follows the
interpreter's hash seed (the reason the drivers pin ``PYTHONHASHSEED=0`` for byte-stable committed
files) — they are sorted on both sides before comparing. Every other ordering (workflows, filter
chains) is semantic and compared exactly.
"""

import json
from pathlib import Path

import pytest

from flip.nvflare.recipes import FlipDiffusionRecipe, FlipEvalRecipe, FlipFedAvgRecipe, FlipFedOptRecipe

_FL_APPS_NVFLARE = Path(__file__).resolve().parents[5] / "fl-apps" / "nvflare"

_TEMPLATES = [
    ("standard", FlipFedAvgRecipe),
    ("evaluation", FlipEvalRecipe),
    ("fed_opt", FlipFedOptRecipe),
    ("diffusion_model", FlipDiffusionRecipe),
]


def _canonicalise(node):
    """Sort the hash-seed-ordered ``tasks`` lists in place; leave every other ordering exact."""
    if isinstance(node, dict):
        return {
            key: (sorted(value) if key == "tasks" and isinstance(value, list) else _canonicalise(value))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_canonicalise(item) for item in node]
    return node


@pytest.mark.parametrize(("template", "recipe_cls"), _TEMPLATES)
@pytest.mark.parametrize("config_name", ["config_fed_server.json", "config_fed_client.json", "meta.json"])
def test_committed_template_matches_recipe_export(tmp_path: Path, template: str, recipe_cls, config_name: str):
    recipe = recipe_cls()
    recipe.export(tmp_path)
    rel_path = "meta.json" if config_name == "meta.json" else f"app/config/{config_name}"
    exported = tmp_path / recipe.job.name / rel_path
    committed = _FL_APPS_NVFLARE / template / rel_path

    assert committed.is_file(), f"committed template file missing: {committed}"
    exported_tree = _canonicalise(json.loads(exported.read_text()))
    committed_tree = _canonicalise(json.loads(committed.read_text()))
    assert exported_tree == committed_tree, (
        f"fl-apps/nvflare/{template}/{config_name} has drifted from {recipe_cls.__name__}'s export — "
        f"regenerate it with fl-apps/nvflare/{template}/recipe.py and commit the result"
    )


@pytest.mark.parametrize(("template", "recipe_cls"), _TEMPLATES)
def test_committed_aggregator_keeps_the_stock_weight_diff_default(template: str, recipe_cls):
    """The dice-0.006 regression class, pinned on the DEPLOYED artefact: no committed template may
    override the aggregator's stock WEIGHT_DIFF expectation (the evaluation template wires no
    aggregator at all — asserted as absence)."""
    server_cfg = json.loads((_FL_APPS_NVFLARE / template / "app/config/config_fed_server.json").read_text())
    aggregators = [c for c in server_cfg.get("components", []) if c.get("id") == "aggregator"]
    for component in aggregators:
        assert "expected_data_kind" not in component.get("args", {}), (
            f"fl-apps/nvflare/{template}: aggregator overrides expected_data_kind — client diffs "
            "would be rejected every round"
        )
    if template != "evaluation":
        assert aggregators, f"fl-apps/nvflare/{template}: no aggregator component found"
