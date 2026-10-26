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
"""Manifest building for the IDC pathology annotation uploader.

The annotations reach a trust by data enrichment rather than by the imaging pull (see
``test_idc_pathology_seeding`` for why), so this manifest is the whole input to that step. Its
failure mode matters: enriching a *subset* of the cohort leaves some slides unscoreable while the
run still looks clean, so a partial download must raise rather than quietly upload what it has.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UPLOADER = REPO_ROOT / "fl-tutorials" / "datasets" / "idc_pathology" / "upload_annotations_to_xnat.py"


def load_uploader():
    """Import the uploader module under test by path."""
    spec = importlib.util.spec_from_file_location("upload_annotations_to_xnat_under_test", UPLOADER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


uploader = load_uploader()

ROWS = [
    {"accession_id": "AAA-1", "site": "Trust_1"},
    {"accession_id": "AAA-2", "site": "Trust_1"},
    {"accession_id": "BBB-1", "site": "Trust_2"},
]


def write_manifest(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def make_annotation(data_dir: Path, site: str, accession: str) -> Path:
    path = uploader.annotation_path(data_dir, site, accession)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"annotation")
    return path


def test_manifest_covers_every_accession_and_names_the_target_explicitly(tmp_path) -> None:
    manifest = write_manifest(tmp_path / "manifest.csv", ROWS)
    for row in ROWS:
        make_annotation(tmp_path, row["site"], row["accession_id"])

    items = uploader.build_manifest(tmp_path, manifest)

    assert [i.accession_id for i in items] == ["AAA-1", "AAA-2", "BBB-1"]
    # Explicit, so the destination name never depends on what the pull happened to leave behind.
    assert {i.target_filename for i in items} == {"annotation.dcm"}


def test_a_partial_download_fails_rather_than_enriching_a_subset(tmp_path) -> None:
    manifest = write_manifest(tmp_path / "manifest.csv", ROWS)
    make_annotation(tmp_path, "Trust_1", "AAA-1")  # the other two were never downloaded

    with pytest.raises(FileNotFoundError) as err:
        uploader.build_manifest(tmp_path, manifest)

    message = str(err.value)
    assert "AAA-2" in message
    assert "BBB-1" in message
    assert "download-idc-pathology-data" in message, "the error must name the command that fixes it"


def test_trust_filter_accepts_a_bare_number_or_the_full_site_name(tmp_path) -> None:
    manifest = write_manifest(tmp_path / "manifest.csv", ROWS)
    for row in ROWS:
        make_annotation(tmp_path, row["site"], row["accession_id"])

    assert [i.accession_id for i in uploader.build_manifest(tmp_path, manifest, trust="2")] == ["BBB-1"]
    assert [i.accession_id for i in uploader.build_manifest(tmp_path, manifest, trust="Trust_2")] == ["BBB-1"]


def test_an_unknown_trust_fails_loudly_rather_than_uploading_nothing(tmp_path) -> None:
    manifest = write_manifest(tmp_path / "manifest.csv", ROWS)

    with pytest.raises(ValueError, match="no rows for trust"):
        uploader.build_manifest(tmp_path, manifest, trust="9")


def test_annotations_are_written_into_the_slide_scans_dicom_resource() -> None:
    """Not a resource of its own: the app finds both objects in the DICOM resource by SOP Class."""
    assert uploader.ANNOTATION_RESOURCE == "DICOM"


def test_the_default_manifest_is_the_fetched_published_one(tmp_path) -> None:
    """The manifest is published on trust-data and fetched into the data root, never committed."""
    assert uploader.DEFAULT_MANIFEST == uploader.DEFAULT_DATA_DIR / "manifest.csv"
    assert not (Path(uploader.__file__).parent / "manifest.csv").exists(), "a committed manifest would fork the source"
    with pytest.raises(FileNotFoundError, match="fetch-idc-pathology-manifest"):
        uploader.build_manifest(tmp_path, tmp_path / "manifest.csv")
