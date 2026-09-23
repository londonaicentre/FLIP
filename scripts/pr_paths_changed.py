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
"""Decide whether a service test workflow should run for the current event.

The service ``test_*`` workflows filter their ``push`` trigger by path but cannot do the
same for ``pull_request`` without losing the full suite on release PRs: a ``paths:`` list
applies to every target branch alike, and a PR to ``main`` must run everything regardless
of what it touches. So those workflows always fire on PRs and ask this script instead,
through ``.github/workflows/pr_paths_changed.yml``. Its answer is the whole policy:

- any event other than ``pull_request`` runs (the push trigger already filtered by path);
- a PR targeting ``main`` runs;
- a PR targeting anything else runs only if one of its changed files matches the
  caller's path list, which is the same list as its push filter, or touches the gate
  itself (this script or the reusable workflow), since a change to the gate must be
  proven against every suite it gates.

The changed-file list comes from ``gh api .../pulls/N/files`` — the list GitHub shows on
the PR, so the decision matches what a reviewer sees. Patterns use GitHub's own filter
syntax (``*`` stops at ``/``, ``**`` does not) so the two lists can be identical text.

Deliberately stdlib-only: the runner has no project venv, only ``python3`` and ``gh``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable

# Files whose change must re-run every gated suite, whatever the caller's list says.
GATE_FILES = (
    "scripts/pr_paths_changed.py",
    ".github/workflows/pr_paths_changed.yml",
)

# PRs into this branch always run the full suite: it is the release branch, and a release
# is judged on everything, not on the diff.
FULL_SUITE_BASE_REFS = ("main",)


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate one GitHub filter pattern into an anchored regex.

    Only the subset the workflows use is supported: ``**`` (any path segment run), ``*``
    and ``?`` (within one segment). A leading ``!`` (negation) is refused rather than
    silently treated as a literal, because a negation that matches nothing would turn a
    "run less" instruction into "run more" without anyone noticing.
    """
    if pattern.startswith("!"):
        raise ValueError(f"negated patterns are not supported: {pattern!r}")
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def parse_patterns(text: str) -> list[str]:
    """One pattern per line; blank lines and ``#`` comments are ignored."""
    patterns = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def first_match(changed_files: Iterable[str], patterns: Iterable[str]) -> tuple[str, str] | None:
    """Return ``(file, pattern)`` for the first changed file a pattern matches, else None."""
    compiled = [(p, glob_to_regex(p)) for p in patterns]
    for path in changed_files:
        for pattern, rx in compiled:
            if rx.match(path):
                return path, pattern
    return None


def decide(event: str, base_ref: str, changed_files: list[str] | None, patterns: list[str]) -> tuple[bool, str]:
    """Return ``(run, reason)``. ``changed_files`` may be None for non-PR events."""
    if event != "pull_request":
        return True, f"event is {event!r}, not a pull request"
    if base_ref in FULL_SUITE_BASE_REFS:
        return True, f"pull request targets {base_ref!r}: full suite always runs"
    hit = first_match(changed_files or [], list(patterns) + list(GATE_FILES))
    if hit is not None:
        path, pattern = hit
        return True, f"{path!r} matches {pattern!r}"
    return False, f"none of the {len(changed_files or [])} changed file(s) match the workflow's paths"


def fetch_changed_files(repo: str, pr_number: str) -> list[str]:
    """The PR's changed files as GitHub lists them, following pagination."""
    out = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr_number}/files", "--paginate", "--jq", ".[].filename"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event", required=True, help="github.event_name")
    parser.add_argument("--base-ref", default="", help="github.base_ref (empty outside pull requests)")
    parser.add_argument("--repo", default="", help="github.repository, owner/name")
    parser.add_argument("--pr-number", default="", help="github.event.pull_request.number")
    parser.add_argument("--paths-file", required=True, help="file holding the caller's path list, one per line")
    parser.add_argument(
        "--changed-files-json",
        help="JSON list of changed files to use instead of calling gh (tests and dry runs)",
    )
    args = parser.parse_args(argv)

    with open(args.paths_file, encoding="utf-8") as fh:
        patterns = parse_patterns(fh.read())
    if not patterns:
        print("::error::the caller passed an empty path list; refusing to gate on nothing", file=sys.stderr)
        return 2

    changed: list[str] | None = None
    if args.event == "pull_request" and args.base_ref not in FULL_SUITE_BASE_REFS:
        if args.changed_files_json is not None:
            changed = json.loads(args.changed_files_json)
        else:
            if not (args.repo and args.pr_number):
                print("::error::--repo and --pr-number are required for pull_request events", file=sys.stderr)
                return 2
            changed = fetch_changed_files(args.repo, args.pr_number)

    run, reason = decide(args.event, args.base_ref, changed, patterns)
    print(f"run={'true' if run else 'false'}: {reason}")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write(f"run={'true' if run else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
