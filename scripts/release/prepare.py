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
"""Prepare a release's version bump on the current branch (FLIP#1608).

``make release-prepare TAG=v0.13.0 [DRY_RUN=1]``

Applies CONTRIBUTING § Cutting a release mechanically, after two checks that refuse to start:

- every PR merged into ``develop`` since the previous ``v*`` tag carries a label (``.github/release.yml`` sorts
  the generated notes by label, so an unlabelled PR is missing from them);
- the previous tag is older than ``TAG``.

Then it bumps the root version to ``TAG``; each service whose code changed since the previous tag by the same
SemVer step (CONTRIBUTING § Versioning; tests and Markdown do not count); flip-utils only if ``flip-utils/**``
changed, strictly above its last ``flip-utils-v*`` tag; runs ``make lock``; and empties the *Release checks*
section. It prints the open Dependabot alert count. It commits, pushes and opens nothing: the agent reviews
the diff, curates the notes and opens the draft ``Release v<X.Y.Z>`` PR.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import record  # noqa: E402
from release_lib import REPO, TEMPLATE, ReleaseError, assert_clean, git, parse_tag  # noqa: E402

# CONTRIBUTING § Versioning: each service's directory and the file carrying its version.
SERVICES = {
    "flip-api": "flip-api/pyproject.toml",
    "flip-ui": "flip-ui/package.json",
    "trust/trust-api": "trust/trust-api/pyproject.toml",
    "trust/imaging-api": "trust/imaging-api/pyproject.toml",
    "trust/data-access-api": "trust/data-access-api/pyproject.toml",
    "fl-services/nvflare/fl-api-base": "fl-services/nvflare/fl-api-base/pyproject.toml",
    "fl-services/flower/fl-api-flower": "fl-services/flower/fl-api-flower/pyproject.toml",
}
FLIP_UTILS = "flip-utils/flip/__init__.py"
VERSION_PATTERNS = {
    ".toml": r'^(version = ")([^"]+)(")',
    ".json": r'^(\s*"version": ")([^"]+)(")',
    ".py": r'^(__version__ = ")([^"]+)(")',
}


def step(previous: str, new: str) -> str:
    """Which SemVer part ``new`` raises over ``previous``: major, minor or patch."""
    p, n = parse_tag(previous), parse_tag(new)
    if n <= p:
        raise ReleaseError(f"{new} is not above the previous release {previous}")
    return "major" if n[0] > p[0] else "minor" if n[1] > p[1] else "patch"


def bump(version: str, part: str) -> str:
    """Raise one SemVer part, zeroing those after it."""
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def counts_as_change(path: str, directory: str) -> bool:
    """A path under a service that ships: tests and Markdown change no released behaviour."""
    if not path.startswith(directory.rstrip("/") + "/"):
        return False
    rest = path[len(directory) + 1 :]
    return not (rest.startswith("tests/") or "/tests/" in rest or rest.endswith(".md"))


def read_version(path: Path) -> str:
    """The version string a pyproject, package.json or ``__init__.py`` carries."""
    m = re.search(VERSION_PATTERNS[path.suffix], path.read_text(), re.M)
    if not m:
        raise ReleaseError(f"no version found in {path}")
    return m[2]


def write_version(text: str, suffix: str, version: str) -> str:
    """Replace the first version string in a file's text."""
    new, n = re.subn(VERSION_PATTERNS[suffix], lambda m: f"{m[1]}{version}{m[3]}", text, count=1, flags=re.M)
    if n != 1:
        raise ReleaseError("version line not found")
    return new


def set_package_lock(text: str, version: str) -> str:
    """flip-ui's package-lock.json names its own version twice, at the top and in ``packages[""]``.

    Both precede every dependency entry, so the first two ``"version"`` lines are the ones; the rest of the file is
    left byte for byte as npm wrote it.
    """
    new, n = re.subn(VERSION_PATTERNS[".json"], lambda m: f"{m[1]}{version}{m[3]}", text, count=2, flags=re.M)
    lock = json.loads(new)
    if n != 2 or lock["version"] != version or lock["packages"][""]["version"] != version:
        raise ReleaseError("flip-ui/package-lock.json does not open with its own two version lines")
    return new


def plan(tag: str, previous: str, utils_tag: str | None, changed: list[str], repo: Path = REPO) -> dict[str, str]:
    """Every file → new version this release sets (relative paths), from the rules in the module docstring."""
    part = step(previous, tag)
    out = {"pyproject.toml": ".".join(map(str, parse_tag(tag)))}
    for directory, file in SERVICES.items():
        if any(counts_as_change(p, directory) for p in changed):
            out[file] = bump(read_version(repo / file), part)
    if any(counts_as_change(p, "flip-utils") for p in changed):
        current = read_version(repo / FLIP_UTILS)
        floor = utils_tag.removeprefix("flip-utils-v") if utils_tag else "0.0.0"
        base = max(current, floor, key=lambda v: tuple(int(x) for x in v.split(".")))
        out[FLIP_UTILS] = bump(base, part)
    return out


def apply(versions: dict[str, str], repo: Path = REPO) -> None:
    """Write the planned versions (and flip-ui's lockfile with its package.json)."""
    for rel, version in versions.items():
        path = repo / rel
        path.write_text(write_version(path.read_text(), path.suffix, version))
        if rel == "flip-ui/package.json":
            lock = repo / "flip-ui" / "package-lock.json"
            lock.write_text(set_package_lock(lock.read_text(), version))


def _gh_json(*args: str):
    out = subprocess.run(["gh", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else []


def unlabelled_prs(since: str) -> list[dict]:
    """PRs merged into develop after ``since`` (the previous tag's ISO commit time) that carry no label at all."""
    prs = _gh_json(
        "pr", "list", "--base", "develop", "--state", "merged", "--limit", "500",
        "--search", f"merged:>{since}", "--json", "number,title,labels",
    )  # fmt: skip
    return [p for p in prs if not p["labels"]]


def dependabot_open() -> int:
    """Open Dependabot alerts on the repository."""
    out = subprocess.run(
        ["gh", "api", "--paginate", "repos/{owner}/{repo}/dependabot/alerts?state=open", "--jq", ".[].number"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return len(out.split())


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dry-run", action="store_true")
    a = parser.parse_args(argv)
    try:
        assert_clean()
        previous = git("tag", "--list", "v*.*.*", "--sort=-v:refname").splitlines()
        previous = [t for t in previous if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
        if not previous:
            raise ReleaseError("no previous v*.*.* tag")
        prev = previous[0]
        utils = (git("tag", "--list", "flip-utils-v*", "--sort=-v:refname").splitlines() or [None])[0]
        # A timestamp, not a date: PRs merged earlier on the tag's day already shipped in it.
        since = git("log", "-1", "--format=%cI", prev)
        missing = unlabelled_prs(since)
        if missing:
            listing = "\n".join(f"  #{p['number']} {p['title']}" for p in missing)
            raise ReleaseError(f"{len(missing)} PR(s) merged since {prev} carry no label:\n{listing}")
        versions = plan(a.tag, prev, utils, git("diff", "--name-only", prev, "HEAD").splitlines())
        print(f"🏷  {prev} → {a.tag}")
        for rel, version in versions.items():
            print(f"  {rel}: {read_version(REPO / rel)} → {version}")
        print(f"🛡  open Dependabot alerts: {dependabot_open()}")
        if a.dry_run:
            return 0
        apply(versions)
        TEMPLATE.write_text(record.replace_section(TEMPLATE.read_text(), record.render({})))
        subprocess.run(["make", "lock"], cwd=REPO, check=True)
        print("✅ bumped, locked and Release checks emptied; review `git diff`, curate the notes, open the draft PR")
    except (ReleaseError, subprocess.CalledProcessError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
