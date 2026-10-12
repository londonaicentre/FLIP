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

"""The MSD download: what is kept from the archive, and the checksum gate. No network."""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
from pathlib import Path

import pytest

DATASETS_DIR = Path(__file__).resolve().parents[3] / "datasets"
sys.path.insert(0, str(DATASETS_DIR))

from brain_mri import download_msd_brain_tumour as dl  # noqa: E402

MEMBERS = {
    "Task01_BrainTumour/dataset.json": b'{"modality": {"0": "FLAIR"}}',
    "Task01_BrainTumour/imagesTr/BRATS_001.nii.gz": b"train image",
    "Task01_BrainTumour/imagesTr/._BRATS_001.nii.gz": b"apple double",
    "Task01_BrainTumour/labelsTr/BRATS_001.nii.gz": b"train label",
    "Task01_BrainTumour/imagesTs/BRATS_485.nii.gz": b"test image, no label",
    "Task01_BrainTumour/._dataset.json": b"apple double",
}


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    path = tmp_path / "Task01_BrainTumour.tar"
    with tarfile.open(path, "w") as tar:
        for name, payload in MEMBERS.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return path


class TestWanted:
    @pytest.mark.parametrize(
        "member",
        [
            "Task01_BrainTumour/dataset.json",
            "Task01_BrainTumour/imagesTr/BRATS_001.nii.gz",
            "Task01_BrainTumour/labelsTr/BRATS_001.nii.gz",
        ],
    )
    def test_training_images_labels_and_the_manifest_are_kept(self, member):
        assert dl.wanted(member)

    @pytest.mark.parametrize(
        "member",
        [
            "Task01_BrainTumour/imagesTs/BRATS_485.nii.gz",
            "Task01_BrainTumour/imagesTr/._BRATS_001.nii.gz",
            "Task01_BrainTumour/._dataset.json",
        ],
    )
    def test_unlabelled_test_images_and_resource_forks_are_dropped(self, member):
        assert not dl.wanted(member)


class TestExtract:
    def test_extracts_only_the_wanted_members(self, archive, tmp_path):
        out = tmp_path / "data"
        dataset_dir = dl.extract(archive, out)

        assert dataset_dir == out / "Task01_BrainTumour"
        assert (dataset_dir / "dataset.json").read_bytes() == MEMBERS["Task01_BrainTumour/dataset.json"]
        assert (dataset_dir / "imagesTr" / "BRATS_001.nii.gz").is_file()
        assert (dataset_dir / "labelsTr" / "BRATS_001.nii.gz").is_file()
        assert not (dataset_dir / "imagesTs").exists()
        assert not (dataset_dir / "imagesTr" / "._BRATS_001.nii.gz").exists()

    def test_extract_is_idempotent(self, archive, tmp_path):
        out = tmp_path / "data"
        dl.extract(archive, out)
        marker = out / "Task01_BrainTumour" / "imagesTr" / "BRATS_001.nii.gz"
        marker.write_bytes(b"left alone")

        dl.extract(archive, out)

        assert marker.read_bytes() == b"left alone", "an already-extracted tree is not re-extracted"


class TestChecksum:
    def test_matching_md5_passes_and_a_mismatch_is_refused(self, archive):
        expected = hashlib.md5(archive.read_bytes()).hexdigest()  # noqa: S324 - MSD publishes md5
        dl.verify_md5(archive, expected)
        with pytest.raises(SystemExit, match="md5"):
            dl.verify_md5(archive, "0" * 32)

    def test_a_present_verified_archive_is_not_downloaded_again(self, archive, monkeypatch):
        expected = hashlib.md5(archive.read_bytes()).hexdigest()  # noqa: S324
        monkeypatch.setattr(dl.requests, "get", lambda *a, **k: pytest.fail("must not touch the network"))

        assert dl.download("https://example.invalid/x.tar", archive, expected) == archive

    def test_a_present_archive_with_the_wrong_checksum_is_replaced(self, archive, monkeypatch, tmp_path):
        payload = b"fresh bytes"

        class _Response:
            headers = {"content-length": str(len(payload))}

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(dl.requests, "get", lambda *a, **k: _Response())
        expected = hashlib.md5(payload).hexdigest()  # noqa: S324

        assert dl.download("https://example.invalid/x.tar", archive, expected) == archive
        assert archive.read_bytes() == payload
        assert not archive.with_suffix(".tar.part").exists()
