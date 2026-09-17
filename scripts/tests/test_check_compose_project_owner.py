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
"""Tests for scripts/check-compose-project-owner.sh — the hub project ownership guard (FLIP#1227).

Black-box: runs the script with a stub ``docker`` on PATH that prints whatever working_dir
labels a test wants the project to carry, so no daemon is needed and the refuse/allow rules
are pinned exactly:

- no containers → allowed
- every container from this checkout → allowed (the normal restart path)
- a container from another directory the current user owns → allowed (same-user worktree hand-over)
- a container from a directory the user cannot stat, or that no longer exists → refused
- FORCE=1 → allowed regardless

Stdlib-only, run as ``python <file>`` by test_trust_kit_scripts.yml.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
GUARD = SCRIPTS_DIR / "check-compose-project-owner.sh"

PASS = 0
FAIL = 0


def _assert(condition: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}")
        if detail:
            for line in detail.splitlines():
                print(f"    {line}")
        FAIL += 1


def _run(working_dirs: list[str], expected: Path, force: str = "") -> subprocess.CompletedProcess:
    """Run the guard with a stub docker that reports ``working_dirs`` for the project."""
    bindir = Path(tempfile.mkdtemp())
    stub = bindir / "docker"
    lines = "".join(f"{d}\n" for d in working_dirs)
    stub.write_text(f"#!/usr/bin/env bash\nprintf '%s' '{lines}'\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "COMPOSE_PROJECT": "deploy",
           "EXPECTED_WORKING_DIR": str(expected), "FORCE": force}
    return subprocess.run(["bash", str(GUARD)], env=env, capture_output=True, text=True)


def test_no_containers_is_allowed() -> None:
    with tempfile.TemporaryDirectory() as d:
        r = _run([], Path(d) / "deploy")
        _assert(r.returncode == 0, "no containers → exit 0", r.stderr)


def test_same_checkout_is_allowed() -> None:
    with tempfile.TemporaryDirectory() as d:
        mine = Path(d) / "deploy"
        mine.mkdir()
        r = _run([str(mine), str(mine)], mine)
        _assert(r.returncode == 0, "containers from this checkout → exit 0", r.stderr)


def test_same_checkout_via_symlink_is_allowed() -> None:
    with tempfile.TemporaryDirectory() as d:
        real = Path(d) / "real" / "deploy"
        real.mkdir(parents=True)
        link = Path(d) / "link"
        link.symlink_to(real.parent)
        r = _run([str(link / "deploy")], real)
        _assert(r.returncode == 0, "same checkout reached through a symlink → exit 0", r.stderr)


def test_other_dir_owned_by_me_is_allowed() -> None:
    with tempfile.TemporaryDirectory() as d:
        mine = Path(d) / "deploy"
        mine.mkdir()
        other = Path(d) / "worktree" / "deploy"
        other.mkdir(parents=True)
        r = _run([str(other)], mine)
        _assert(r.returncode == 0, "another directory owned by the current user (worktree) → exit 0", r.stderr)


def test_unreadable_or_missing_dir_is_refused() -> None:
    with tempfile.TemporaryDirectory() as d:
        mine = Path(d) / "deploy"
        mine.mkdir()
        gone = Path(d) / "someone-else" / "deploy"  # never created: stat fails, as for a 0750 peer home
        r = _run([str(gone)], mine)
        _assert(r.returncode == 1, "directory the user cannot stat → exit 1", r.stderr)
        _assert(str(gone) in r.stderr, "message names the foreign directory", r.stderr)
        _assert("FLIP_INSTANCE" in r.stderr, "message points at FLIP_INSTANCE as the remedy", r.stderr)
        _assert("label=com.docker.compose.project=deploy" in r.stderr, "message gives the docker ps listing", r.stderr)


def test_force_overrides() -> None:
    with tempfile.TemporaryDirectory() as d:
        mine = Path(d) / "deploy"
        mine.mkdir()
        r = _run([str(Path(d) / "someone-else" / "deploy")], mine, force="1")
        _assert(r.returncode == 0, "FORCE=1 → exit 0 even when foreign", r.stderr)


def test_mixed_own_and_foreign_is_refused() -> None:
    with tempfile.TemporaryDirectory() as d:
        mine = Path(d) / "deploy"
        mine.mkdir()
        r = _run([str(mine), str(Path(d) / "someone-else" / "deploy")], mine)
        _assert(r.returncode == 1, "own + foreign containers in one project → exit 1", r.stderr)


def main() -> None:
    if not GUARD.is_file():
        sys.exit(f"❌ {GUARD} not found")
    test_no_containers_is_allowed()
    test_same_checkout_is_allowed()
    test_same_checkout_via_symlink_is_allowed()
    test_other_dir_owned_by_me_is_allowed()
    test_unreadable_or_missing_dir_is_refused()
    test_force_overrides()
    test_mixed_own_and_foreign_is_refused()
    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
