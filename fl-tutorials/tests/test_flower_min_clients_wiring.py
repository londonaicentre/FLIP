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
# Drift guard: every Flower ServerApp must hand its strategy the participating-trust count.
#
# flwr's FedAvg defaults min_train_nodes / min_evaluate_nodes / min_available_nodes to 2, so an
# app that forgets never starts a round on a single-trust project — it waits for a second node
# until start()'s 3600s timeout, logging nothing. That silence is why this is a static guard
# rather than something left to review: the failure looks identical to "training is slow".
#
# It covers fl-apps/flower (the deployed templates) as well as fl-tutorials/flower, because both
# trees are copied as the starting point for new apps. It lives in this suite because that is the
# CPU-only pytest that already runs over the tutorial tree with no GPU, dataset or FL image.

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# flwr's own knobs, for apps that build on FedAvg directly rather than on a FLIP strategy.
NODE_THRESHOLD_KWARGS = {"min_train_nodes", "min_evaluate_nodes", "min_available_nodes"}

# Both trees ship Flower apps; a new app is nearly always a copy of one of these.
FLOWER_SERVER_APPS = sorted(
    path
    for tree in ("fl-apps", "fl-tutorials")
    for path in (REPO_ROOT / tree).glob("flower/*/app/server_app.py")
)


def _strategy_call(source: str) -> ast.Call | None:
    """Return the ``strategy = <Strategy>(...)`` call node, or None if there isn't one."""
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and any(isinstance(target, ast.Name) and target.id == "strategy" for target in node.targets)
        ):
            return node.value
    return None


def test_discovery_actually_finds_the_flower_server_apps():
    """Guard the guard: a glob that matches nothing would pass every assertion below."""
    assert FLOWER_SERVER_APPS, f"no Flower server_app.py found under {REPO_ROOT} — the glob has drifted"


@pytest.mark.parametrize("server_app", FLOWER_SERVER_APPS, ids=lambda p: p.parents[1].name)
def test_flower_server_app_passes_min_clients_to_its_strategy(server_app: Path):
    source = server_app.read_text()

    call = _strategy_call(source)
    assert call is not None, f"{server_app}: no `strategy = ...(...)` construction found"

    keywords = {keyword.arg for keyword in call.keywords}
    # FLIP's strategies take the single min_clients argument; an app built straight on flwr's
    # FedAvg has to set the three underlying thresholds itself. Either is fine — leaving them at
    # flwr's defaults is not.
    assert "min_clients" in keywords or NODE_THRESHOLD_KWARGS <= keywords, (
        f"{server_app}: strategy is constructed without min_clients (or the three min_*_nodes), "
        f"so it inherits flwr's min_*_nodes=2 and a single-trust run will hang silently until "
        f"start()'s timeout"
    )


@pytest.mark.parametrize("server_app", FLOWER_SERVER_APPS, ids=lambda p: p.parents[1].name)
def test_app_config_declares_flip_min_clients(server_app: Path):
    """The key must be declared in [tool.flwr.app.config], or every deployed run dies at submit.

    flwr rejects a --run-config override whose key the app config does not declare
    ("Key '<key>' is not present in the main dictionary"), so injecting flip-min-clients
    without declaring it turns a silent hang into a hard failure on every Flower run.
    """
    pyproject = server_app.parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text())["tool"]["flwr"]["app"]["config"]

    assert "flip-min-clients" in config, (
        f"{pyproject}: [tool.flwr.app.config] does not declare flip-min-clients, so fl-api's "
        f"injected override will be rejected by flwr"
    )


@pytest.mark.parametrize("server_app", FLOWER_SERVER_APPS, ids=lambda p: p.parents[1].name)
def test_min_clients_comes_from_the_injected_trust_count(server_app: Path):
    """The value must be the trust count fl-api injects, never a hardcoded constant.

    A literal is what the NVFLARE side had to fix: pinned at 1 the aggregator closes a round on
    the fastest trust and silently drops the slower ones; pinned high it never starts at all.
    """
    assert '"flip-min-clients"' in server_app.read_text(), (
        f"{server_app}: min_clients is not read from the injected `flip-min-clients` run-config key"
    )
