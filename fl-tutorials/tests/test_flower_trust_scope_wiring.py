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
# Drift guard: every FLIP Flower app must scope its run to the trusts it was approved for.
#
# A Flower net is shared by every trust onboarded onto it, and flwr's FedAvg samples from every
# connected SuperNode (grid.get_node_ids() in flwr.serverapp.strategy.fedavg). An app that does
# not pass `trusts` therefore trains and evaluates on trusts whose projects never included this
# run — silently, since the rounds succeed and the extra sites simply appear in the per-client
# metrics. The FLARE adapter has never had the gap (deploy_map / mandatory_clients), which is
# why this is a static guard rather than something left to review (FLIP#1743, FLIP-SR-048).
#
# Scope, as for the min_clients guard beside it: fl-apps/flower (the templates that actually
# deploy — flip-api discards an uploaded server_app.py) and fl-tutorials/flower. Apps whose
# pyproject declares no flip-model-id are skipped by definition: nothing injects run-config
# into them.

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest
from flip.flower.scope import TRUSTS_KEY, trusts_from_run_config
from tutorial_apps import TUTORIALS_ROOT

REPO_ROOT = TUTORIALS_ROOT.parent

# Imported, not spelled out: a rename of either symbol must fail this guard rather than leave
# the apps reading a key nobody writes (which would silently restore "every connected node").
READER = trusts_from_run_config.__name__
TREE_GLOBS = {"fl-apps": "flower/*/app/server_app.py", "fl-tutorials": "flower/*/*/app/server_app.py"}
TREES = tuple(TREE_GLOBS)


def _flip_flower_apps() -> list[Path]:
    """Every Flower server_app.py that FLIP injects run-config into, sorted for stable test ids."""
    apps = []
    for tree, pattern in TREE_GLOBS.items():
        for server_app in (REPO_ROOT / tree).glob(pattern):
            pyproject = server_app.parents[1] / "pyproject.toml"
            config = (
                tomllib.loads(pyproject.read_text()).get("tool", {}).get("flwr", {}).get("app", {}).get("config", {})
            )
            if "flip-model-id" in config:  # marks an app on the FLIP deploy path
                apps.append(server_app)
    return sorted(apps)


FLIP_FLOWER_APPS = _flip_flower_apps()


def _is_reader(func: ast.expr) -> bool:
    """True for `trusts_from_run_config(...)`, plain or module-qualified."""
    return getattr(func, "id", None) == READER or getattr(func, "attr", None) == READER


def _reader_call(value: ast.expr, tree: ast.Module) -> ast.Call | None:
    """Resolve the trusts argument to the reader call behind it, direct or via a local."""
    if isinstance(value, ast.Call) and _is_reader(value.func):
        return value
    if isinstance(value, ast.Name):  # apps assign it first, inside their try/except
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == value.id for t in node.targets)
                and isinstance(node.value, ast.Call)
                and _is_reader(node.value.func)
            ):
                return node.value
    return None


def _strategy_call_in(tree: ast.Module) -> ast.Call | None:
    """Return the ``strategy = <Strategy>(...)`` call node from ``tree``, or None if absent."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and any(isinstance(target, ast.Name) and target.id == "strategy" for target in node.targets)
        ):
            return node.value
    return None


def _handler_sets_error(handler: ast.ExceptHandler) -> bool:
    """True if an except block reports the failure as a model status of ERROR."""
    for node in ast.walk(handler):
        if isinstance(node, ast.Attribute) and node.attr == "ERROR":
            return True
    return False


def _guarded_by_value_error(node: ast.AST, tree: ast.Module) -> bool:
    """True if ``node`` sits inside a ``try`` whose ValueError handler sets the model to ERROR."""
    for candidate in ast.walk(tree):
        if not isinstance(candidate, ast.Try):
            continue
        if not any(body_node is node or node in ast.walk(body_node) for body_node in candidate.body):
            continue
        for handler in candidate.handlers:
            catches = handler.type
            names = [catches] if not isinstance(catches, ast.Tuple) else list(catches.elts)
            if any(getattr(name, "id", None) == "ValueError" for name in names) and _handler_sets_error(handler):
                return True
    return False


def test_discovery_actually_finds_the_flip_flower_apps():
    """Guard the guard: an empty parametrize list makes pytest skip, not fail."""
    assert FLIP_FLOWER_APPS, f"no FLIP Flower server_app.py found under {REPO_ROOT} — the glob has drifted"
    covered = {path.relative_to(REPO_ROOT).parts[0] for path in FLIP_FLOWER_APPS}
    assert covered == set(TREES), f"only {sorted(covered)} covered — a tree moved or the glob depth is wrong"


def test_fl_api_writes_the_key_the_apps_read():
    """The writer lives in another package and cannot import TRUSTS_KEY, so pin the literal."""
    upload = REPO_ROOT / "fl-services/flower/fl-api-flower/fl_api/utils/upload.py"
    assert f'"{TRUSTS_KEY}"' in upload.read_text(), (
        f"{upload} does not write {TRUSTS_KEY!r} — the reader and writer have drifted apart, so "
        f"every run would fall back to sampling every SuperNode on the net"
    )


@pytest.mark.parametrize("server_app", FLIP_FLOWER_APPS, ids=lambda p: p.parents[1].name)
def test_strategy_gets_the_approved_trusts_from_the_run_config(server_app: Path):
    """The strategy must receive ``trusts``, and receive it from the run-config reader.

    Asserting the argument's *value* is the reader call — rather than that the key appears
    somewhere in the file — is what stops a hard-coded list or a dead read from satisfying
    this. A hard-coded list is the specific mistake worth catching: it would scope every run
    on that app to the same trusts regardless of the project it was approved for.
    """
    source = server_app.read_text()
    tree = ast.parse(source)
    call = _strategy_call_in(tree)
    assert call is not None, f"{server_app}: no `strategy = ...(...)` construction found"

    keyword = next((k for k in call.keywords if k.arg == "trusts"), None)
    assert keyword is not None, (
        f"{server_app}: strategy is constructed without trusts, so flwr samples every SuperNode "
        f"connected to the net — including trusts outside this run's project"
    )
    reader_call = _reader_call(keyword.value, tree)
    assert reader_call is not None, (
        f"{server_app}: trusts is not {READER}(...) — directly or via a local bound to it. "
        f"A literal list scopes every run on this app to the same trusts"
    )
    argument = reader_call.args[0] if reader_call.args else None
    assert getattr(argument, "id", None) == "run_config" or getattr(argument, "attr", None) == "run_config", (
        f"{server_app}: {READER} is not called on run_config, so the injected trust list is never read"
    )


@pytest.mark.parametrize("server_app", FLIP_FLOWER_APPS, ids=lambda p: p.parents[1].name)
def test_a_malformed_trust_list_reaches_the_researcher_as_error(server_app: Path):
    """Reading it must fail the model, not just the process.

    ``trusts_from_run_config`` raises on a value that names no trust, because the alternative
    is a silently unscoped run. An unhandled raise leaves the model at its last status and the
    net BUSY; ERROR is the only channel the researcher can see, the ServerApp log stream not
    being surfaced by the platform.
    """
    tree = ast.parse(server_app.read_text())
    call = _strategy_call_in(tree)
    assert call is not None, f"{server_app}: no `strategy = ...(...)` construction found"
    keyword = next((k for k in call.keywords if k.arg == "trusts"), None)
    assert keyword is not None, f"{server_app}: strategy is constructed without trusts"
    reader_call = _reader_call(keyword.value, tree)
    assert reader_call is not None, f"{server_app}: trusts does not come from {READER}"

    assert _guarded_by_value_error(reader_call, tree), (
        f"{server_app}: the {READER} call is not inside a try whose ValueError handler sets the "
        f"model to ERROR, so a malformed {TRUSTS_KEY} would strand the model and the net"
    )


@pytest.mark.parametrize("server_app", FLIP_FLOWER_APPS, ids=lambda p: p.parents[1].name)
def test_app_config_declares_flip_trusts(server_app: Path):
    """The key must be declared in [tool.flwr.app.config], or every deployed run dies at submit.

    flwr rejects a --run-config override whose key the app config does not declare
    ("Key '<key>' is not present in the main dictionary"), so injecting flip-trusts without
    declaring it would fail every Flower run on that app.
    """
    pyproject = server_app.parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text())["tool"]["flwr"]["app"]["config"]

    assert TRUSTS_KEY in config, (
        f"{pyproject}: [tool.flwr.app.config] does not declare {TRUSTS_KEY}, so fl-api's "
        f"injected override will be rejected by flwr"
    )
    declared = config[TRUSTS_KEY]
    assert isinstance(declared, str), (
        f"{pyproject}: {TRUSTS_KEY}={declared!r} is not a string; the value is a comma-separated "
        f"list of slot names and a run config cannot carry a TOML array"
    )
    # Nothing injects on the simulator / submit_tutorial paths, where no SuperNode carries a
    # registered trust name — a declared default naming trusts there would match no node at all
    # and hang the run in flwr's unbounded sample_nodes wait.
    assert declared == "", f"{pyproject}: {TRUSTS_KEY} must default to the empty string, got {declared!r}"


@pytest.mark.parametrize("server_app", FLIP_FLOWER_APPS, ids=lambda p: p.parents[1].name)
def test_the_declared_default_reads_back_as_unscoped(server_app: Path):
    """Control: the placeholder each app declares is what the reader calls "no list"."""
    config = tomllib.loads((server_app.parents[1] / "pyproject.toml").read_text())["tool"]["flwr"]["app"]["config"]

    assert trusts_from_run_config(config) is None
