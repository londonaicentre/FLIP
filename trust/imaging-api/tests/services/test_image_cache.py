# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

import json
import os
from unittest.mock import patch

import pytest

from imaging_api.services.image_cache import (
    SENTINEL_PREFIX,
    CachedDownload,
    clear_accession_dir,
    invalidate_accession,
    read_sentinel,
    remove_sentinel,
    sentinel_path,
    write_sentinel,
)

RECORD = CachedDownload(experiment_id="XNAT_E00004", resource_stats=[["173680", "NIFTI", "82", "1", "183823"]])


def _seed_sentinel(base, net_id, project, accession, assessor="scan", resource="NIFTI"):
    accession_dir = os.path.join(str(base), net_id, project, accession)
    os.makedirs(accession_dir, exist_ok=True)
    write_sentinel(accession_dir, assessor, resource, RECORD)
    return os.path.join(accession_dir, f"{SENTINEL_PREFIX}{assessor}-{resource}")


# ── sentinel_path ──


class TestSentinelPath:
    def test_builds_direct_child_of_accession_dir(self, tmp_path):
        path = sentinel_path(str(tmp_path), "scan", "NIFTI")
        assert path == os.path.join(str(tmp_path), f"{SENTINEL_PREFIX}scan-NIFTI")

    @pytest.mark.parametrize(
        ("assessor_type", "resource_type"),
        [
            ("scan/../..", "NIFTI"),  # separator smuggled via assessor
            ("scan", "../NIFTI"),  # separator smuggled via resource
            ("scan", "/etc/passwd"),  # absolute path in resource
            ("", "NIFTI"),  # empty assessor
            ("scan", ""),  # empty resource
        ],
    )
    def test_path_component_values_are_rejected(self, tmp_path, assessor_type, resource_type):
        with pytest.raises(ValueError, match="Path traversal detected in"):
            sentinel_path(str(tmp_path), assessor_type, resource_type)

    def test_resolved_path_guard_holds_if_component_validation_is_bypassed(self, tmp_path, monkeypatch):
        """The second (resolved-path) layer must refuse escapes even without the component check."""
        from imaging_api.services import image_cache

        monkeypatch.setattr(image_cache, "_validate_component", lambda name, value: None)
        with pytest.raises(ValueError, match="Path traversal detected in sentinel name"):
            sentinel_path(str(tmp_path), "../" * 20 + "etc", "passwd")


# ── write_sentinel / remove_sentinel ──


class TestWriteRemoveSentinel:
    def test_write_creates_dotfile_in_extracted_directory(self, tmp_path):
        accession_dir = str(tmp_path / "net1" / "proj" / "ACC1")
        os.makedirs(accession_dir)
        write_sentinel(accession_dir, "scan", "NIFTI", RECORD)
        assert os.path.isfile(os.path.join(accession_dir, f"{SENTINEL_PREFIX}scan-NIFTI"))

    def test_write_refuses_a_missing_accession_directory(self, tmp_path):
        """No extracted folder means the download produced nothing: never mark that complete."""
        accession_dir = str(tmp_path / "net1" / "proj" / "ACC1")
        with pytest.raises(FileNotFoundError, match="refusing to mark the download complete"):
            write_sentinel(accession_dir, "scan", "NIFTI", RECORD)
        assert not os.path.exists(accession_dir)

    @pytest.mark.parametrize("record", [RECORD, CachedDownload(experiment_id="XNAT_E00004", resource_stats=None)])
    def test_the_record_reads_back(self, tmp_path, record):
        write_sentinel(str(tmp_path), "scan", "NIFTI", record)
        assert read_sentinel(str(tmp_path), "scan", "NIFTI") == record

    def test_the_record_is_json_with_a_version(self, tmp_path):
        write_sentinel(str(tmp_path), "scan", "NIFTI", RECORD)
        assert json.loads((tmp_path / f"{SENTINEL_PREFIX}scan-NIFTI").read_text()) == {
            "version": 1,
            "experiment_id": "XNAT_E00004",
            "resource_stats": [["173680", "NIFTI", "82", "1", "183823"]],
        }

    @pytest.mark.parametrize(
        "content",
        [
            "",  # an empty sentinel, as imaging-api wrote before the record
            "{trunc",
            "[]",
            '{"experiment_id": "XNAT_E1", "resource_stats": null}',  # no version
            '{"version": 2, "experiment_id": "XNAT_E1", "resource_stats": null}',
            '{"version": 1, "resource_stats": null}',
            '{"version": 1, "experiment_id": "", "resource_stats": null}',
            '{"version": 1, "experiment_id": "..", "resource_stats": null}',
            '{"version": 1, "experiment_id": "a/b", "resource_stats": null}',
            '{"version": 1, "experiment_id": 7, "resource_stats": null}',
            '{"version": 1, "experiment_id": "XNAT_E1", "resource_stats": "x"}',
            '{"version": 1, "experiment_id": "XNAT_E1", "resource_stats": [["a", 1]]}',
            '{"version": 1, "experiment_id": "XNAT_E1", "resource_stats": ["a"]}',
        ],
    )
    def test_anything_but_a_record_reads_as_none(self, tmp_path, content):
        (tmp_path / f"{SENTINEL_PREFIX}scan-NIFTI").write_text(content)
        assert read_sentinel(str(tmp_path), "scan", "NIFTI") is None

    def test_a_missing_sentinel_reads_as_none(self, tmp_path):
        assert read_sentinel(str(tmp_path), "scan", "NIFTI") is None

    def test_remove_is_idempotent(self, tmp_path):
        accession_dir = str(tmp_path / "net1" / "proj" / "ACC1")
        os.makedirs(accession_dir)
        write_sentinel(accession_dir, "scan", "NIFTI", RECORD)
        remove_sentinel(accession_dir, "scan", "NIFTI")
        remove_sentinel(accession_dir, "scan", "NIFTI")  # second removal must not raise
        assert not os.path.exists(os.path.join(accession_dir, f"{SENTINEL_PREFIX}scan-NIFTI"))


# ── invalidate_accession ──


class TestInvalidateAccession:
    def test_removes_sentinels_across_all_nets(self, tmp_path):
        s1 = _seed_sentinel(tmp_path, "net-1", "proj-A", "ACC1")
        s2 = _seed_sentinel(tmp_path, "net-2", "proj-A", "ACC1", resource="DICOM")
        survivor_other_accession = _seed_sentinel(tmp_path, "net-1", "proj-A", "ACC2")
        survivor_other_project = _seed_sentinel(tmp_path, "net-1", "proj-B", "ACC1")

        removed = invalidate_accession(str(tmp_path), "proj-A", "ACC1")

        assert removed == 2
        assert not os.path.exists(s1)
        assert not os.path.exists(s2)
        assert os.path.exists(survivor_other_accession)
        assert os.path.exists(survivor_other_project)

    def test_leaves_image_content_untouched(self, tmp_path):
        sentinel = _seed_sentinel(tmp_path, "net-1", "proj-A", "ACC1")
        payload = os.path.join(os.path.dirname(sentinel), "scan.nii")
        with open(payload, "w") as f:
            f.write("nifti-data")

        invalidate_accession(str(tmp_path), "proj-A", "ACC1")

        assert os.path.exists(payload)

    def test_no_matches_is_a_noop(self, tmp_path):
        assert invalidate_accession(str(tmp_path), "proj-A", "ACC1") == 0

    def test_missing_base_dir_is_a_noop(self, tmp_path):
        assert invalidate_accession(str(tmp_path / "never-created"), "proj-A", "ACC1") == 0

    def test_glob_metacharacters_are_literal(self, tmp_path):
        """An id containing glob metacharacters must not wildcard onto other accessions."""
        survivor = _seed_sentinel(tmp_path, "net-1", "proj-A", "ACC1")

        removed = invalidate_accession(str(tmp_path), "proj-A", "ACC*")

        assert removed == 0
        assert os.path.exists(survivor)

    @pytest.mark.parametrize(
        ("project", "accession"),
        [
            ("../escape", "ACC1"),  # separator in project
            ("proj-A", "ACC1/.."),  # separator in accession
            ("..", "ACC1"),  # dot-dir project could glob a level up
            ("proj-A", "."),  # dot-dir accession
            ("", "ACC1"),  # empty project
            ("proj-A", ""),  # empty accession
        ],
    )
    def test_path_component_values_are_rejected(self, tmp_path, project, accession):
        with pytest.raises(ValueError, match="Path traversal detected in"):
            invalidate_accession(str(tmp_path), project, accession)


# ── clear_accession_dir ──


class TestClearAccessionDir:
    def test_deletes_images_and_every_sentinel_but_nothing_beside_it(self, tmp_path):
        nifti = _seed_sentinel(tmp_path, "net-1", "proj-A", "ACC1")
        dicom = _seed_sentinel(tmp_path, "net-1", "proj-A", "ACC1", resource="DICOM")
        accession_dir = os.path.dirname(nifti)
        image = os.path.join(accession_dir, "scans", "1-x", "resources", "NIFTI", "files", "input.nii.gz")
        os.makedirs(os.path.dirname(image))
        open(image, "w").close()
        sibling = _seed_sentinel(tmp_path, "net-1", "proj-A", "ACC2")
        other_net = _seed_sentinel(tmp_path, "net-2", "proj-A", "ACC1")

        clear_accession_dir(accession_dir)

        assert not os.path.exists(accession_dir)
        assert not os.path.exists(nifti)
        assert not os.path.exists(dicom)
        assert os.path.exists(sibling)
        assert os.path.exists(other_net)
        assert os.path.isdir(os.path.dirname(accession_dir))  # the project dir stays

    def test_a_missing_directory_is_a_noop(self, tmp_path):
        clear_accession_dir(str(tmp_path / "net-1" / "proj-A" / "ACC1"))

    def test_a_file_vanishing_mid_way_is_not_an_error(self, tmp_path):
        """A concurrent NVFLARE CleanupImages can delete files under the walk."""
        sentinel = _seed_sentinel(tmp_path, "net-1", "proj-A", "ACC1")
        real_unlink = os.unlink

        def _unlink_already_gone(path, *args, **kwargs):
            real_unlink(path, *args, **kwargs)
            raise FileNotFoundError(path)

        with patch("shutil.os.unlink", side_effect=_unlink_already_gone):
            clear_accession_dir(os.path.dirname(sentinel))

        assert not os.path.exists(os.path.dirname(sentinel))

    def test_other_failures_propagate(self, tmp_path):
        sentinel = _seed_sentinel(tmp_path, "net-1", "proj-A", "ACC1")

        with patch("shutil.os.unlink", side_effect=PermissionError("owned by the FL client")):
            with pytest.raises(PermissionError):
                clear_accession_dir(os.path.dirname(sentinel))
