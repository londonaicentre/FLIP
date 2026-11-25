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
"""Assert that a Flower FL image's import path is read-only to the user apps run as (FLIP#1573).

Runs *inside* a built image, as the uid a ServerApp or ClientApp gets. Since FLIP#1418 nothing
installs an app's dependencies at run time, so the image's environment is the only environment a run
has: if the runtime user owns it, app code can rewrite ``flip``, ``flwr`` or ``torch`` in
site-packages and every later run in that container imports the rewritten copy. ``fl-base`` therefore
hands the interpreter prefixes back to root after the install, and this probe is what keeps that
from regressing.

What it checks, per check name:

``runtime-uid-not-root``
    Refuses to pass as uid 0 — root bypasses the permission bits, so every other check below would
    be vacuous.
``sys-path-not-writable``
    No directory on ``sys.path`` is writable, by the permission bits *and* by an actual attempt to
    create a file in it (``os.access`` can disagree with the kernel).
``prefix-trees-not-writable``
    Nothing under ``sys.prefix`` or ``sys.base_prefix`` — the venv and the interpreter whose stdlib
    it reuses, console scripts included — is writable.
``installed-packages-not-writable``
    For every installed distribution, neither its location nor its top-level package directories are
    writable. Covers anything installed outside the prefixes.
``flip-from-site-packages``
    ``import flip`` resolves to a non-writable directory inside a site-packages dir on ``sys.path``,
    not to the root-owned source copy at ``/opt/flip-utils`` and not to the working dir.
``caches-outside-prefix``
    ``$HOME`` is writable and the flwr/torch/pip/XDG cache locations resolve under it rather than
    into the interpreter prefixes — otherwise a read-only prefix would break the first cache write.
``bytecode-precompiled``
    Every ``.py`` in the ``flip`` and ``flwr`` trees has cached bytecode for this interpreter, so a
    run imports them without needing to write into a tree it cannot write.

Usage (the wrapper ``check_image_env_readonly.sh`` does this for a list of images and uids)::

    docker run --rm --user 49999:49999 \\
      -v "$PWD/fl-services/flower/scripts/check_image_env_readonly.py:/probe.py:ro" \\
      --entrypoint python <image> /probe.py

Stdlib-only and executable as a plain script, like the guards in ``scripts/tests`` — it has to run
inside an image whose only extra is FLIP's own dependency set.
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import importlib.util
import os
import site
import sys
import sysconfig
from pathlib import Path

# Report at most this many offending paths per check: a writable site-packages yields thousands of
# them and the first few already name the tree.
MAX_REPORTED = 15


def _existing_dirs(paths: list[str]) -> list[Path]:
    seen: dict[Path, None] = {}
    for raw in paths:
        if not raw:
            continue
        p = Path(raw)
        if p.is_dir():
            seen.setdefault(p.resolve(), None)
    return list(seen)


def _writable(path: Path) -> bool:
    """True if the *effective* uid may write ``path`` per the permission bits."""
    kwargs = {"effective_ids": True} if os.access in os.supports_effective_ids else {}
    try:
        return os.access(path, os.W_OK, **kwargs)  # type: ignore[arg-type]
    except OSError:
        return False


def _really_writable(path: Path) -> bool:
    """True if a file can actually be created in ``path`` — the kernel's answer, not the bits'."""
    probe = path / f".flip-write-probe-{os.getpid()}"
    try:
        fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        return False
    os.close(fd)
    try:
        os.unlink(probe)
    except OSError:
        pass
    return True


def _site_dirs() -> list[Path]:
    candidates = list(site.getsitepackages()) + [site.getusersitepackages()]
    for key in ("purelib", "platlib"):
        candidates.append(sysconfig.get_paths().get(key, ""))
    return _existing_dirs([str(c) for c in candidates])


def _prefixes() -> list[Path]:
    return _existing_dirs([sys.prefix, sys.base_prefix])


class Checks:
    """Collects per-check pass/fail so the caller can report "N of M checks failed"."""

    def __init__(self) -> None:
        self.results: list[tuple[str, bool, list[str]]] = []

    def record(self, name: str, problems: list[str]) -> None:
        self.results.append((name, not problems, problems))

    @property
    def failed(self) -> list[tuple[str, bool, list[str]]]:
        return [r for r in self.results if not r[1]]


def check_runtime_uid_not_root(checks: Checks) -> None:
    uid = os.geteuid()
    problems = []
    if uid == 0:
        problems.append(
            "running as uid 0: root bypasses the permission bits, so every writability check below "
            "would pass whatever the image looks like. Re-run with --user <uid>:<gid>."
        )
    checks.record("runtime-uid-not-root", problems)


def check_sys_path_not_writable(checks: Checks) -> None:
    problems = []
    dirs = _existing_dirs(sys.path)
    if not dirs:
        problems.append("sys.path contains no existing directory — the check below would read nothing")
    for d in dirs:
        # The process's own working dir is the app's, and is meant to be writable.
        if d == Path.cwd().resolve():
            continue
        if _writable(d) or _really_writable(d):
            problems.append(f"{d}: on sys.path and writable by uid {os.geteuid()}")
    checks.record("sys-path-not-writable", problems[:MAX_REPORTED])


def check_prefix_trees_not_writable(checks: Checks) -> None:
    problems: list[str] = []
    prefixes = _prefixes()
    if not prefixes:
        problems.append("neither sys.prefix nor sys.base_prefix is a directory")
    scanned = 0
    for prefix in prefixes:
        for root, dirnames, filenames in os.walk(prefix):
            root_path = Path(root)
            scanned += 1
            if _writable(root_path):
                problems.append(f"{root_path}: directory under the interpreter prefix is writable")
            for name in filenames:
                f = root_path / name
                if f.is_symlink():
                    continue
                if _writable(f):
                    problems.append(f"{f}: file under the interpreter prefix is writable")
            if len(problems) > MAX_REPORTED:
                dirnames[:] = []
    if scanned == 0 and not problems:
        problems.append("walked no directory under the interpreter prefixes")
    checks.record("prefix-trees-not-writable", problems[:MAX_REPORTED])


def _distribution_dirs() -> list[Path]:
    dirs: dict[Path, None] = {}
    for dist in md.distributions():
        base = getattr(dist, "_path", None)
        if base is not None:
            dirs.setdefault(Path(base), None)
            dirs.setdefault(Path(base).parent, None)
        try:
            files = dist.files or []
        except Exception:  # pragma: no cover - a malformed RECORD must not mask the check
            files = []
        for rel in files:
            parts = Path(str(rel)).parts
            if not parts or parts[0].startswith(".."):
                continue
            top = dist.locate_file(parts[0])
            top_path = Path(str(top))
            if top_path.is_dir():
                dirs.setdefault(top_path, None)
    return [d for d in dirs if d.exists()]


def check_installed_packages_not_writable(checks: Checks) -> None:
    problems = []
    dirs = _distribution_dirs()
    if not dirs:
        problems.append("found no installed distribution — the check below would read nothing")
    for d in dirs:
        if _writable(d) or (d.is_dir() and _really_writable(d)):
            problems.append(f"{d}: installed package directory is writable by uid {os.geteuid()}")
    checks.record("installed-packages-not-writable", problems[:MAX_REPORTED])


def check_flip_from_site_packages(checks: Checks) -> None:
    problems = []
    spec = importlib.util.find_spec("flip")
    if spec is None or not spec.origin:
        problems.append("flip is not importable in this image")
        checks.record("flip-from-site-packages", problems)
        return
    pkg = Path(spec.origin).resolve().parent
    site_dirs = _site_dirs()
    if not any(pkg.is_relative_to(d) for d in site_dirs):
        problems.append(f"flip resolves to {pkg}, which is not inside a site-packages dir ({site_dirs})")
    if pkg.is_relative_to(Path("/opt/flip-utils")):
        problems.append(
            f"flip resolves to {pkg}: runs import the /opt/flip-utils source copy rather than the installed package"
        )
    if _writable(pkg) or _really_writable(pkg):
        problems.append(f"{pkg}: the imported flip package is writable by uid {os.geteuid()}")
    checks.record("flip-from-site-packages", problems)


def check_caches_outside_prefix(checks: Checks) -> None:
    problems = []
    home = os.environ.get("HOME")
    if not home:
        problems.append("HOME is unset, so the caches below have nowhere to resolve to")
        checks.record("caches-outside-prefix", problems)
        return
    home_path = Path(home)
    if not home_path.is_dir():
        problems.append(f"HOME={home} is not a directory")
    elif not _really_writable(home_path):
        problems.append(f"HOME={home} is not writable by uid {os.geteuid()}: flwr and the caches write there")

    xdg_cache = Path(os.environ.get("XDG_CACHE_HOME") or home_path / ".cache")
    locations = {
        "flwr home (FLWR_HOME)": Path(os.environ.get("FLWR_HOME") or home_path / ".flwr"),
        "torch hub (TORCH_HOME)": Path(os.environ.get("TORCH_HOME") or xdg_cache / "torch"),
        "pip cache (PIP_CACHE_DIR)": Path(os.environ.get("PIP_CACHE_DIR") or xdg_cache / "pip"),
        "XDG cache (XDG_CACHE_HOME)": xdg_cache,
        "bytecode cache (PYTHONPYCACHEPREFIX)": Path(os.environ.get("PYTHONPYCACHEPREFIX") or home_path),
    }
    prefixes = _prefixes()
    for label, path in locations.items():
        for prefix in prefixes:
            if path.resolve().is_relative_to(prefix):
                problems.append(
                    f"{label} resolves to {path}, inside the read-only interpreter prefix {prefix}: "
                    "point it under $HOME"
                )
    checks.record("caches-outside-prefix", problems)


def check_bytecode_precompiled(checks: Checks) -> None:
    problems = []
    checked = 0
    for name in ("flip", "flwr"):
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.origin:
            problems.append(f"{name} is not importable in this image")
            continue
        for src in Path(spec.origin).parent.rglob("*.py"):
            checked += 1
            if not Path(importlib.util.cache_from_source(str(src))).exists():
                problems.append(f"{src}: no cached bytecode for this interpreter (run compileall at build time)")
    if checked == 0 and not problems:
        problems.append("found no .py file in the flip/flwr trees — the check read nothing")
    checks.record("bytecode-precompiled", problems[:MAX_REPORTED])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assert that this image's import path is read-only to the running uid (FLIP#1573)."
    )
    parser.add_argument(
        "--label",
        default=os.environ.get("FLIP_PROBE_LABEL", ""),
        help="free-text label printed with the result, e.g. the image reference under test",
    )
    args = parser.parse_args()

    checks = Checks()
    check_runtime_uid_not_root(checks)
    check_sys_path_not_writable(checks)
    check_prefix_trees_not_writable(checks)
    check_installed_packages_not_writable(checks)
    check_flip_from_site_packages(checks)
    check_caches_outside_prefix(checks)
    check_bytecode_precompiled(checks)

    label = f" [{args.label}]" if args.label else ""
    print(f"FL image environment probe{label}: uid={os.geteuid()} gid={os.getegid()} prefix={sys.prefix}")
    for name, ok, problems in checks.results:
        print(f"  {'✅' if ok else '❌'} {name}")
        for problem in problems:
            print(f"       - {problem}")
    failed = len(checks.failed)
    total = len(checks.results)
    print(f"{failed} of {total} checks failed{label}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
