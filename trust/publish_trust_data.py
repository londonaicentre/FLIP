#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["huggingface_hub>=1.6"]
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
"""Publish a new version of ``aicentreflip/trust-data``: ONE commit on ``main``, ONE tag (FLIP#1100).

The dataset holds exactly one copy of every artefact, at an unversioned path::

    trust<N>/trust<N>_pgdata.tar          vocab-free pgdata volume (make -C omop-db export-pgdata)
    trust<N>/trust<N>_orthanc_data.tar    Orthanc storage volume
    omop-csv/<project>/<table>.csv        canonical OMOP tables (+ source/…), one tree per project
    dicom/<project>.tar.gz                the project's DICOM set (orthanc/publish_dicom.py)
    README.md                             the dataset card

A data version is a git *tag* on the dataset, which is what ``trust/.data_version`` pins and what
every consumer resolves at. So publishing is: upload exactly the files that changed (everything else
stays as it was at the previous tag — nothing is copied, renamed or suffixed), in one commit, then tag
that commit. An existing tag is never moved: a version, once published, means one set of bytes for
good. The commit and the tag are separate Hub calls, so a failure between them leaves the bytes on
``main`` with nothing pinning them — re-run the same command to finish the job.

Usage (from ``trust/``; ``make publish-trust-data`` wraps it)::

    uv run publish_trust_data.py --version 20261001 \\
        --pgdata omop-db/dist/trust1_pgdata.tar omop-db/dist/trust2_pgdata.tar \\
        --omop-csv omop-db/data/canonical \\
        --dicom orthanc/dist/dicom/prostate_project.tar.gz \\
        --card README.md --dry-run

Needs ``hf auth login`` with write access to the dataset. Then bump ``trust/.data_version``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from huggingface_hub import CommitOperationAdd, HfApi

HF_TRUST_DATA_REPO = os.environ.get("HF_TRUST_DATA_REPO", "aicentreflip/trust-data")

# The archive names the trusts fetch. A version suffix in a name is the old layout and is refused:
# the version is the tag, and a suffixed file would be a second copy nobody fetches.
VOLUME_RE = re.compile(r"^(?P<trust>trust\d+)_(?:pgdata|orthanc_data)\.tar$")
VERSIONED_RE = re.compile(r"_\d{8}(?=\.tar(?:\.gz)?$)")
# The shape every published version has had, and what trust/.data_version is expected to hold.
TAG_RE = re.compile(r"\d{8}")


def path_in_repo(local: Path, kind: str) -> str:
    """Where one local artefact lands on the dataset.

    Args:
        local (Path): The file to upload.
        kind (str): ``volume`` (a pgdata or Orthanc tarball), ``dicom`` (a project DICOM set) or
            ``card`` (the README).

    Returns:
        str: The unversioned path in the dataset.

    Raises:
        SystemExit: If the name carries a version suffix, or is not one this layout knows.
    """
    name = local.name
    if VERSIONED_RE.search(name):
        raise SystemExit(f"❌ {name}: the version is the tag, not the filename — rename it without the _<date>")
    if kind == "card":
        return "README.md"
    if kind == "dicom":
        if not name.endswith(".tar.gz"):
            raise SystemExit(f"❌ {name}: a DICOM set is dicom/<project>.tar.gz (see orthanc/publish_dicom.py)")
        return f"dicom/{name}"
    match = VOLUME_RE.match(name)
    if not match:
        raise SystemExit(f"❌ {name}: a volume is trust<N>_pgdata.tar or trust<N>_orthanc_data.tar")
    return f"{match['trust']}/{name}"


def omop_csv_operations(canonical_dir: Path) -> list[CommitOperationAdd]:
    """Every ``<project>/**/*.csv`` under a canonical tree → ``omop-csv/<project>/…``."""
    ops = []
    for csv_path in sorted(canonical_dir.rglob("*.csv")):
        rel = csv_path.relative_to(canonical_dir)
        if len(rel.parts) < 2:
            raise SystemExit(f"❌ {csv_path}: expected <project>/<table>.csv under {canonical_dir}")
        ops.append(CommitOperationAdd(path_in_repo=f"omop-csv/{rel.as_posix()}", path_or_fileobj=str(csv_path)))
    if not ops:
        raise SystemExit(f"❌ no <project>/*.csv under {canonical_dir}")
    return ops


def build_operations(
    volumes: list[Path], omop_csv_dir: Path | None, dicom: list[Path], card: Path | None
) -> list[CommitOperationAdd]:
    """Assemble the single commit's operations; missing local files are refused before anything uploads."""
    planned: list[tuple[str, Path]] = [(path_in_repo(local, "volume"), local) for local in volumes]
    planned += [(path_in_repo(local, "dicom"), local) for local in dicom]
    if card is not None:
        planned.append((path_in_repo(card, "card"), card))
    for _, local in planned:
        if not local.is_file():
            raise SystemExit(f"❌ missing local file: {local}")
    ops = [CommitOperationAdd(path_in_repo=dest, path_or_fileobj=str(local)) for dest, local in planned]
    if omop_csv_dir is not None:
        ops.extend(omop_csv_operations(omop_csv_dir))
    if not ops:
        raise SystemExit("❌ nothing to publish — pass at least one of --pgdata/--orthanc/--omop-csv/--dicom/--card")
    return ops


def publish(api: HfApi, version: str, operations: list[CommitOperationAdd], repo: str, dry_run: bool) -> str | None:
    """One commit, then the tag on that commit. Returns the commit id, or None on a dry run.

    The commit and the tag are two Hub calls, so a failure between them leaves the new bytes on
    ``main`` untagged — nothing pins them, and no consumer resolves them. That is recoverable and
    the message says how: re-running publishes the same artefacts again (unchanged files are a
    no-op for the Hub) and then tags. The one thing that never happens is a *moved* version — an
    existing tag is refused up front.

    Raises:
        SystemExit: If the tag already exists — a version is immutable; publish a new one. Or if
            tagging failed after the commit landed, carrying the commit id and the recovery step.
    """
    tags = {ref.name for ref in api.list_repo_refs(repo, repo_type="dataset").tags}
    if version in tags:
        raise SystemExit(f"❌ tag {version} already exists on {repo} — a version is never moved; pick a new one")
    print(f"📦 {repo} @ {version}: {len(operations)} file(s)")
    for op in operations:
        print(f"   {op.path_in_repo}  <-  {op.path_or_fileobj}")
    if dry_run:
        print("   (dry run — nothing uploaded, nothing tagged)")
        return None
    info = api.create_commit(
        repo,
        repo_type="dataset",
        operations=operations,
        commit_message=f"trust-data {version}: {len(operations)} file(s)",
        commit_description="Published by FLIP/trust/publish_trust_data.py. One copy of every artefact at an "
        f"unversioned path; the data version is the tag {version} on this commit.",
    )
    try:
        api.create_tag(repo, tag=version, revision=info.oid, repo_type="dataset", tag_message=f"trust-data {version}")
    except Exception as exc:
        raise SystemExit(
            f"❌ commit {info.oid} landed on {repo}@main but tagging it {version} failed: {exc}\n"
            f"   The artefacts are uploaded and nothing pins them — no consumer resolves them, so\n"
            f"   nothing is broken, but the version is not published until the tag exists.\n"
            f"   Re-run this same command to finish it: the Hub treats unchanged files as a no-op,\n"
            f"   so it re-commits cheaply and then tags."
        ) from exc
    print(f"✅ commit {info.oid} tagged {version}. Now set trust/.data_version to {version}.")
    return info.oid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True, help="the new data-version tag, e.g. 20261001")
    parser.add_argument("--pgdata", nargs="*", type=Path, default=[], help="trust<N>_pgdata.tar files")
    parser.add_argument("--orthanc", nargs="*", type=Path, default=[], help="trust<N>_orthanc_data.tar files")
    parser.add_argument("--omop-csv", type=Path, default=None, help="canonical tree: <dir>/<project>/<table>.csv")
    parser.add_argument("--dicom", nargs="*", type=Path, default=[], help="dicom/<project>.tar.gz files")
    parser.add_argument("--card", type=Path, default=None, help="the dataset README.md")
    parser.add_argument("--repo", default=HF_TRUST_DATA_REPO)
    parser.add_argument("--dry-run", action="store_true", help="list the operations; upload and tag nothing")
    parser.add_argument("--allow-any-tag", action="store_true", help="publish a --version that is not a YYYYMMDD date")
    args = parser.parse_args(argv)

    # A filename carrying a version is refused outright (VERSIONED_RE), so the tag — the half that
    # cannot be corrected by re-uploading — should be at least as strict. A typo like 2026101 would
    # otherwise publish a tag no .data_version will ever pin.
    if not args.allow_any_tag and not TAG_RE.fullmatch(args.version):
        raise SystemExit(f"❌ a data version is YYYYMMDD, got {args.version!r} — pass --allow-any-tag to override")

    operations = build_operations(args.pgdata + args.orthanc, args.omop_csv, args.dicom, args.card)
    publish(HfApi(), args.version, operations, args.repo, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
