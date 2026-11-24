# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``validate_terraform.yml`` runs on every file this suite reads (FLIP#2047).

That workflow is the only place this suite runs, and it is filtered by path on pushes and on pull
requests. Several guards here read files outside ``deploy/aws`` (a Dockerfile's ``USER``, a
Makefile, another workflow); while such a file was missing from the filter, a PR changing only
that file never ran the guard it could break, and the failure surfaced on the next unrelated
``deploy/aws`` PR, or on Fargate.

The list is derived from the test sources, not kept by hand. A static scan sees two things:

- a path built from ``Path(__file__)`` with ``/``, ``.parent`` and ``.parents[n]``, through
  plain name assignments (``REPO_ROOT / "deploy" / "env_mode.mk"``);
- a string literal with a ``/`` that names a tracked path from the repo root and nothing below
  ``deploy/aws`` (``"flip-api/Dockerfile"`` in a table a test later joins onto the root).

It does not see a name joined on at run time (``WORKFLOWS / workflow``), a root-level file named
without a ``/``, or what a Makefile a test runs goes on to include. Where the scan ends on such a
directory the test fails until ``BY_HAND`` says which files are read there. The one input no list
can name is ``test_env_mode.py``'s sweep of every tracked Makefile and script for an old ``HUB``
comparison.
"""

import ast
import importlib.util
import os
import subprocess
from pathlib import Path

import yaml

AWS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AWS_DIR.parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate_terraform.yml"
# GitHub's own filter syntax (`*` stops at `/`, `**` does not), as the PR path gate reads it.
_spec = importlib.util.spec_from_file_location("pr_paths_changed", REPO_ROOT / "scripts" / "pr_paths_changed.py")
GATE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(GATE)

#: Directories the scan cannot see through, and the files the tests read there, found by hand.
BY_HAND = {
    # test_env_mode.py reads the root Makefile by its bare name.
    ".": ("Makefile",),
    # test_ci_keypair_parameter.py loops over the three Terraform workflows.
    ".github/workflows": (
        ".github/workflows/terraform_plan.yml",
        ".github/workflows/terraform_apply.yml",
        ".github/workflows/terraform_drift.yml",
    ),
    # test_providers_forwarding.py runs make there.
    "deploy/providers/AWS": ("deploy/providers/AWS/GNUmakefile",),
    # test_trust_makefile_hub.py parses both Makefiles, which include the deploy/*.mk fragments.
    "trust": ("trust/Makefile", "deploy/instance.mk", "deploy/portable.mk"),
    "trust/xnat": ("trust/xnat/Makefile", "trust/xnat/.env"),
}


def _resolve(node: ast.AST, names: dict[str, Path], source: Path) -> Path | None:
    """The path ``node`` builds from ``Path(__file__)``, or None when it is not one the scan follows."""
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "Path" and [ast.unparse(arg) for arg in node.args] == ["__file__"]:
            return source
        if isinstance(func, ast.Attribute) and func.attr in {"resolve", "absolute"}:
            return _resolve(func.value, names, source)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        base = _resolve(node.value, names, source)
        return base.parent if base else None
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
        base = _resolve(node.value.value, names, source)
        if base and isinstance(node.slice, ast.Constant):
            return base.parents[node.slice.value]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        base = _resolve(node.left, names, source)
        if base and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            return Path(os.path.normpath(base / node.right.value))
    return None


def _outside_paths(source: Path) -> set[str]:
    """Repo-relative paths outside ``deploy/aws`` that the module ``source`` names."""
    tree = ast.parse(source.read_text())
    names: dict[str, Path] = {}
    # A path that only goes on to build a longer one, or to be given a name, is not where a read ends.
    prefixes = set()
    for node in sorted((n for n in ast.walk(tree) if isinstance(n, ast.Assign)), key=lambda n: n.lineno):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            prefixes.add(node.value)
            value = _resolve(node.value, names, source)
            if value:
                names[node.targets[0].id] = value
    for node in ast.walk(tree):
        if _resolve(node, names, source):
            prefixes.update(ast.iter_child_nodes(node))

    found = set()
    for node in ast.walk(tree):
        if node in prefixes:
            continue
        path = _resolve(node, names, source)
        if path is None and isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if "/" in text and not text.startswith("/") and not set(text) & set(" \n*"):
                path = Path(os.path.normpath(REPO_ROOT / text))
        if path is None or not path.exists() or not path.is_relative_to(REPO_ROOT):
            continue
        # The root and deploy/ are prefixes of everything here, not inputs in themselves.
        if path.is_relative_to(AWS_DIR) or AWS_DIR.is_relative_to(path):
            continue
        found.add(path.relative_to(REPO_ROOT).as_posix())
    return found


def _tracked(paths: set[str]) -> set[str]:
    """Only tracked paths can appear in a push or a pull request."""
    listed = subprocess.run(
        ["git", "ls-files", "--", *sorted(paths)], cwd=REPO_ROOT, text=True, capture_output=True, check=True
    ).stdout.split("\n")
    return {path for path in paths if path in listed or any(line.startswith(f"{path}/") for line in listed)}


SCANNED = _tracked(set().union(*map(_outside_paths, AWS_DIR.glob("tests/*.py"))))
DIRECTORIES = {path for path in SCANNED if (REPO_ROOT / path).is_dir()}
FILES = sorted((SCANNED - DIRECTORIES) | {path for paths in BY_HAND.values() for path in paths})
# `on:` is parsed as the boolean True by YAML 1.1.
TRIGGERS = yaml.safe_load(WORKFLOW.read_text())[True]
# The push list stands for both: the last test holds the pull_request one equal to it.
PATTERNS = TRIGGERS["push"]["paths"]


def _covered(path: str) -> bool:
    return GATE.first_match([path], PATTERNS) is not None


def test_the_scan_sees_both_kinds_of_reference() -> None:
    """A scan that silently found nothing would pass every test below."""
    assert "fl-services/nvflare/Makefile" in SCANNED  # built with `/` from the root
    assert "flip-api/Dockerfile" in SCANNED  # a string literal joined on later


def test_every_directory_the_scan_ends_on_is_accounted_for() -> None:
    unexplained = [path for path in sorted(DIRECTORIES) if path not in BY_HAND and not _covered(f"{path}/a/b")]
    assert not unexplained, (
        f"a test reads below {unexplained} by a name the scan cannot follow: "
        "list the files it reads there in BY_HAND, or cover the directory with `<dir>/**` in the workflow"
    )


def test_the_hand_list_names_real_files() -> None:
    stale = [path for paths in BY_HAND.values() for path in paths if not (REPO_ROOT / path).is_file()]
    assert not stale, f"BY_HAND names files that no longer exist: {stale}"


def test_the_workflow_runs_on_every_file_the_suite_reads() -> None:
    missing = [path for path in FILES if not _covered(path)]
    assert not missing, (
        f"validate_terraform.yml's paths do not list {missing}, which deploy/aws/tests reads: "
        "a change to one of them alone would not run this suite"
    )


def test_push_and_pull_request_filter_on_the_same_paths() -> None:
    assert TRIGGERS["push"]["paths"] == TRIGGERS["pull_request"]["paths"]
