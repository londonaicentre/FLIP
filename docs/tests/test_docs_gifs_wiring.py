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

"""The docs GIFs wiring, checked statically (FLIP#1236).

A build-time gap between the manifest and the rst is deliberately non-fatal (a new GIF is authored in two
steps: spec + figure first, then the bot publishes and the pin PR merges). This guard catches the drift that
*is* a bug at PR time: a figure with no spec, a spec with no figure, the recorder and the fetcher disagreeing
on where the files live, a malformed pin, or a GIF creeping back into git.
"""

from __future__ import annotations

import re
import subprocess

from conftest import DOCS_DIR, REPO_ROOT

RST_FIGURE_RE = re.compile(r"^\.\. figure:: \.\./assets/generated/gifs/(?P<path>[a-z-]+/[a-z0-9-]+\.gif)\s*$", re.M)
OLD_FIGURE_RE = re.compile(r"assets/(admin|flip)/[a-z0-9-]+\.gif")
SPECS_DIR = REPO_ROOT / "flip-ui" / "test" / "cypress" / "docs"


def rst_figures() -> set[str]:
    return {m["path"] for rst in (DOCS_DIR / "source").rglob("*.rst") for m in RST_FIGURE_RE.finditer(rst.read_text())}


def test_every_figure_has_a_spec_and_every_spec_a_figure(publisher):
    assert rst_figures() == set(publisher.expected_gifs(SPECS_DIR))


def test_no_figure_points_at_the_old_tracked_paths():
    offenders = [str(rst) for rst in (DOCS_DIR / "source").rglob("*.rst") if OLD_FIGURE_RE.search(rst.read_text())]
    assert offenders == []


def test_no_gif_is_tracked_under_the_docs_assets():
    tracked = subprocess.run(
        ["git", "ls-files", "docs/source/assets/**/*.gif"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert tracked == []


def test_the_recorder_and_the_fetcher_share_one_destination(fetcher):
    script = (REPO_ROOT / "flip-ui" / "scripts" / "videos-to-gifs.sh").read_text()
    match = re.search(r'^assets_root="\$\{repo_root\}/(?P<rel>[^"]+)"', script, re.M)
    assert match is not None, "videos-to-gifs.sh must define assets_root relative to repo_root"
    assert REPO_ROOT / match["rel"] == fetcher.DEFAULT_DEST


def test_the_pin_is_one_well_formed_tag(fetcher, publisher):
    assert publisher.TAG_RE.fullmatch(fetcher.pinned_revision())


def test_the_card_documents_the_published_contract(publisher, tmp_path):
    """The card is published as the dataset README, so it must describe what the publisher actually writes."""
    (tmp_path / "flip").mkdir()
    (tmp_path / "flip" / "x.gif").write_bytes(b"GIF89a")
    manifest = publisher.build_manifest(
        tmp_path, ["flip/x.gif"], version="v", source_commit="0" * 40, recorded_at="t", workflow_run=None
    )
    card = publisher.DEFAULT_CARD.read_text()
    documented = [
        *manifest,
        *manifest["files"]["flip/x.gif"],
        publisher.MANIFEST_NAME,
        "admin/<name>.gif",
        "flip/<name>.gif",
        "YYYYMMDDTHHMMSSZ-<sha7>",
        "never moved",
        "never deleted",
    ]
    missing = [term for term in documented if term not in card]
    assert missing == [], f"the dataset card does not mention: {missing}"
