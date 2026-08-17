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

import os

import pytest

from imaging_api.services.image_cache import (
    SENTINEL_PREFIX,
    invalidate_accession,
    remove_sentinel,
    sentinel_path,
    write_sentinel,
)


def _seed_sentinel(base, net_id, project, accession, assessor="scan", resource="NIFTI"):
    accession_dir = os.path.join(str(base), net_id, project, accession)
    write_sentinel(accession_dir, assessor, resource)
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


# ── write_sentinel / remove_sentinel ──


class TestWriteRemoveSentinel:
    def test_write_creates_directory_and_dotfile(self, tmp_path):
        accession_dir = str(tmp_path / "net1" / "proj" / "ACC1")
        write_sentinel(accession_dir, "scan", "NIFTI")
        assert os.path.isfile(os.path.join(accession_dir, f"{SENTINEL_PREFIX}scan-NIFTI"))

    def test_remove_is_idempotent(self, tmp_path):
        accession_dir = str(tmp_path / "net1" / "proj" / "ACC1")
        write_sentinel(accession_dir, "scan", "NIFTI")
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
