#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["requests>=2.33"]
#
# [tool.uv]
# exclude-newer = "3 days"
# ///
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
"""Fetch the documentation GIFs from the ``aicentreflip/docs-gifs`` dataset (FLIP#1236, FLIP#1547).

The user-guide GIFs are not tracked in git. Every recording from ``develop`` is one commit on the dataset's
``main`` (``publish_docs_gifs.py``), and ``release.yml`` tags the one each release ships as ``vX.Y.Z``
(``freeze_docs_gifs.py``). ``source/conf.py`` calls :func:`fetch` before Sphinx reads the sources, trying the
revisions :func:`revisions_to_try` picks for the build at hand::

    release tag vX.Y.Z                 vX.Y.Z, else main
    git main, and ReadTheDocs' stable  v<the version in pyproject.toml>, else main
    PR preview #N                      pr-N (a hand recording, see publish_docs_gifs.py), else main
    anything else (latest, local)      main

The first that exists is resolved to its commit, and the manifest and every file are read at that commit, so a
recording published mid-build cannot mix two sets::

    https://huggingface.co/datasets/<repo>/resolve/<commit>/manifest.json
    https://huggingface.co/datasets/<repo>/resolve/<commit>/<category>/<name>.gif

Each file is streamed to ``<file>.part``, checked against the manifest's sha256 and size, and only then renamed
into place; a file whose local bytes already match is not downloaded. The manifest is written last, so its
presence means every file it lists was verified. Files under the destination that the manifest does not list are
left alone (``make clean`` wipes the tree).

Anonymous by construction: no token is read. The dataset is public, and the CI publish job runs this script
without credentials to prove that a fresh recording resolves for everyone.

Usage (``FLIP_DOCS_GIFS_REPO`` / ``FLIP_DOCS_GIFS_REVISION`` override the dataset and the revision)::

    uv run docs/scripts/fetch_docs_gifs.py                              # -> source/assets/generated/gifs/
    uv run docs/scripts/fetch_docs_gifs.py --revision v0.12.0 --dest /tmp/verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

DOCS_DIR = Path(__file__).resolve().parents[1]
PYPROJECT = DOCS_DIR.parent / "pyproject.toml"
DEFAULT_REPO = "aicentreflip/docs-gifs"
REPO_ENV = "FLIP_DOCS_GIFS_REPO"
REVISION_ENV = "FLIP_DOCS_GIFS_REVISION"
DEFAULT_DEST = DOCS_DIR / "source" / "assets" / "generated" / "gifs"
MANIFEST_NAME = "manifest.json"
HF_BASE_URL = "https://huggingface.co/datasets"
HF_API_URL = "https://huggingface.co/api/datasets"
RELEASE_RE = re.compile(r"v\d+\.\d+\.\d+")

ATTEMPTS = 3
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = 60
CHUNK_BYTES = 1 << 16
# A manifest path is <category>/<name>.gif and nothing else — no `..`, no absolute path, no other suffix — so a
# manifest can never direct a write outside the destination.
SAFE_PATH_RE = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+\.gif")
SHA256_RE = re.compile(r"[0-9a-f]{64}")

Log = Callable[[str], None]
Sleep = Callable[[float], None]


class FetchError(Exception):
    """A fetch or verification failure. The message names the URL and what went wrong."""


class _Missing(FetchError):
    """HTTP 404: the revision, or a file at it, is not on the dataset."""


class _Retry(Exception):
    """A transient failure: retried up to ATTEMPTS times, after waiting ``wait`` seconds."""

    def __init__(self, reason: str, wait: float):
        super().__init__(reason)
        self.reason = reason
        self.wait = wait


@dataclass
class FetchReport:
    """What one :func:`fetch` did."""

    repo: str
    revision: str
    commit: str
    version: str
    fetched: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.repo}@{self.revision} ({self.commit[:7]}, manifest version {self.version}): "
            f"{len(self.fetched)} fetched, {len(self.verified)} already present and verified"
        )


def repo_from_env() -> str:
    """The dataset: ``$FLIP_DOCS_GIFS_REPO``, else :data:`DEFAULT_REPO`."""
    return os.environ.get(REPO_ENV) or DEFAULT_REPO


def project_version(pyproject: Path = PYPROJECT) -> str:
    """The platform version, read from the file ``release.yml`` reads it from to name the release."""
    return tomllib.loads(pyproject.read_text())["project"]["version"]


def revisions_to_try(explicit: str | None = None, environ: Mapping[str, str] = os.environ) -> list[str]:
    """The dataset revisions for this build, best first (the table in the module docstring).

    An explicit revision, else ``$FLIP_DOCS_GIFS_REVISION``, is the only one tried. Otherwise ReadTheDocs' build
    variables say what is being built; a build without them (a contributor's) gets ``main``.
    """
    chosen = explicit or environ.get(REVISION_ENV)
    if chosen:
        return [chosen]
    name = environ.get("READTHEDOCS_VERSION_NAME", "")
    if environ.get("READTHEDOCS_VERSION_TYPE") == "external":
        return [f"pr-{name}", "main"]
    if RELEASE_RE.fullmatch(name):
        return [name, "main"]
    if name in ("main", "stable"):
        return [f"v{project_version()}", "main"]
    return ["main"]


def resolve_url(repo: str, revision: str, path: str) -> str:
    """The anonymous download URL of one file at one revision."""
    return f"{HF_BASE_URL}/{repo}/resolve/{revision}/{path}"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_manifest(raw: bytes, source: str) -> dict[str, Any]:
    """Validate a manifest's shape; ``source`` names where it came from in any error.

    Raises:
        FetchError: Unless it is ``{"version": str, "files": {"<category>/<name>.gif": {"sha256", "bytes"}}}``.
    """
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise FetchError(f"{source}: manifest is not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        raise FetchError(f"{source}: manifest must be an object with a string 'version'")
    if not isinstance(data.get("files"), dict):
        raise FetchError(f"{source}: manifest must carry an object 'files'")
    for path, entry in data["files"].items():
        if not SAFE_PATH_RE.fullmatch(path):
            raise FetchError(f"{source}: manifest path {path!r} is not <category>/<name>.gif")
        sha = entry.get("sha256") if isinstance(entry, dict) else None
        size = entry.get("bytes") if isinstance(entry, dict) else None
        sha_ok = isinstance(sha, str) and SHA256_RE.fullmatch(sha) is not None
        size_ok = isinstance(size, int) and not isinstance(size, bool) and size >= 0
        if not (sha_ok and size_ok):
            raise FetchError(f"{source}: entry {path!r} needs a 64-hex 'sha256' and a non-negative integer 'bytes'")
    return data


def _matches(path: Path, sha256: str, size: int) -> bool:
    return path.is_file() and path.stat().st_size == size and sha256_of(path) == sha256


def _backoff(attempt: int) -> float:
    return float(2 ** (attempt - 1))  # 1 s after the first failure, 2 s after the second


def _retry_after(response: requests.Response, attempt: int) -> float:
    """Honour a numeric Retry-After (capped); fall back to the backoff for an absent or HTTP-date value."""
    try:
        return min(float(response.headers.get("Retry-After", "")), float(MAX_RETRY_AFTER_SECONDS))
    except ValueError:
        return _backoff(attempt)


def _open(session: requests.Session, url: str, *, stream: bool, attempt: int) -> requests.Response:
    """One GET. A transient outcome raises _Retry; a definitive failure raises FetchError."""
    try:
        response = session.get(url, stream=stream, timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS))
    except requests.RequestException as exc:
        raise _Retry(f"{type(exc).__name__}: {exc}", _backoff(attempt)) from exc
    if response.status_code == 200:
        return response
    response.close()
    if response.status_code in (429, 503):
        raise _Retry(f"HTTP {response.status_code}", _retry_after(response, attempt))
    if response.status_code >= 500:
        raise _Retry(f"HTTP {response.status_code}", _backoff(attempt))
    if response.status_code == 404:
        raise _Missing(f"{url}: HTTP 404 — does that revision exist on the dataset?")
    raise FetchError(f"{url}: HTTP {response.status_code}")


def _with_retries(action: Callable[[int], Any], url: str, log: Log, sleep: Sleep) -> Any:
    reason = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return action(attempt)
        except _Retry as exc:
            reason = exc.reason
            if attempt < ATTEMPTS:
                log(f"retrying {url} in {exc.wait:.0f}s (attempt {attempt} of {ATTEMPTS} failed: {reason})")
                sleep(exc.wait)
    raise FetchError(f"{url}: giving up after {ATTEMPTS} attempts ({reason})")


def _read(session: requests.Session, url: str, attempt: int) -> bytes:
    response = _open(session, url, stream=False, attempt=attempt)
    try:
        return response.content
    finally:
        response.close()


def _download(session: requests.Session, url: str, target: Path, sha256: str, size: int, attempt: int) -> None:
    """Stream one file to ``<target>.part``, verify it, rename it into place."""
    part = target.with_name(target.name + ".part")
    response = _open(session, url, stream=True, attempt=attempt)
    digest = hashlib.sha256()
    received = 0
    try:
        with part.open("wb") as fh:
            for chunk in response.iter_content(CHUNK_BYTES):
                fh.write(chunk)
                digest.update(chunk)
                received += len(chunk)
    except requests.RequestException as exc:
        part.unlink(missing_ok=True)
        raise _Retry(f"{type(exc).__name__} mid-transfer: {exc}", _backoff(attempt)) from exc
    finally:
        response.close()
    if received != size or digest.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise FetchError(
            f"{url}: the manifest promises {size} bytes with sha256 {sha256}, "
            f"got {received} bytes with sha256 {digest.hexdigest()}"
        )
    os.replace(part, target)


def _write_manifest(dest: Path, manifest: dict[str, Any]) -> None:
    final = dest / MANIFEST_NAME
    part = final.with_name(final.name + ".part")
    part.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.replace(part, final)


def resolve(repo: str, revisions: Sequence[str], session: requests.Session, log: Log, sleep: Sleep) -> tuple[str, str]:
    """The first of ``revisions`` that exists on the dataset, and the commit it points at.

    Raises:
        FetchError: If none of them exists, or the Hub cannot be reached.
    """
    for revision in revisions:
        url = f"{HF_API_URL}/{repo}/revision/{quote(revision, safe='')}"
        try:
            raw = _with_retries(partial(_read, session, url), url, log, sleep)
        except _Missing:
            log(f"no {revision} on {repo}")
            continue
        return revision, json.loads(raw)["sha"]
    raise FetchError(f"none of {', '.join(revisions)} exists on {repo}")


def fetch(
    repo: str,
    revisions: Sequence[str],
    dest: Path,
    *,
    session: requests.Session | None = None,
    log: Log = lambda _message: None,
    sleep: Sleep = time.sleep,
) -> FetchReport:
    """Bring ``dest`` to the manifest of the first of ``revisions`` on ``repo``, fetching only what differs.

    Args:
        repo (str): The dataset, ``<owner>/<name>``.
        revisions (Sequence[str]): Tags, branches or commits to try, best first (:func:`revisions_to_try`).
        dest (Path): Where the ``<category>/<name>.gif`` files and the manifest land.
        session (requests.Session | None): The HTTP session; a fresh one when None. Tests pass a fake.
        log (Callable[[str], None]): Where progress lines go (the Sphinx logger, or ``print``).
        sleep (Callable[[float], None]): How to wait between attempts; tests pass a recorder.

    Returns:
        FetchReport: The revision used, its commit, and what was fetched and what was already present.

    Raises:
        FetchError: On any lookup, download, verification, or manifest failure — naming the URL.
    """
    session = session or requests.Session()
    revision, commit = resolve(repo, revisions, session, log, sleep)
    manifest_url = resolve_url(repo, commit, MANIFEST_NAME)
    raw = _with_retries(partial(_read, session, manifest_url), manifest_url, log, sleep)
    manifest = parse_manifest(raw, manifest_url)
    report = FetchReport(repo=repo, revision=revision, commit=commit, version=manifest["version"])
    dest.mkdir(parents=True, exist_ok=True)
    for path, entry in sorted(manifest["files"].items()):
        target = dest / path
        if _matches(target, entry["sha256"], entry["bytes"]):
            report.verified.append(path)
            continue
        url = resolve_url(repo, commit, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        _with_retries(partial(_download, session, url, target, entry["sha256"], entry["bytes"]), url, log, sleep)
        report.fetched.append(path)
        log(f"fetched {path} ({entry['bytes']} bytes)")
    _write_manifest(dest, manifest)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=None, help=f"the dataset (default ${REPO_ENV}, else {DEFAULT_REPO})")
    parser.add_argument("--revision", default=None, help=f"tag, branch or commit (default ${REVISION_ENV}, else main)")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="where the GIFs land")
    args = parser.parse_args(argv)
    try:
        report = fetch(args.repo or repo_from_env(), revisions_to_try(args.revision), args.dest, log=print)
    except FetchError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    print(f"✅ {report.summary()} → {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
