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

"""Assert that nothing a FLARE image imports can be rewritten by the uid that runs the FL processes.

The FL server and client are long-lived processes that execute researcher-supplied job code as the
runtime user, inside a workspace that same user writes. Anything that user can also write *and*
Python can import is shared state between jobs: a module dropped there is imported by the next job,
and by the client process itself. This script is the check that no such directory exists, run
against the built image rather than read off the Dockerfile, because the property depends on the
installer (editable vs not), on ownership, and on the interpreter's own path rules.

Run it inside the image, as the uid the image will run as (never as root — root passes every
permission check and would make this vacuous). Mount it somewhere root-owned, not at ``/``: on an
image without ``PYTHONSAFEPATH`` the script's own directory joins ``sys.path``, and the probe would
then report the mount point rather than what the image does::

    docker run --rm -u 1000:1000 \\
      -v "$PWD/fl-services/nvflare/scripts/check_import_path_readonly.py:/opt/probe/check.py:ro" \\
      --entrypoint python flare-fl-client:<tag> /opt/probe/check.py

Exit status is 0 only if every check passes; the summary line names how many failed.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import sysconfig

# Reproduces what a provisioned FLARE kit's startup/sub_start.sh exports before it starts the FL
# process: `export PYTHONPATH=/local/custom:$PYTHONPATH`. With PYTHONPATH unset that trailing
# colon is an empty entry, which Python reads as the working directory.
KIT_PYTHONPATH_PREFIX = "/local/custom"


def _writable_by_me(path: str, uid: int, gids: set[int]) -> bool:
    """Return True if *path* can be written by this uid, from its mode bits alone.

    ``os.access`` is not used: it answers for the real uid including root's blanket override, and
    follows symlinks (whose own mode is always 0777 and meaningless).

    Args:
        path: Path to inspect; symlinks are reported as not writable (their target is inspected
            separately when it is itself on the walk).
        uid: Effective uid to answer for.
        gids: Group ids the process belongs to.

    Returns:
        True when the uid could write the path.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return False
    mode = st.st_mode
    if stat.S_ISLNK(mode):
        return False
    if mode & stat.S_IWOTH:
        return True
    if mode & stat.S_IWGRP and st.st_gid in gids:
        return True
    return bool(mode & stat.S_IWUSR and st.st_uid == uid)


def _walk_for_writable(root: str, uid: int, gids: set[int], limit: int = 20) -> tuple[list[str], int]:
    """Walk *root* and collect entries this uid can write.

    Args:
        root: Directory to walk.
        uid: Effective uid to answer for.
        gids: Group ids the process belongs to.
        limit: Stop collecting after this many offenders (the walk still completes).

    Returns:
        The offending paths (at most *limit*) and the number of filesystem entries inspected.
    """
    offenders: list[str] = []
    inspected = 0
    if _writable_by_me(root, uid, gids):
        offenders.append(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            inspected += 1
            if len(offenders) >= limit:
                continue
            full = os.path.join(dirpath, name)
            if _writable_by_me(full, uid, gids):
                offenders.append(full)
    return offenders, inspected


def _import_paths() -> list[str]:
    """Return the existing directories on this interpreter's ``sys.path``."""
    return [p for p in sys.path if p and os.path.isdir(p)]


def _child_sys_path(env: dict[str, str], cwd: str) -> list[str]:
    """Return the ``sys.path`` a child interpreter gets under *env* and *cwd*.

    Args:
        env: Full environment for the child.
        cwd: Working directory to start the child in.

    Returns:
        The child's ``sys.path``.
    """
    out = subprocess.run(
        [sys.executable, "-c", "import sys, json; print(json.dumps(sys.path))"],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


class Checks:
    """Collects check results and prints them as they are recorded."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.total = 0

    def record(self, name: str, ok: bool, detail: str = "") -> bool:
        """Record one check.

        Args:
            name: Human-readable check name.
            ok: Whether it passed.
            detail: Extra context, printed on failure (and on success when short).

        Returns:
            *ok*, so callers can branch on it.
        """
        self.total += 1
        print(f"{'✅ PASS' if ok else '❌ FAIL'}: {name}{f' — {detail}' if detail else ''}")
        if not ok:
            self.failures.append(name)
        return ok


def main() -> int:
    """Run every check and return the process exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        default="/app",
        help="The FLARE workspace the FL process is started from (default: /app).",
    )
    args = parser.parse_args()

    uid = os.geteuid()
    gids = set(os.getgroups()) | {os.getegid()}
    if uid == 0:
        print("❌ refusing to run as root: every permission check would pass vacuously", file=sys.stderr)
        return 2

    print(f"probing as uid={uid} gids={sorted(gids)} workspace={args.workspace} python={sys.executable}")
    checks = Checks()

    # 1. The interpreter must not prepend the working directory / script directory to sys.path.
    #    FLARE starts the FL process with `python3 -u -m ...` from inside the workspace, so without
    #    this the workspace leads sys.path.
    checks.record(
        "PYTHONSAFEPATH is in effect (sys.flags.safe_path)",
        bool(sys.flags.safe_path),
        f"safe_path={sys.flags.safe_path}",
    )

    # 2. PYTHONSAFEPATH does not filter PYTHONPATH, and an empty PYTHONPATH entry *is* the working
    #    directory. The kit's sub_start.sh appends to PYTHONPATH, so the image's own value must be
    #    non-empty for that expansion to stay harmless.
    image_pythonpath = os.environ.get("PYTHONPATH", "")
    kit_expansion = f"{KIT_PYTHONPATH_PREFIX}:{image_pythonpath}"
    empty_entries = [i for i, p in enumerate(kit_expansion.split(os.pathsep)) if not p]
    checks.record(
        "the kit's PYTHONPATH expansion has no empty (=cwd) entry",
        not empty_entries,
        f"PYTHONPATH={image_pythonpath!r} -> {kit_expansion!r}",
    )

    # 3. Belt and braces: the workspace itself must not be on this interpreter's path.
    cwd = os.getcwd()
    on_path = [p for p in sys.path if p in ("", ".", cwd, args.workspace)]
    checks.record(
        "neither the working directory nor the workspace is on sys.path",
        not on_path,
        f"offending entries: {on_path}" if on_path else f"cwd={cwd}",
    )

    # 4. The core property: no import-path directory, and nothing inside one, is writable here.
    offenders: list[str] = []
    inspected = 0
    probe_self = os.path.realpath(__file__)
    for entry in _import_paths():
        found, count = _walk_for_writable(entry, uid, gids)
        # This script is bind-mounted in and keeps its host ownership; it is not part of the image.
        offenders.extend(p for p in found if os.path.realpath(p) != probe_self)
        inspected += count
    checks.record(
        "no sys.path entry (or anything under one) is writable by the runtime uid",
        not offenders,
        f"{inspected} entries inspected across {len(_import_paths())} path roots"
        if not offenders
        else f"writable: {offenders[:20]}",
    )

    # 5. flip-utils must be installed as an ordinary package, not editable off a workspace copy.
    purelib = os.path.realpath(sysconfig.get_paths()["purelib"])
    try:
        import flip  # noqa: PLC0415 — the resolution of this import is the thing under test
    except Exception as exc:  # pragma: no cover - defensive
        checks.record("`import flip` resolves to site-packages", False, f"import failed: {exc!r}")
    else:
        flip_file = os.path.realpath(flip.__file__ or "")
        checks.record(
            "`import flip` resolves to site-packages",
            flip_file.startswith(purelib + os.sep),
            f"flip.__file__={flip_file} (site-packages={purelib})",
        )

    # 6. An editable install leaves a .pth or __editable__ finder behind that puts a source tree
    #    back on sys.path at interpreter start; so does any stray .pth. None may name a writable dir.
    pth_offenders: list[str] = []
    for site_dir in {purelib, os.path.realpath(sysconfig.get_paths()["platlib"])}:
        if not os.path.isdir(site_dir):
            continue
        for name in sorted(os.listdir(site_dir)):
            if not name.endswith(".pth"):
                continue
            full = os.path.join(site_dir, name)
            try:
                lines = [line.strip() for line in open(full, encoding="utf-8", errors="replace")]
            except OSError as exc:
                pth_offenders.append(f"{full} (unreadable: {exc})")
                continue
            for line in lines:
                if not line or line.startswith("#") or line.startswith("import "):
                    continue
                target = line if os.path.isabs(line) else os.path.join(site_dir, line)
                if os.path.isdir(target) and _writable_by_me(target, uid, gids):
                    pth_offenders.append(f"{full} -> {target}")
    checks.record(
        "no .pth file adds a writable directory to sys.path",
        not pth_offenders,
        f"offenders: {pth_offenders}" if pth_offenders else "",
    )

    # 7. The FL framework itself must come from the same read-only install.
    try:
        import nvflare  # noqa: PLC0415 — as above
    except Exception as exc:  # pragma: no cover - defensive
        checks.record("`import nvflare` resolves to site-packages", False, f"import failed: {exc!r}")
    else:
        nvflare_file = os.path.realpath(nvflare.__file__ or "")
        checks.record(
            "`import nvflare` resolves to site-packages",
            nvflare_file.startswith(purelib + os.sep),
            f"nvflare.__file__={nvflare_file}",
        )

    # 8. The workspace is world-writable by design (a kit's FLIP_UID has to create entries in it),
    #    which is only safe if it is sticky: otherwise any uid can unlink or rename the root-owned
    #    .venv beside its own files and substitute one it controls.
    if os.path.isdir(args.workspace):
        ws_mode = os.stat(args.workspace).st_mode
        world_writable = bool(ws_mode & stat.S_IWOTH)
        checks.record(
            "the workspace is sticky if it is world-writable",
            (not world_writable) or bool(ws_mode & stat.S_ISVTX),
            f"mode={stat.filemode(ws_mode)}",
        )
    else:
        checks.record("the workspace is sticky if it is world-writable", True, f"{args.workspace} absent")

    # 9. End to end: NVFLARE builds each job worker's PYTHONPATH from the parent's sys.path
    #    (nvflare.utils.job_launcher_utils.add_custom_dir_to_path) plus the job's own custom dir.
    #    Reproduce that spawn from the workspace, under the kit's PYTHONPATH, and check what the
    #    worker would actually import from. The job custom dir is expected to be writable — that is
    #    the per-job directory FLARE creates — so it is excluded by name, nothing else is.
    job_custom = os.path.join(args.workspace, "simulated-job-id", "app_site", "custom")
    parent_env = dict(os.environ, PYTHONPATH=kit_expansion)
    parent_path = _child_sys_path(parent_env, args.workspace)
    worker_env = dict(parent_env, PYTHONPATH=os.pathsep.join([*parent_path, job_custom]))
    worker_path = _child_sys_path(worker_env, args.workspace)
    worker_offenders = [
        p
        for p in worker_path
        if p != job_custom and os.path.isdir(p) and _writable_by_me(os.path.realpath(p), uid, gids)
    ]
    checks.record(
        "a spawned FLARE job worker imports from no writable directory but its own job dir",
        not worker_offenders,
        f"writable worker path entries: {worker_offenders}" if worker_offenders else f"{len(worker_path)} entries",
    )

    failed = len(checks.failures)
    print(f"\n{failed} of {checks.total} checks failed")
    if failed:
        for name in checks.failures:
            print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
