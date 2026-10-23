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
"""Check the ``develop`` → ``main`` release PR is ready, and print what follows the merge (FLIP#1608).

``make release-main-check TAG=v0.13.0 [PR=<n>] [OWED=<file>]``

Read-only: opens, merges and approves nothing. It finds the open PR titled ``Release vX.Y.Z (develop → main)``
(or ``PR``) and fails unless GitHub reports it MERGEABLE, ``validate_branch_origin`` passed,
``check-version-bump`` passed whenever ``flip-utils/**`` changed (it runs only then), and the release-notes
preview comment is present. It then prints the after-merge checklist. With no such PR it prints the
``gh pr create`` command to open one, its body carrying the checklist and the owed after-merge items (one per
line in ``OWED``).
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_lib import REPO, ReleaseError, git, parse_tag  # noqa: E402

PREVIEW_MARKER = "<!-- release-notes-preview -->"  # pr-release-notes-preview.yml
ORIGIN_WORKFLOW = "Validate PR Source Branch"  # validate_branch_origin.yml
VERSION_WORKFLOW = "Check version bump for PR to main"  # check-version-bump.yml, only on flip-utils/** changes


def title(tag: str) -> str:
    """The release PR's title."""
    return f"Release {tag} (develop → main)"


def after_merge(tag: str) -> list[str]:
    """What happens after the merge, in order (the skill's *After the bump PR merges*)."""
    return [
        "Merge with a **merge commit** — never squash or rebase.",
        "Two prod Terraform applies follow: the push apply at `sha-<short7>`, then `release.yml`'s apply at "
        f"`{tag}` once the twelve image builds are green. Either can hold on the FL gate; only the user attests "
        "`fl_quiesced`.",
        "Nothing merges to `main` until the release apply has run.",
        f"Check the Release page, GHCR `:{tag}` on all twelve images, PyPI if flip-utils was bumped, and that prod "
        f"`/api/health` reports `{tag}`.",
        "Deploy the UI by hand (#1186): `make -C deploy/providers/AWS deploy-ui HUB=<hub>` from a checkout at the tag.",
        "Sites upgrade themselves: point operators to *Site upgrade* and "
        "`docs/source/sys-admin/admin-upgrading-sites.rst`.",
    ]


def _passed(rollup: list[dict], workflow: str) -> bool | None:
    """Whether a workflow's checks all succeeded; None when it did not run."""
    runs = [c for c in rollup if c.get("workflowName") == workflow]
    if not runs:
        return None
    return all((c.get("conclusion") or c.get("state")) in ("SUCCESS", "NEUTRAL", "SKIPPED") for c in runs)


def problems(pr: dict, flip_utils_changed: bool) -> list[str]:
    """Why the PR is not ready to merge (empty when it is), from ``gh pr view --json`` output."""
    out = []
    if pr.get("baseRefName") != "main" or pr.get("headRefName") != "develop":
        out.append(f"#{pr['number']} is {pr.get('headRefName')} → {pr.get('baseRefName')}, not develop → main")
    if pr.get("mergeable") != "MERGEABLE":
        out.append(f"GitHub reports #{pr['number']} as {pr.get('mergeable')}, not MERGEABLE")
    rollup = pr.get("statusCheckRollup") or []
    if not _passed(rollup, ORIGIN_WORKFLOW):
        out.append(f"'{ORIGIN_WORKFLOW}' (validate_branch_origin) has not passed")
    version = _passed(rollup, VERSION_WORKFLOW)
    if flip_utils_changed and not version:
        out.append(f"flip-utils changed, and '{VERSION_WORKFLOW}' (check-version-bump) has not passed")
    if version is False:
        out.append(f"'{VERSION_WORKFLOW}' (check-version-bump) failed")
    if not any(PREVIEW_MARKER in (c.get("body") or "") for c in pr.get("comments") or []):
        out.append("no release-notes preview comment yet (pr-release-notes-preview.yml)")
    return out


def create_command(tag: str, owed: list[str]) -> str:
    """The ``gh pr create`` command that opens the release PR, with its body skeleton."""
    lines = [
        "## Summary",
        "",
        f"Release {tag}: merges `develop` into `main`. The bump PR, its notes and the *Release checks* evidence "
        "were reviewed on `develop`.",
        "",
        "## Merge and after",
        "",
        *(f"- [ ] {step}" for step in after_merge(tag)),
        *(f"- [ ] {item}" for item in owed),
    ]
    body = "\n".join(lines)
    return (
        f"gh pr create --base main --head develop --label release --title {shlex.quote(title(tag))}"
        f" --body {shlex.quote(body)}"
    )


def find_pr(tag: str) -> str | None:
    """The open develop → main PR carrying the release title."""
    out = subprocess.run(
        ["gh", "pr", "list", "--base", "main", "--head", "develop", "--state", "open", "--json", "number,title"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return next((str(p["number"]) for p in json.loads(out or "[]") if p["title"] == title(tag)), None)


def view(pr: str) -> dict:
    """The PR fields the checks read."""
    fields = "number,title,baseRefName,headRefName,mergeable,statusCheckRollup,comments"
    out = subprocess.run(
        ["gh", "pr", "view", pr, "--json", fields], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout
    return json.loads(out)


def flip_utils_changed() -> bool:
    """Whether develop changes flip-utils/** relative to main."""
    git("fetch", "--quiet", "origin", "main", "develop")
    return bool(git("diff", "--name-only", "origin/main", "origin/develop", "--", "flip-utils/"))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--pr", default="")
    parser.add_argument("--owed", default="", help="file with one owed after-merge item per line")
    a = parser.parse_args(argv)
    try:
        parse_tag(a.tag)
        owed = [line.strip() for line in Path(a.owed).read_text().splitlines() if line.strip()] if a.owed else []
        number = a.pr or find_pr(a.tag)
        if not number:
            print(f"No open PR titled '{title(a.tag)}'. To open it (only when asked to):\n")
            print(create_command(a.tag, owed))
            return 1
        found = problems(view(number), flip_utils_changed())
        for line in found:
            print(f"  ✗ {line}")
        if found:
            print(f"❌ #{number} is not ready to merge")
            return 1
        print(f"✅ #{number} is ready. Merging is the user's call. After the merge:")
        for i, step in enumerate([*after_merge(a.tag), *owed], 1):
            print(f"  {i}. {step}")
    except (ReleaseError, subprocess.CalledProcessError, OSError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
