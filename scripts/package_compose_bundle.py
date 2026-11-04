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
"""Package the compose trust's deployment assets as one versioned bundle (FLIP#1753).

A Kubernetes trust upgrades to a published chart (FLIP#1646). A compose trust upgrades by running
the Makefile chain out of a git checkout, which is why its agent has to clone the repository, check
the tree is clean and check it sits on a release tag before it dares deploy. This replaces "the
checkout" with one immutable artifact::

    flip-compose-bundle-v0.13.0.tar.gz

built from the explicit manifest in ``deploy/compose.bundle.manifest`` rather than from a rule
about what to leave out. The difference matters: an exclusion rule ships whatever was added last
week and nobody thought about, and the failure mode is a trust that upgrades to something that
cannot run its own Makefile. A manifest that misses a file fails here, at packaging time, with the
name of the pattern — not on a site.

Two things the builder refuses outright:

* any ``.env*`` file, because the bundle is downloaded by a trust and published publicly, and a
  kit file is the trust's API key;
* a manifest pattern that matches nothing, because a renamed file must be a packaging failure
  rather than a silent hole in the artifact.

The tarball is reproducible: members are sorted, ownership is zeroed and every mtime is
``SOURCE_DATE_EPOCH`` (or the commit's own time), so the same commit packages to the same bytes.
That is what lets a trust verify a download against a checksum published beside it.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "deploy" / "compose.bundle.manifest"
BUNDLE_NAME = "flip-compose-bundle"
MANIFEST_INSIDE = "BUNDLE.json"

_VERSION = re.compile(r"^v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$")


class BundleError(RuntimeError):
    """The manifest or the tree cannot produce a bundle worth shipping."""


def normalise_version(raw: str) -> str:
    """``0.13.0`` or ``v0.13.0`` -> ``v0.13.0``; anything else raises.

    One spelling everywhere: the bundle's file name, ``BUNDLE.json`` and the release asset all use
    the git tag's form, so a site never has to guess which of two spellings it is holding.
    """
    match = _VERSION.match(raw.strip())
    if not match:
        raise BundleError(f"{raw!r} is not a release version (expected X.Y.Z or vX.Y.Z)")
    return f"v{match.group(1)}"


def read_manifest(text: str) -> tuple[list[str], list[str]]:
    """Split the manifest into (include patterns, exclude patterns), comments and blanks dropped."""
    include: list[str] = []
    exclude: list[str] = []
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("!"):
            exclude.append(entry[1:].strip())
        else:
            include.append(entry)
    if not include:
        raise BundleError("the manifest lists nothing to include")
    return include, exclude


def _is_under(path: str, pattern: str) -> bool:
    """Whether a repo-relative path is the pattern's directory or sits under it."""
    directory = pattern.rstrip("/")
    return path == directory or path.startswith(f"{directory}/")


def _matches(path: str, pattern: str) -> bool:
    """Whether one manifest entry selects this path.

    Directory entries (trailing ``/``) take everything beneath them; anything else is matched whole
    with shell globs, where ``*`` does not cross a ``/`` — the same rule git's pathspecs use, so a
    manifest line means what it looks like it means.
    """
    if pattern.endswith("/"):
        return _is_under(path, pattern)
    if "*" not in pattern and not pattern.endswith("/"):
        return path == pattern
    parts = path.split("/")
    pattern_parts = pattern.split("/")

    def walk(i: int, j: int) -> bool:
        if i == len(parts) and j == len(pattern_parts):
            return True
        if j == len(pattern_parts):
            return False
        if pattern_parts[j] == "**":
            return any(walk(i + k, j + 1) for k in range(len(parts) - i + 1))
        if i == len(parts):
            return False
        return fnmatch.fnmatchcase(parts[i], pattern_parts[j]) and walk(i + 1, j + 1)

    return walk(0, 0)


def select_files(manifest_text: str, repo_root: Path = REPO_ROOT) -> list[str]:
    """Every repository file the manifest selects, sorted, with secrets refused.

    Raises:
        BundleError: a pattern matches nothing, or the selection contains a ``.env`` file.
    """
    include, exclude = read_manifest(manifest_text)
    tracked = _tracked_files(repo_root)

    chosen: set[str] = set()
    for pattern in include:
        hits = [path for path in tracked if _matches(path, pattern)]
        if not hits:
            raise BundleError(f"manifest entry {pattern!r} matches no tracked file")
        chosen.update(hits)

    for pattern in exclude:
        # An exclusion names a directory and takes everything under it, which is the direction that
        # fails safe: a file added to an excluded tree stays out without a second manifest line.
        chosen = {path for path in chosen if not _is_under(path, pattern)}

    if not chosen:
        raise BundleError("the manifest selects nothing after exclusions")

    # A kit file is a trust's API key. It is gitignored, so this can only trip on a manifest entry
    # that starts selecting it -- which is exactly the accident worth failing the release for.
    secrets = sorted(path for path in chosen if Path(path).name.startswith(".env"))
    if secrets:
        raise BundleError(f"the bundle would ship environment files: {', '.join(secrets)}")

    return sorted(chosen)


def _tracked_files(repo_root: Path) -> list[str]:
    """The files git tracks, which is the definition of "part of the release".

    Tracked rather than "everything on disk" so a local experiment, a workspace or a build output
    cannot end up in an artifact a trust downloads.
    """
    out = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        capture_output=True,
        check=True,
    )
    return [path for path in out.stdout.decode().split("\0") if path]


def source_date_epoch(repo_root: Path = REPO_ROOT) -> int:
    """The commit's time, or ``SOURCE_DATE_EPOCH`` when the caller sets one."""
    override = os.environ.get("SOURCE_DATE_EPOCH")
    if override:
        try:
            return int(override)
        except ValueError as exc:
            raise BundleError(f"SOURCE_DATE_EPOCH={override!r} is not a Unix time") from exc
    out = subprocess.run(
        ["git", "-C", str(repo_root), "log", "-1", "--format=%ct"],
        capture_output=True,
        check=True,
    )
    return int(out.stdout.decode().strip())


def bundle_manifest(version: str, commit: str, files: list[str]) -> bytes:
    """The ``BUNDLE.json`` shipped inside the tarball: what this is, and what it holds."""
    payload = {
        "bundle": BUNDLE_NAME,
        "release": version,
        "commit": commit,
        "files": files,
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()


def build(
    version: str,
    out_dir: Path,
    repo_root: Path = REPO_ROOT,
    manifest: Path = MANIFEST,
    epoch: int | None = None,
) -> Path:
    """Write ``<out_dir>/flip-compose-bundle-<version>.tar.gz`` and return its path."""
    release = normalise_version(version)
    files = select_files(manifest.read_text(), repo_root)
    stamp = source_date_epoch(repo_root) if epoch is None else epoch
    commit = (
        subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], capture_output=True, check=True)
        .stdout.decode()
        .strip()
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{BUNDLE_NAME}-{release}.tar.gz"
    # `mtime=` on the tarfile plus zeroed ownership is what makes two builds of one commit identical.
    with tarfile.open(target, "w:gz", compresslevel=9) as tar:
        for path in files:
            _add(tar, repo_root / path, path, stamp)
        _add_bytes(tar, MANIFEST_INSIDE, bundle_manifest(release, commit, files), stamp)
    return target


def _add(tar: tarfile.TarFile, source: Path, name: str, stamp: int) -> None:
    info = tar.gettarinfo(str(source), arcname=name)
    info.mtime = stamp
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o755 if source.stat().st_mode & 0o111 else 0o644
    with source.open("rb") as handle:
        tar.addfile(info, handle)


def _add_bytes(tar: tarfile.TarFile, name: str, payload: bytes, stamp: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mtime = stamp
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(payload))


def checksum_for(bundle_path: Path) -> Path:
    """The ``.sha256`` beside the tarball.

    Appended rather than :meth:`Path.with_suffix`, which would turn ``…-v0.13.0.tar.gz`` into
    ``…-v0.13.0.tar.sha256`` -- a name nothing (and no ``sha256sum -c``) expects.
    """
    return bundle_path.with_name(f"{bundle_path.name}.sha256")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", help="the release tag, vX.Y.Z (or X.Y.Z)")
    parser.add_argument("--out", default="dist", help="where to write the tarball (default: dist/)")
    parser.add_argument("--checksum", action="store_true", help="also write the .sha256 beside it")
    args = parser.parse_args(argv)

    try:
        path = build(args.version, Path(args.out))
    except (BundleError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    digest = sha256(path)
    if args.checksum:
        checksum_for(path).write_text(f"{digest}  {path.name}\n")
    print(f"{path}  sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
