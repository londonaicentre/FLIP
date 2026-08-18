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
# app that forgets never starts a round on a single-trust project. sample_nodes polls in an
# UNBOUNDED sleep(1) loop, so the job hangs for good rather than timing out; the only trace is
# flwr's per-second "Waiting for nodes to connect" INFO line in the ServerApp log, which the
# platform does not surface. From the UI it is indistinguishable from slow training — which is
# why this is a static guard rather than something left to review.
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
from flip.flower.strategy import MIN_CLIENTS_KEY, min_clients_from_run_config
from tutorial_apps import TUTORIALS_ROOT

REPO_ROOT = TUTORIALS_ROOT.parent

# Imported, not spelled out: a rename of either symbol must fail this guard rather than leave the
# apps reading a key nobody writes (which would silently restore flwr's defaults).
READER = min_clients_from_run_config.__name__
TREES = ("fl-apps", "fl-tutorials")


def _flip_flower_apps() -> list[Path]:
    """Every Flower server_app.py that FLIP injects run-config into, sorted for stable test ids."""
    apps = []
    for tree in TREES:
        for server_app in (REPO_ROOT / tree).glob("flower/*/app/server_app.py"):
            pyproject = server_app.parents[1] / "pyproject.toml"
            config = (
                tomllib.loads(pyproject.read_text()).get("tool", {}).get("flwr", {}).get("app", {}).get("config", {})
            )
            # flip-model-id marks an app on the FLIP deploy path.
            if "flip-model-id" in config:
                apps.append(server_app)
    return sorted(apps)


FLIP_FLOWER_APPS = _flip_flower_apps()


def _is_reader(func: ast.expr) -> bool:
    """True for `min_clients_from_run_config(...)`, plain or module-qualified."""
    return getattr(func, "id", None) == READER or getattr(func, "attr", None) == READER


def _reader_call(value: ast.expr, tree: ast.Module) -> ast.Call | None:
    """Resolve the min_clients argument to the reader call behind it, direct or via a local."""
    if isinstance(value, ast.Call) and _is_reader(value.func):
        return value
    if isinstance(value, ast.Name):  # apps assign it first so the parse sits inside their try/except
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == value.id for t in node.targets)
                and isinstance(node.value, ast.Call)
                and _is_reader(node.value.func)
            ):
                return node.value
    return None


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
    """Guard the guard: an empty parametrize list makes pytest skip, not fail.

    Asserted per tree, not just non-empty: fl-apps/flower holds the templates that actually
    deploy, and a whole-tree miss there would still leave the tutorials green.
    """
    assert FLIP_FLOWER_APPS, f"no FLIP Flower server_app.py found under {REPO_ROOT} — the glob has drifted"
    covered = {path.relative_to(REPO_ROOT).parts[0] for path in FLIP_FLOWER_APPS}
    assert covered == set(TREES), f"only {sorted(covered)} covered — a tree moved or the glob depth is wrong"


def test_fl_api_writes_the_key_the_apps_read():
    """The writer lives in another package and cannot import MIN_CLIENTS_KEY, so pin the literal.

    Without this the seam is unguarded: renaming the constant keeps every other test green while
    production reads a key nobody writes, and the thresholds fall back to flwr's defaults.
    """
    upload = REPO_ROOT / "fl-services/flower/fl-api-flower/fl_api/utils/upload.py"
    assert f'"{MIN_CLIENTS_KEY}"' in upload.read_text(), (
        f"{upload} does not write {MIN_CLIENTS_KEY!r} — the reader and writer have drifted apart"
    )


@pytest.mark.parametrize("server_app", FLIP_FLOWER_APPS, ids=lambda p: p.parents[1].name)
def test_strategy_gets_min_clients_from_the_injected_trust_count(server_app: Path):
    """The strategy must receive min_clients, and receive it from the run-config reader.

    Asserting the argument's *value* is the reader call — rather than that the key appears
    somewhere in the file — is what stops a literal (`min_clients=1`) or a dead read from
    satisfying this. A constant is the specific mistake worth catching: pinned low, a round
    starts before every participating trust has connected, so the absent ones are never
    sampled and sit the round out — training on part of the federation without saying so.
    """
    call = _strategy_call(server_app.read_text())
    assert call is not None, f"{server_app}: no `strategy = ...(...)` construction found"

    tree = ast.parse(server_app.read_text())
    keyword = next((k for k in call.keywords if k.arg == "min_clients"), None)
    assert keyword is not None, (
        f"{server_app}: strategy is constructed without min_clients, so it inherits flwr's "
        f"min_*_nodes=2 and a single-trust run hangs indefinitely in sample_nodes"
    )
    reader_call = _reader_call(keyword.value, tree)
    assert reader_call is not None, (
        f"{server_app}: min_clients is not {READER}(...) — directly or via a local bound to it. "
        f"A literal or an unrelated value leaves the threshold unrelated to the trust count"
    )
    # The reader takes whatever mapping it is handed; context.node_config sits right beside
    # run_config and would return None, restoring flwr's defaults with the wiring looking correct.
    argument = reader_call.args[0] if reader_call.args else None
    assert getattr(argument, "id", None) == "run_config" or getattr(argument, "attr", None) == "run_config", (
        f"{server_app}: {READER} is not called on run_config, so the injected trust count is never read"
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

    assert MIN_CLIENTS_KEY in config, (
        f"{pyproject}: [tool.flwr.app.config] does not declare {MIN_CLIENTS_KEY}, so fl-api's "
        f"injected override will be rejected by flwr"
    )
    # Nothing injects on the simulator / submit_tutorial paths, so the declared placeholder is
    # the effective value there; anything below flwr's own default would quietly shrink the quorum.
    declared = config[MIN_CLIENTS_KEY]
    # bool is an int subclass, so it has to be excluded separately.
    assert not isinstance(declared, bool), f"{pyproject}: {MIN_CLIENTS_KEY}={declared!r} is a bool, not an integer"
    assert isinstance(declared, int), (
        f"{pyproject}: {MIN_CLIENTS_KEY}={declared!r} is not a TOML integer; a float would be truncated"
    )
    assert declared >= 2, (
        f"{pyproject}: declared {MIN_CLIENTS_KEY}={declared} is below flwr's "
        f"default of 2, which loosens the quorum wherever nothing injects the trust count"
    )
