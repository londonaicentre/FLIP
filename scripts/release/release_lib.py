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
"""Helpers shared by the release scripts (FLIP#1608): git state, env files, the instance's make arguments.

Stdlib only, so every script runs with the host's plain ``python3`` on the GPU host.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / ".github" / "RELEASE_NOTES_TEMPLATE.md"
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# An uncommented `KEY=<placeholder>` line: a value the example leaves for the operator to fill.
PLACEHOLDER_RE = re.compile(r"^([A-Z0-9_]+)=<[^>\n]*>", re.M)


class ReleaseError(Exception):
    """A precondition the release pipeline refuses to run without."""


def die(message: str) -> None:
    """Print a refusal and exit non-zero."""
    print(f"❌ {message}", file=sys.stderr)
    sys.exit(1)


def parse_tag(tag: str) -> tuple[int, int, int]:
    """Split ``vX.Y.Z`` into integers, refusing anything else (pre-release tags are not releases)."""
    m = TAG_RE.match(tag)
    if not m:
        raise ReleaseError(f"TAG must look like v1.2.3, got {tag!r}")
    return int(m[1]), int(m[2]), int(m[3])


def default_instance(tag: str) -> str:
    """``v0.13.0`` -> ``rel0130``: the instance name and image tag a release check runs under."""
    return "rel" + "".join(str(n) for n in parse_tag(tag))


def git(*args: str, cwd: Path = REPO) -> str:
    """Run git and return stripped stdout."""
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def head_sha(cwd: Path = REPO) -> str:
    """The checkout's HEAD commit."""
    return git("rev-parse", "HEAD", cwd=cwd)


def assert_clean(cwd: Path = REPO) -> None:
    """Refuse a checkout with tracked changes: the gates must test a commit, not a working tree."""
    dirty = git("status", "--porcelain", "--untracked-files=no", cwd=cwd)
    if dirty:
        raise ReleaseError(f"tracked changes in {cwd}:\n{dirty}")


def pr_head(pr: str) -> str:
    """The head commit GitHub has for a PR."""
    out = subprocess.run(
        ["gh", "pr", "view", pr, "--json", "headRefOid", "-q", ".headRefOid"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def assert_at_pr_head(pr: str | None, cwd: Path = REPO) -> str:
    """Refuse unless HEAD is the PR's head (when a PR is named); return HEAD."""
    sha = head_sha(cwd)
    if pr and pr_head(pr) != sha:
        raise ReleaseError(f"HEAD {sha[:9]} is not the head of PR #{pr}; check out the PR head first")
    return sha


def env_file_name(instance: str) -> str:
    """The instance's hub env file, a bare filename as MAIN_ENV_FILE requires."""
    return f".env.{instance}.development"


def read_env(path: Path) -> dict[str, str]:
    """Uncommented ``KEY=VALUE`` lines of an env file (last one wins, as make and compose read it)."""
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m:
            values[m[1]] = m[2]
    return values


def set_env(text: str, key: str, value: str) -> str:
    """Set ``key`` in env-file text: replace its line, else uncomment its documented ``# KEY=`` default, else append.

    The documented default keeps the value next to the comment that explains it.
    """
    line = f"{key}={value}"
    for pattern in (rf"^{key}=.*$", rf"^# ?{key}=.*$"):
        new, n = re.subn(pattern, lambda _: line, text, count=1, flags=re.M)
        if n:
            return new
    return text.rstrip("\n") + f"\n{line}\n"


def documented_value(text: str, key: str) -> str | None:
    """The value a key carries in an example, set or commented out (``# KEY=8180`` -> ``8180``)."""
    m = re.search(rf"^(?:# ?)?{key}=([^\s#]*)", text, re.M)
    return m[1] if m else None


def placeholders(text: str) -> list[str]:
    """Keys still carrying an unfilled ``<placeholder>`` value."""
    return PLACEHOLDER_RE.findall(text)


def instance_make_args(instance: str) -> list[str]:
    """What every make call against the instance passes, so nothing falls back to the default stack or a shared tag."""
    return [
        f"FLIP_INSTANCE={instance}",
        f"MAIN_ENV_FILE={env_file_name(instance)}",
        "DOCKER_FL_REGISTRY=",
        f"DOCKER_FL_TAG={instance}",
        f"XNAT_TAG={instance}",
    ]


def instance_env(instance: str, repo: Path = REPO) -> dict[str, str]:
    """Process environment for make calls: the host PATH plus the instance's own hub URLs (belt and braces).

    Non-interactive SSH may not load the login PATH, so ``uv`` in ``~/.local/bin`` is added here.
    """
    env = dict(os.environ)
    env["PATH"] = f"{Path.home() / '.local' / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env_file = repo / env_file_name(instance)
    if env_file.exists():
        values = read_env(env_file)
        for key in ("FLIP_E2E_BASE_URL", "KEYCLOAK_PUBLIC_URL"):
            if values.get(key):
                env[key] = values[key]
    return env


def gates_dir(tag: str, repo: Path = REPO) -> Path:
    """Where ``release-gates`` writes its results and logs (gitignored)."""
    return repo / "release-gates" / tag


def load_results(tag: str, repo: Path = REPO) -> dict[str, dict]:
    """Every gate result written for ``tag``, by gate name."""
    out = {}
    for path in sorted(gates_dir(tag, repo).glob("*.json")):
        data = json.loads(path.read_text())
        out[data["gate"]] = data
    return out
