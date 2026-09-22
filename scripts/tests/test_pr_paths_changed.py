#!/usr/bin/env python3
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
"""Tests for the PR path gate: the policy in ``scripts/pr_paths_changed.py`` and the
wiring of every service test workflow that calls it.

Two halves. The first pins the decision itself — which events run, that a PR into
``main`` always runs, that a change to the gate re-runs every suite, and that the
pattern syntax is GitHub's (``*`` stops at ``/``, ``**`` does not) so a workflow's push
filter can be reused as its gate list without translation.

The second is a drift guard over the callers. Each gated workflow carries its path list
twice — once as the push ``paths:`` filter, once as the ``changes`` job input — because
GitHub offers no way to share the two. If they diverge, a PR into develop silently
skips a suite that a push to develop would run, which is exactly the class of gap the
gate exists to avoid. The guard also insists every other job in a gated workflow
``needs: changes`` and carries the ``if:``, since a job that forgets either simply runs
unconditionally and the gate looks like it works.

Deliberately stdlib-only and executable as a plain script — ``test_trust_kit_scripts.yml``
runs these with ``python <file>``, not pytest, and PyYAML is not installed there. The
workflow parsing is line-based and relies on the regular shape the gated workflows share;
a caller written differently enough to defeat it deserves a look anyway.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
GATE_WORKFLOW = "pr_paths_changed.yml"

# The service test workflows that must call the gate. A new service test workflow that
# filters its push trigger by path belongs here too.
GATED_WORKFLOWS = (
    "test_flip_ui.yml",
    "test_flip_api.yml",
    "test_trust_trust_api.yml",
    "test_trust_imaging_api.yml",
    "test_trust_data_access_api.yml",
    "test_trust_omop_db.yml",
    "test_trust_data_tools.yml",
)

GATE_PATHS = ["flip-ui/**", ".github/workflows/test_flip_ui.yml"]


def _load_gate():
    spec = importlib.util.spec_from_file_location("pr_paths_changed", REPO_ROOT / "scripts" / "pr_paths_changed.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


# --- the pattern syntax must be GitHub's, or the reused push list means something else ---


def test_double_star_spans_directories() -> None:
    rx = gate.glob_to_regex("flip-ui/**")
    assert rx.match("flip-ui/package.json")
    assert rx.match("flip-ui/src/pages/Home.vue")
    assert not rx.match("flip-uix/package.json")
    assert not rx.match("docs/flip-ui/README.md")


def test_single_star_stops_at_slash() -> None:
    rx = gate.glob_to_regex("deploy/compose*.yml")
    assert rx.match("deploy/compose.development.yml")
    assert not rx.match("deploy/sub/compose.development.yml")


def test_literal_path_matches_only_itself() -> None:
    rx = gate.glob_to_regex("trust/Makefile")
    assert rx.match("trust/Makefile")
    assert not rx.match("trust/Makefile.bak")
    assert not rx.match("trust/orthanc/Makefile")


def test_regex_metacharacters_are_literal() -> None:
    assert not gate.glob_to_regex("fl-apps/check_required_files.sh").match("fl-apps/check_required_filesXsh")


def test_negation_is_refused() -> None:
    try:
        gate.glob_to_regex("!flip-ui/**")
    except ValueError:
        return
    raise AssertionError("a negated pattern must be refused, not treated as a literal")


def test_parse_patterns_drops_blanks_and_comments() -> None:
    assert gate.parse_patterns("a/**\n\n# note\n  b.yml \n") == ["a/**", "b.yml"]


# --- the decision ---


def test_non_pull_request_events_run() -> None:
    for event in ("push", "workflow_dispatch", "schedule"):
        run, _ = gate.decide(event, "", None, GATE_PATHS)
        assert run, event


def test_pull_request_into_main_runs_regardless_of_files() -> None:
    run, reason = gate.decide("pull_request", "main", ["README.md"], GATE_PATHS)
    assert run
    assert "main" in reason


def test_pull_request_into_develop_runs_on_a_matching_file() -> None:
    run, reason = gate.decide("pull_request", "develop", ["docs/x.rst", "flip-ui/src/a.ts"], GATE_PATHS)
    assert run
    assert "flip-ui/src/a.ts" in reason


def test_pull_request_into_develop_skips_when_nothing_matches() -> None:
    run, _ = gate.decide("pull_request", "develop", ["flip-api/src/x.py", "AGENTS.md"], GATE_PATHS)
    assert not run


def test_empty_pull_request_skips() -> None:
    run, _ = gate.decide("pull_request", "develop", [], GATE_PATHS)
    assert not run


def test_touching_the_gate_runs_every_suite() -> None:
    for gate_file in gate.GATE_FILES:
        assert (REPO_ROOT / gate_file).is_file(), f"{gate_file} is listed in GATE_FILES but does not exist"
        run, reason = gate.decide("pull_request", "develop", [gate_file], GATE_PATHS)
        assert run, gate_file
        assert gate_file in reason, gate_file


# --- the callers' wiring ---


def _push_paths(text: str) -> list[str]:
    """The quoted entries of the push trigger's ``paths:`` list."""
    m = re.search(r"^  push:\n(?:    .*\n)*?    paths:\n((?:      .*\n)+)", text, re.M)
    if not m:
        return []
    return [ln.strip()[2:].strip().strip('"') for ln in m.group(1).splitlines() if ln.strip().startswith("- ")]


def _gate_paths(text: str) -> list[str]:
    """The lines of the ``changes`` job's ``paths: |`` block."""
    m = re.search(r"^  changes:\n(?:    .*\n)*?      paths: \|\n((?:        .*\n)+)", text, re.M)
    if not m:
        return []
    return [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]


def _jobs(text: str) -> dict[str, str]:
    """Top-level job name -> that job's body, for everything under ``jobs:``."""
    body = text.split("\njobs:\n", 1)[1]
    names = [(m.group(1), m.start()) for m in re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", body, re.M)]
    return {name: body[start : nxt[1]] for (name, start), nxt in zip(names, names[1:] + [(None, len(body))])}


def test_gate_workflow_is_reusable_only() -> None:
    text = (WORKFLOWS / GATE_WORKFLOW).read_text()
    assert re.search(r"^on:\n  workflow_call:", text, re.M), "the gate must be callable, not triggered"
    assert not re.search(r"^  (push|pull_request):", text, re.M), "the gate must not trigger on its own"
    assert "sparse-checkout: scripts/pr_paths_changed.py" in text


def test_every_gated_workflow_calls_the_gate_with_its_push_paths() -> None:
    for name in GATED_WORKFLOWS:
        text = (WORKFLOWS / name).read_text()
        push = _push_paths(text)
        assert push, f"{name}: push trigger has no paths: filter to mirror"
        assert f".github/workflows/{name}" in push, f"{name}: the push filter must include the workflow itself"
        assert _gate_paths(text) == push, f"{name}: changes job paths must equal the push paths: filter"
        assert re.search(r"^  pull_request:\n(?:    .*\n)*?    branches: \[main, develop\]", text, re.M), (
            f"{name}: PRs into main must be admitted"
        )
        assert not re.search(r"^  pull_request:\n(?:    .*\n)*?    paths:", text, re.M), (
            f"{name}: a paths: filter on pull_request would skip suites on PRs into main; the gate filters"
        )


def test_every_other_job_is_gated() -> None:
    for name in GATED_WORKFLOWS:
        jobs = _jobs((WORKFLOWS / name).read_text())
        assert "changes" in jobs, name
        assert f"uses: ./.github/workflows/{GATE_WORKFLOW}" in jobs["changes"], name
        assert "pull-requests: read" in jobs["changes"], f"{name}: the gate reads the PR's file list"
        for job, body in jobs.items():
            if job == "changes":
                continue
            assert "\n    needs: changes\n" in body, f"{name}/{job}: job must depend on the gate"
            assert "\n    if: needs.changes.outputs.run == 'true'\n" in body, f"{name}/{job}: must skip on run=false"


def test_scripts_test_workflow_triggers_on_gated_workflow_edits() -> None:
    """This guard asserts on workflow files, so edits to them must run it — the trap
    test_trust_kit_scripts.yml already documents for the compose files.

    A trigger satisfies that either by listing the paths or by carrying no ``paths:``
    filter at all, which is strictly broader. ``pull_request`` is unfiltered today
    (FLIP#1230) so every develop-targeted PR runs this ~1s job; the push trigger is
    filtered and must therefore name them.
    """
    text = (WORKFLOWS / "test_trust_kit_scripts.yml").read_text()
    for trigger in ("push", "pull_request"):
        m = re.search(rf"^  {trigger}:\n((?:    .*\n)+)", text, re.M)
        assert m, trigger
        block = m.group(1)
        if "    paths:\n" not in block:
            continue
        assert '".github/workflows/test_*.yml"' in block, trigger
        assert f'".github/workflows/{GATE_WORKFLOW}"' in block, trigger


TESTS = [obj for name, obj in sorted(globals().items()) if name.startswith("test_") and callable(obj)]


def main() -> None:
    passed = failed = 0
    for test in TESTS:
        try:
            test()
        except Exception:  # noqa: BLE001 — report every failure, not just the first
            print(f"  ❌ {test.__name__}")
            traceback.print_exc()
            failed += 1
        else:
            print(f"  ✅ {test.__name__}")
            passed += 1
    print("—")
    print(f"PASS={passed}  FAIL={failed}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
