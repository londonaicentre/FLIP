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
"""Tag the docs GIFs a release ships: ``vX.Y.Z`` on ``aicentreflip/docs-gifs`` (FLIP#1547).

``release.yml`` runs this at the release commit before it pushes the git tag, so ReadTheDocs' build of the new
version finds the dataset tag (``fetch_docs_gifs.py``). Every recording on the dataset's ``main`` carries a
manifest naming the commit it was recorded from; the release gets the newest recording whose commit it contains.
An existing tag is never moved, so a re-run after a success does nothing.

Usage (needs ``HF_TOKEN`` with write access, run in a full clone of the release commit)::

    uv run docs/scripts/freeze_docs_gifs.py --release-commit "$(git rev-parse HEAD)"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.errors import EntryNotFoundError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = os.environ.get("FLIP_DOCS_GIFS_REPO", "aicentreflip/docs-gifs")
MANIFEST_NAME = "manifest.json"


def release_tag(pyproject: Path = REPO_ROOT / "pyproject.toml") -> str:
    """``v<version>``: the tag ``release.yml`` gives this release, from the same file."""
    return "v" + tomllib.loads(pyproject.read_text())["project"]["version"]


def contains(release_commit: str, commit: str) -> bool:
    """Whether ``commit`` is in the history of ``release_commit``."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, release_commit], cwd=REPO_ROOT, capture_output=True
    )
    return result.returncode == 0


def shipped_recording(api: HfApi, repo: str, release_commit: str) -> str | None:
    """The dataset commit of the newest recording on ``main`` that the release contains, if any."""
    for commit in api.list_repo_commits(repo, repo_type="dataset"):
        try:
            manifest = api.hf_hub_download(repo, MANIFEST_NAME, repo_type="dataset", revision=commit.commit_id)
        except EntryNotFoundError:
            continue  # the dataset's first commits predate the manifest
        if contains(release_commit, json.loads(Path(manifest).read_text())["source_commit"]):
            return commit.commit_id
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release-commit", required=True, help="the commit being released")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    args = parser.parse_args(argv)

    tag = release_tag()
    api = HfApi()
    if tag in {ref.name for ref in api.list_repo_refs(args.repo, repo_type="dataset").tags}:
        print(f"✅ {args.repo} already has {tag}")
        return 0
    commit = shipped_recording(api, args.repo, args.release_commit)
    if commit is None:
        raise SystemExit(f"❌ no recording on {args.repo} was made from a commit in {args.release_commit}")
    api.create_tag(args.repo, tag=tag, revision=commit, repo_type="dataset", tag_message=f"docs GIFs of FLIP {tag}")
    print(f"✅ tagged {commit} {tag}: https://huggingface.co/datasets/{args.repo}/tree/{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
