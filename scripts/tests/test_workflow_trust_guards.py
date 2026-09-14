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
"""Guards on the trust boundary of the ``workflow_run``-triggered image builds (FLIP#882).

A ``workflow_run`` event always fires in the base repository, in the trusted context, with
whatever ``permissions:`` the job asks for — here ``packages: write`` to ghcr.io/londonaicentre.
What it carries is the *triggering* run's ``head_repository`` and ``head_sha``, and for a fork
pull request those are the fork's. A job that checks that pair out and builds it has handed a
fork the org's registry credential, and nothing else in the file stops it: the ``branches:``
filter matches the triggering run's head branch (a fork branch named ``develop`` passes), the
test workflow that has to succeed first runs from the PR head (the fork controls it), and the
``github.repository == 'londonaicentre/FLIP'`` gate is true for every workflow_run event by
construction. The one guard that closes it is requiring the triggering run to have come from
this repository — ``github.event.workflow_run.head_repository.full_name == github.repository``
— at the JOB level, before the checkout runs.

Each assertion pins one way that guard has been, or could be, lost:

- Every workflow that triggers on ``workflow_run`` and checks out ``head_repository``/``head_sha``
  must carry the same-repo guard in a job-level ``if:``. This is the finding itself, present in
  five sibling workflows at once because ``docker_build_omop_db.yml`` was "a faithful copy of the
  existing pattern" — the pattern propagates by copy-paste, so the guard has to be checked on
  every file, not remembered per file.
- A workflow that does NOT trigger on ``workflow_run`` must not reference those fields in a
  checkout at all. ``docker_build_orthanc.yml`` did — dead on the push/pull_request events it
  actually receives, but the exact fork-controlled checkout, waiting for someone to add the
  trigger and re-open the hole with no guard in front of it.
- At least one such workflow must exist, so a rename or a parser miss fails loudly instead of
  passing an empty set.

Deliberately stdlib-only and executable as a plain script — ``test_trust_kit_scripts.yml`` runs
these with ``python <file>``, not pytest, and PyYAML is not installed there. The parsing is
line-based: it needs only the trigger list, the job-level ``if:`` text, and the checkout inputs,
and a workflow that defeats it would be unusual enough to warrant a look anyway.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# The expression that closes FLIP#882. Matched on a comment-stripped, single line so the
# folded `if: >-` block and the explanatory comment above it cannot satisfy it by accident.
SAME_REPO_GUARD = "github.event.workflow_run.head_repository.full_name == github.repository"

# A checkout input that hands the job the triggering run's code. Either field alone is enough
# to be the fork-controlled half of the pattern.
FORK_CONTROLLED_CHECKOUT = re.compile(
    r"^\s*(repository|ref):\s*\$\{\{.*github\.event\.workflow_run\.(head_repository|head_sha)"
)

TRIGGER_WORKFLOW_RUN = re.compile(r"^\s+workflow_run:\s*$")


def _stripped_lines(path: Path) -> list[str]:
    """The file's lines with whole-line comments removed, so prose never satisfies a check."""
    return [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]


def _triggers_on_workflow_run(lines: list[str]) -> bool:
    """True when ``workflow_run:`` appears in the ``on:`` block, i.e. before the first ``jobs:``."""
    for line in lines:
        if line.startswith("jobs:"):
            return False
        if TRIGGER_WORKFLOW_RUN.match(line):
            return True
    return False


def _checks_out_triggering_run(lines: list[str]) -> bool:
    return any(FORK_CONTROLLED_CHECKOUT.match(line) for line in lines)


def _has_job_level_same_repo_guard(lines: list[str]) -> bool:
    """The guard must sit between ``jobs:`` and the first ``steps:`` — i.e. on the job, not a step.

    A guard on the push step alone would still let the checkout and the build run the fork's
    code in the trusted context; only a job-level ``if:`` keeps it out entirely.
    """
    in_jobs = False
    for line in lines:
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if in_jobs and re.match(r"^\s+steps:\s*$", line):
            return False
        if in_jobs and SAME_REPO_GUARD in line:
            return True
    return False


def check_workflow_run_builds_are_guarded(failures: list[str]) -> None:
    guarded_count = 0
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        lines = _stripped_lines(path)
        rel = path.relative_to(REPO_ROOT)
        triggers = _triggers_on_workflow_run(lines)
        checks_out = _checks_out_triggering_run(lines)

        if triggers and checks_out:
            if _has_job_level_same_repo_guard(lines):
                guarded_count += 1
            else:
                failures.append(
                    f"{rel}: triggers on workflow_run and checks out the triggering run's "
                    f"head_repository/head_sha, but has no job-level `if:` containing\n"
                    f"      {SAME_REPO_GUARD}\n"
                    f"    A fork PR whose head branch is named main/develop reaches this job in the "
                    f"trusted context with packages: write and gets its code built and pushed under "
                    f"the org namespace (FLIP#882). Add the guard to the job's `if:` — see any of the "
                    f"docker_build_* siblings for the exact block."
                )
        elif checks_out and not triggers:
            failures.append(
                f"{rel}: passes github.event.workflow_run.head_repository/head_sha to a checkout but "
                f"does not trigger on workflow_run. Those fields are empty on every event this file "
                f"receives, so this is dead today — and the exact fork-controlled checkout FLIP#882 "
                f"closes, which would re-open here unguarded the moment a workflow_run trigger is "
                f"added. Use the default checkout instead (the way docker_build_orthanc.yml now does)."
            )

    if guarded_count == 0:
        failures.append(
            "no workflow_run-triggered build with a checkout of the triggering run was found under "
            f"{WORKFLOWS_DIR.relative_to(REPO_ROOT)} — the guard has nothing to guard. Either the "
            "docker_build_* workflows were renamed/restructured (update this test) or the parser "
            "missed them; a green run on an empty set is not a pass."
        )


def main() -> int:
    failures: list[str] = []
    check_workflow_run_builds_are_guarded(failures)

    if failures:
        print("❌ workflow trust guards failed:\n")
        for f in failures:
            print(f"  - {f}\n")
        return 1
    print("✅ workflow trust guards passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
