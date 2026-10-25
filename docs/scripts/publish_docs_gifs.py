#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["huggingface_hub>=1.6"]
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
"""Publish one recording of the documentation GIFs to ``aicentreflip/docs-gifs`` as ONE commit (FLIP#1236).

The dataset holds one copy of every documentation GIF, at an unversioned path::

    admin/<name>.gif    one per flip-ui/test/cypress/docs/admin/<name>.spec.ts
    flip/<name>.gif     one per flip-ui/test/cypress/docs/flip/<name>.spec.ts
    <cat>/<name>.gif    one per line of docs/scripts/live-recorded-gifs.txt (recorded on a live trust)
    manifest.json       version, source_commit, recorded_at, workflow_run, per-file sha256 + bytes
    README.md           the dataset card (docs/scripts/docs_gifs_card.md)

Every commit on the dataset's ``main`` is one recording from ``develop``, published by the CI workflow; docs
builds fetch the newest, and ``release.yml`` tags the one each release ships (``freeze_docs_gifs.py``,
FLIP#1547). A recording made by hand — GIFs CI cannot record, such as ones that need a live trust — goes to the
branch ``pr-<N>`` of the PR that documents them, which that PR's ReadTheDocs preview fetches.

The GIFs listed in ``docs/scripts/live-recorded-gifs.txt`` are only ever recorded by hand. One missing from
``--gifs-dir`` is carried forward from the first dataset ref that has it — each ``--carry-from`` (the CI workflow
passes the merged PR's ``pr-<N>``, so the hand recording that PR previewed reaches ``main``), then the target
branch, then ``main`` — checked against that ref's manifest.

A recording's version is ``YYYYMMDDTHHMMSSZ-<sha7>``: the UTC instant it started and the commit it was recorded
from (the CI workflow mints it before Cypress runs, and the manifest's ``recorded_at`` is read back from it).
Publishing is: add the GIF of every demo spec and live recording, delete the branch's GIFs no longer called for,
add the manifest and the card, in one commit.

Usage (needs ``hf auth login`` with write access, or ``HF_TOKEN``; the CI publish job passes ``--branch main``,
``--version``, ``--source-commit`` and ``--workflow-run`` explicitly)::

    uv run docs/scripts/publish_docs_gifs.py --branch pr-945 --source-commit "$(git rev-parse HEAD)" --dry-run
    uv run docs/scripts/publish_docs_gifs.py --branch pr-945 --source-commit "$(git rev-parse HEAD)"

Then rebuild the PR's ReadTheDocs preview.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi, hf_hub_download
from huggingface_hub.errors import EntryNotFoundError, RevisionNotFoundError

DOCS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = DOCS_DIR.parent
DEFAULT_REPO = os.environ.get("FLIP_DOCS_GIFS_REPO", "aicentreflip/docs-gifs")
DEFAULT_GIFS_DIR = DOCS_DIR / "source" / "assets" / "generated" / "gifs"
DEFAULT_SPECS_DIR = REPO_ROOT / "flip-ui" / "test" / "cypress" / "docs"
DEFAULT_CARD = DOCS_DIR / "scripts" / "docs_gifs_card.md"
DEFAULT_LIVE_LIST = DOCS_DIR / "scripts" / "live-recorded-gifs.txt"
GIF_PATH_RE = re.compile(r"[a-z-]+/[a-z0-9-]+\.gif")
MANIFEST_NAME = "manifest.json"
SPEC_SUFFIX = ".spec.ts"
# Directories under the specs tree that hold support code rather than recordings.
NON_SPEC_DIRS = frozenset({"support"})
VERSION_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
RECORDED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
VERSION_RE = re.compile(r"(?P<time>\d{8}T\d{6}Z)-(?P<sha7>[0-9a-f]{7})")
COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
# main holds the CI recordings from develop; pr-<N> a hand recording for PR #N's preview (fetch_docs_gifs.py).
BRANCH_RE = re.compile(r"main|pr-\d+")

Operation = CommitOperationAdd | CommitOperationDelete


def live_gifs(live_list: Path | None) -> list[str]:
    """The ``<category>/<name>.gif`` lines of the live-recorded list (comments and blanks skipped).

    Raises:
        SystemExit: If a line is not a ``<category>/<name>.gif`` path.
    """
    if live_list is None or not live_list.is_file():
        return []
    paths = []
    for line in live_list.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if not GIF_PATH_RE.fullmatch(line):
            raise SystemExit(f"❌ {live_list}: {line!r} is not <category>/<name>.gif (lowercase, hyphens)")
        paths.append(line)
    return paths


def expected_gifs(specs_dir: Path, live_list: Path | None = None) -> list[str]:
    """``<category>/<name>.gif`` for every ``<category>/<name>.spec.ts`` under the specs tree, plus every GIF
    recorded on a live trust (``live_list``), sorted.

    Raises:
        SystemExit: If a spec is not exactly one directory deep, there are none, or a live-recorded GIF has the
            same path as a spec's.

    ``live_list`` defaults to docs/scripts/live-recorded-gifs.txt, read at call time; a path that does not
    exist means none.
    """
    paths = []
    for spec in sorted(specs_dir.rglob(f"*{SPEC_SUFFIX}")):
        rel = spec.relative_to(specs_dir)
        if len(rel.parts) != 2:
            raise SystemExit(f"❌ {spec}: a demo spec is <category>/<name>{SPEC_SUFFIX} directly under {specs_dir}")
        category, filename = rel.parts
        if category in NON_SPEC_DIRS:
            continue
        paths.append(f"{category}/{filename.removesuffix(SPEC_SUFFIX)}.gif")
    if not paths:
        raise SystemExit(f"❌ no <category>/<name>{SPEC_SUFFIX} under {specs_dir}")
    live = live_gifs(DEFAULT_LIVE_LIST if live_list is None else live_list)
    clash = sorted(set(paths) & set(live))
    if clash:
        raise SystemExit(f"❌ {', '.join(clash)}: both a Cypress spec and a live recording; keep one")
    return sorted(paths + live)


def carry_forward(gifs_dir: Path, live: list[str], repo: str, refs: list[str], download) -> list[str]:
    """Copy each live-recorded GIF missing from ``gifs_dir`` from the first of ``refs`` that has it; returns those
    copied.

    A CI publish records only the Cypress GIFs, so without this every publish would drop the live ones.
    ``download(filename, ref) -> local path`` fetches one file at one dataset ref; each GIF is checked against the
    sha256 that ref's manifest records, so a carried GIF is byte-for-byte the one already published.

    Raises:
        SystemExit: If a live GIF is missing locally and no ref has it, or its bytes do not match the manifest.
    """
    manifests: dict[str, dict[str, Any]] = {}

    def files_at(ref: str) -> dict[str, Any]:
        if ref not in manifests:
            try:
                manifests[ref] = json.loads(Path(download(MANIFEST_NAME, ref)).read_text()).get("files", {})
            except (RevisionNotFoundError, EntryNotFoundError):
                manifests[ref] = {}  # a pr-<N> nobody published, or a ref from before the manifest
        return manifests[ref]

    missing = [path for path in live if not (gifs_dir / path).is_file()]
    for path in missing:
        ref = next((ref for ref in refs if path in files_at(ref)), None)
        if ref is None:
            raise SystemExit(
                f"❌ {path} is on none of {', '.join(refs)} in {repo} — record it on a live trust first "
                "(docs/scripts/live-recorded-gifs.txt says how)"
            )
        source = Path(download(path, ref))
        if sha256_of(source) != files_at(ref)[path]["sha256"]:
            raise SystemExit(f"❌ {path} from {repo}@{ref} does not match that ref's manifest")
        target = gifs_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        print(f"↪️  carried {path} forward from {ref}")
    return missing


def make_version(source_commit: str, now: datetime) -> str:
    """``YYYYMMDDTHHMMSSZ-<sha7>`` for GIFs recorded from ``source_commit`` starting at ``now``."""
    return f"{now.astimezone(UTC).strftime(VERSION_TIME_FORMAT)}-{source_commit[:7]}"


def version_timestamp(version: str) -> str | None:
    """The ISO-8601 instant a well-formed version encodes, or None for any other version."""
    match = VERSION_RE.fullmatch(version)
    if match is None:
        return None
    return datetime.strptime(match["time"], VERSION_TIME_FORMAT).replace(tzinfo=UTC).strftime(RECORDED_AT_FORMAT)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    gifs_dir: Path, paths: list[str], *, version: str, source_commit: str, recorded_at: str, workflow_run: str | None
) -> dict[str, Any]:
    """The manifest of one version: provenance plus sha256 + size of every GIF.

    Raises:
        SystemExit: If a spec's GIF is missing under ``gifs_dir`` — before anything is uploaded.
    """
    missing = [path for path in paths if not (gifs_dir / path).is_file()]
    if missing:
        raise SystemExit(
            f"❌ every demo spec needs its GIF under {gifs_dir}; missing: {', '.join(missing)}\n"
            "   Record them first: cd flip-ui && npm run docs:record && npm run docs:gifs"
        )
    files = {
        path: {"sha256": sha256_of(gifs_dir / path), "bytes": (gifs_dir / path).stat().st_size}
        for path in sorted(paths)
    }
    return {
        "version": version,
        "source_commit": source_commit,
        "recorded_at": recorded_at,
        "workflow_run": workflow_run,
        "files": files,
    }


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """The manifest as it is stored: canonical JSON, sorted keys, trailing newline."""
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()


def build_operations(
    gifs_dir: Path, manifest: dict[str, Any], published: list[str], card: Path | None
) -> list[Operation]:
    """Adds for every manifest GIF, the manifest and the card; deletes for published GIFs no spec calls for."""
    expected = set(manifest["files"])
    strays = [
        p for p in sorted(g.relative_to(gifs_dir).as_posix() for g in gifs_dir.rglob("*.gif")) if p not in expected
    ]
    if strays:
        print(f"⚠️  ignoring {len(strays)} GIF(s) under {gifs_dir} with no spec: {', '.join(strays)}")
    if card is not None and not card.is_file():
        raise SystemExit(f"❌ missing card: {card}")
    ops: list[Operation] = [
        CommitOperationAdd(path_in_repo=path, path_or_fileobj=str(gifs_dir / path)) for path in sorted(expected)
    ]
    ops.append(CommitOperationAdd(path_in_repo=MANIFEST_NAME, path_or_fileobj=manifest_bytes(manifest)))
    if card is not None:
        ops.append(CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=str(card)))
    ops.extend(
        CommitOperationDelete(path_in_repo=path)
        for path in sorted(published)
        if path.endswith(".gif") and path not in expected
    )
    return ops


def publish(
    api: HfApi, version: str, operations: list[Operation], repo: str, branch: str, *, new_branch: bool, dry_run: bool
) -> str | None:
    """One commit on ``branch``, branching it from ``main`` first when it is new. Returns the commit id, or None
    on a dry run."""
    adds = [op.path_in_repo for op in operations if isinstance(op, CommitOperationAdd)]
    deletes = [op.path_in_repo for op in operations if isinstance(op, CommitOperationDelete)]
    print(f"📦 {repo}@{branch} {version}: {len(adds)} file(s) to add, {len(deletes)} to delete")
    for path in adds:
        print(f"   + {path}")
    for path in deletes:
        print(f"   - {path}")
    if dry_run:
        print("   (dry run — nothing uploaded)")
        return None
    if new_branch:
        api.create_branch(repo, branch=branch, revision="main", repo_type="dataset")
    info = api.create_commit(
        repo,
        repo_type="dataset",
        revision=branch,
        operations=operations,
        commit_message=f"docs-gifs {version}: {len(adds)} added, {len(deletes)} deleted",
        commit_description="Published by FLIP/docs/scripts/publish_docs_gifs.py. One copy of every GIF at an "
        "unversioned path.",
    )
    print(f"✅ commit {info.oid} on {branch}: https://huggingface.co/datasets/{repo}/tree/{branch}")
    return info.oid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--branch", required=True, help="pr-<N> for a hand recording; main is the CI workflow's")
    parser.add_argument("--source-commit", required=True, help="the commit the GIFs were recorded from (full sha)")
    parser.add_argument("--version", default=None, help="default <now, UTC>-<sha7 of --source-commit>")
    parser.add_argument("--workflow-run", default=None, help="URL of the Actions run that recorded them (omit by hand)")
    parser.add_argument("--gifs-dir", type=Path, default=DEFAULT_GIFS_DIR, help="the <category>/<name>.gif tree")
    parser.add_argument("--specs-dir", type=Path, default=DEFAULT_SPECS_DIR, help="the demo specs defining the set")
    parser.add_argument("--card", type=Path, default=DEFAULT_CARD, help="the dataset card, published as README.md")
    parser.add_argument(
        "--live-list", type=Path, default=DEFAULT_LIVE_LIST, help="GIFs recorded on a live trust, not by Cypress"
    )
    parser.add_argument(
        "--carry-from",
        action="append",
        default=[],
        help="a dataset ref to take a missing live-recorded GIF from, before --branch and main (repeatable)",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--dry-run", action="store_true", help="list the operations; upload nothing")
    parser.add_argument("--allow-any-version", action="store_true", help="publish a --version of another shape")
    args = parser.parse_args(argv)

    if not BRANCH_RE.fullmatch(args.branch):
        raise SystemExit(f"❌ --branch is main or pr-<N>, got {args.branch!r}")
    if args.branch == "main" and not args.workflow_run:
        # Every docs build without a recording of its own shows main: keep it to what CI recorded from develop.
        raise SystemExit("❌ only the CI workflow publishes main (it passes --workflow-run); use --branch pr-<N>")
    if not COMMIT_SHA_RE.fullmatch(args.source_commit):
        raise SystemExit(f"❌ --source-commit must be a full 40-hex commit sha, got {args.source_commit!r}")
    now = datetime.now(UTC)
    version = args.version or make_version(args.source_commit, now)
    match = VERSION_RE.fullmatch(version)
    if not args.allow_any_version:
        # The shape every consumer expects, and a sha7 that really is the commit the manifest will name.
        if match is None:
            raise SystemExit(
                f"❌ a version is YYYYMMDDTHHMMSSZ-<sha7>, got {version!r} — pass --allow-any-version to override"
            )
        if match["sha7"] != args.source_commit[:7]:
            raise SystemExit(f"❌ {version} does not name --source-commit {args.source_commit[:7]}")
    recorded_at = version_timestamp(version) or now.strftime(RECORDED_AT_FORMAT)

    expected = expected_gifs(args.specs_dir, args.live_list)
    carry_forward(
        args.gifs_dir,
        live_gifs(args.live_list),
        args.repo,
        list(dict.fromkeys([*args.carry_from, args.branch, "main"])),
        lambda filename, ref: hf_hub_download(args.repo, filename, repo_type="dataset", revision=ref),
    )
    manifest = build_manifest(
        args.gifs_dir,
        expected,
        version=version,
        source_commit=args.source_commit,
        recorded_at=recorded_at,
        workflow_run=args.workflow_run,
    )
    api = HfApi()
    new_branch = args.branch not in {ref.name for ref in api.list_repo_refs(args.repo, repo_type="dataset").branches}
    published = api.list_repo_files(args.repo, repo_type="dataset", revision="main" if new_branch else args.branch)
    operations = build_operations(args.gifs_dir, manifest, published, args.card)
    publish(api, version, operations, args.repo, args.branch, new_branch=new_branch, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
