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
# Drift guard: every FLIP Flower app must take its node thresholds from the trust count.
#
# flwr's FedAvg defaults min_train_nodes / min_evaluate_nodes / min_available_nodes to 2, so an
# app that forgets never starts a round on a single-trust project — it waits for a second node
# until start()'s 3600s timeout, logging nothing. That silence is why this is a static guard
# rather than something left to review: the failure looks identical to "training is slow".
#
# Scope. It covers fl-apps/flower (the templates that actually deploy — flip-api discards an
# uploaded server_app.py) and fl-tutorials/flower. Apps whose pyproject declares no flip-model-id
# are skipped as a matter of definition: nothing injects run-config into them, so requiring the
# key would be wrong rather than merely noisy. Note scripts/check_tutorial_sync.sh already pins
# most tutorial server_app.py files byte-identical to their fl-apps template, but it deliberately
# does NOT pair pyproject.toml — so the declaration arm below is the only guard on that half.

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest
from tutorial_apps import TUTORIALS_ROOT

REPO_ROOT = TUTORIALS_ROOT.parent

# The reader every FLIP Flower app must route through; see flip.flower.strategy.
READER = "min_clients_from_run_config"


def _flip_flower_apps() -> list[Path]:
    """Every Flower server_app.py that FLIP injects run-config into, newest-first by path."""
    apps = []
    for tree in ("fl-apps", "fl-tutorials"):
        for server_app in (REPO_ROOT / tree).glob("flower/*/app/server_app.py"):
            pyproject = server_app.parents[1] / "pyproject.toml"
            config = tomllib.loads(pyproject.read_text()).get("tool", {}).get("flwr", {}).get("app", {}).get(
                "config", {}
            )
            # flip-model-id marks an app on the FLIP deploy path.
            if "flip-model-id" in config:
                apps.append(server_app)
    return sorted(apps)


FLIP_FLOWER_APPS = _flip_flower_apps()


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


def test_discovery_actually_finds_the_flip_flower_apps():
    """Guard the guard: an empty parametrize list makes pytest skip, not fail."""
    assert FLIP_FLOWER_APPS, f"no FLIP Flower server_app.py found under {REPO_ROOT} — the glob has drifted"


@pytest.mark.parametrize("server_app", FLIP_FLOWER_APPS, ids=lambda p: p.parents[1].name)
def test_strategy_gets_min_clients_from_the_injected_trust_count(server_app: Path):
    """The strategy must receive min_clients, and receive it from the run-config reader.

    Asserting the argument's *value* is the reader call — rather than that the key appears
    somewhere in the file — is what stops a literal (`min_clients=1`) or a dead read from
    satisfying this. A constant is the specific mistake worth catching: pinned low, a round
    closes on whichever trust replies first and the slower ones are silently dropped.
    """
    call = _strategy_call(server_app.read_text())
    assert call is not None, f"{server_app}: no `strategy = ...(...)` construction found"

    keyword = next((k for k in call.keywords if k.arg == "min_clients"), None)
    assert keyword is not None, (
        f"{server_app}: strategy is constructed without min_clients, so it inherits flwr's "
        f"min_*_nodes=2 and a single-trust run will hang silently until start()'s timeout"
    )
    assert isinstance(keyword.value, ast.Call), (
        f"{server_app}: min_clients is a literal, not a call — a constant leaves the threshold "
        f"unrelated to the participating-trust count"
    )
    assert getattr(keyword.value.func, "id", None) == READER, (
        f"{server_app}: min_clients does not come from {READER}(run_config), so the value passed "
        f"to the strategy is not the injected trust count"
    )


@pytest.mark.parametrize("server_app", FLIP_FLOWER_APPS, ids=lambda p: p.parents[1].name)
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
    # Nothing injects on the simulator / submit_tutorial paths, so the declared placeholder is
    # the effective value there; anything below flwr's own default would quietly shrink the quorum.
    assert config["flip-min-clients"] >= 2, (
        f"{pyproject}: declared flip-min-clients={config['flip-min-clients']} is below flwr's "
        f"default of 2, which loosens the quorum wherever nothing injects the trust count"
    )
