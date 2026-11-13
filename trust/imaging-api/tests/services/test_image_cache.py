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
import shutil
from pathlib import Path

import pytest

from imaging_api.services import image_cache
from imaging_api.services.image_cache import (
    SENTINEL_PREFIX,
    CachedDownload,
    clear_resource,
    invalidate_accession,
    read_sentinel,
    remove_accessions_except,
    sentinel_path,
    write_sentinel,
)

RECORD = CachedDownload("XNAT_E00004", [["173680", "NIFTI", "82", "1", "183823"]])


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


# ── write_sentinel ──


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

    @pytest.mark.parametrize("record", [RECORD, CachedDownload("XNAT_E00004", None)])
    def test_the_record_reads_back(self, tmp_path, record):
        write_sentinel(str(tmp_path), "scan", "NIFTI", record)
        assert read_sentinel(str(tmp_path), "scan", "NIFTI") == record

    @pytest.mark.parametrize(
        "content",
        [
            "",  # what imaging-api wrote before the record
            '{"version": 2, "experiment_id": "XNAT_E1"}',
            '{"version": 1, "experiment_id": ".."}',  # the ID goes into an XNAT URL path
        ],
    )
    def test_anything_but_a_record_reads_as_none(self, tmp_path, content):
        (tmp_path / f"{SENTINEL_PREFIX}scan-NIFTI").write_text(content)
        assert read_sentinel(str(tmp_path), "scan", "NIFTI") is None


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


# ── clear_resource ──


def _seed_resource(accession_dir, resource, assessor="scan"):
    image = Path(accession_dir, f"{assessor}s", "1-x", "resources", resource, "files", "image")
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_text("x")
    write_sentinel(str(accession_dir), assessor, resource, RECORD)
    return image


class TestClearResource:
    def test_one_resource_takes_only_its_files_and_the_sentinels_that_cover_them(self, tmp_path):
        nifti, dicom = _seed_resource(tmp_path, "NIFTI"), _seed_resource(tmp_path, "DICOM")
        _seed_resource(tmp_path, "ALL")

        clear_resource(str(tmp_path), "scan", "NIFTI")

        assert not nifti.parent.parent.exists()
        assert [read_sentinel(str(tmp_path), "scan", r) is None for r in ("NIFTI", "ALL", "DICOM")] == [
            True,
            True,
            False,
        ]
        assert dicom.is_file()

    def test_all_takes_every_scan_resource_but_not_assessors(self, tmp_path):
        _seed_resource(tmp_path, "NIFTI")
        _seed_resource(tmp_path, "ALL")
        seg = _seed_resource(tmp_path, "SEG", assessor="assessor")

        clear_resource(str(tmp_path), "scan", "ALL")

        assert not (tmp_path / "scans").exists()
        assert [read_sentinel(str(tmp_path), "scan", r) for r in ("NIFTI", "ALL")] == [None, None]
        assert seg.is_file()
        assert read_sentinel(str(tmp_path), "assessor", "SEG") == RECORD

    def test_nothing_cached_is_a_noop(self, tmp_path):
        clear_resource(str(tmp_path), "scan", "ALL")
        clear_resource(str(tmp_path), "scan", "NIFTI")

    def test_a_resource_folder_resolving_outside_the_accession_is_left_alone(self, tmp_path):
        accession, outside = tmp_path / "ACC", tmp_path / "outside"
        (outside / "files").mkdir(parents=True)
        (outside / "files" / "keep").write_text("x")
        link = accession / "scans" / "1-x" / "resources" / "NIFTI"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside, target_is_directory=True)

        clear_resource(str(accession), "scan", "NIFTI")

        assert (outside / "files" / "keep").is_file()

    def test_a_file_that_cannot_be_removed_raises_after_its_sentinel_is_gone(self, tmp_path):
        """Sentinels go first, so a partial delete never leaves one over missing files."""
        files = _seed_resource(tmp_path, "NIFTI").parent
        files.chmod(0o500)
        try:
            with pytest.raises(PermissionError):
                clear_resource(str(tmp_path), "scan", "NIFTI")
        finally:
            files.chmod(0o700)
        assert read_sentinel(str(tmp_path), "scan", "NIFTI") is None

    @pytest.mark.parametrize(("assessor", "resource"), [("../x", "ALL"), ("scan", "a/b")])
    def test_path_component_values_are_rejected(self, tmp_path, assessor, resource):
        with pytest.raises(ValueError, match="Path traversal detected in"):
            clear_resource(str(tmp_path), assessor, resource)


class TestRemoveAccessionsExcept:
    """A project's cached studies follow its approved cohort (FLIP#1860)."""

    def test_removes_the_projects_other_accessions_in_every_net(self, tmp_path):
        for net, accession in (("net-1", "A1"), ("net-1", "A2"), ("net-2", "A2"), ("net-2", "A3")):
            _seed_sentinel(tmp_path, net, "proj", accession)

        assert remove_accessions_except(str(tmp_path), "proj", {"A1"}) == (["A2", "A3"], [])

        assert (tmp_path / "net-1" / "proj" / "A1").is_dir()
        assert not (tmp_path / "net-1" / "proj" / "A2").exists()
        assert list((tmp_path / "net-2" / "proj").iterdir()) == []

    def test_leaves_other_projects_and_files_alone(self, tmp_path):
        _seed_sentinel(tmp_path, "net-1", "other", "A2")
        _seed_sentinel(tmp_path, "net-1", "proj", "A1")
        archive = tmp_path / "net-1" / "proj" / "A2-scans-NIFTI.zip"  # a download in progress
        archive.write_bytes(b"")

        assert remove_accessions_except(str(tmp_path), "proj", {"A1"}) == ([], [])

        assert (tmp_path / "net-1" / "other" / "A2").is_dir()
        assert archive.exists()

    def test_never_follows_a_symlink_an_fl_client_left(self, tmp_path):
        """The fl-client can write in its net directory; a link must not point the removal elsewhere."""
        victim = Path(_seed_sentinel(tmp_path, "net-1", "other", "A9")).parent
        (tmp_path / "net-1" / "proj").mkdir()
        (tmp_path / "net-1" / "proj" / "link").symlink_to(victim, target_is_directory=True)
        (tmp_path / "net-2").mkdir()
        (tmp_path / "net-2" / "proj").symlink_to(victim.parent, target_is_directory=True)

        assert remove_accessions_except(str(tmp_path), "proj", set()) == ([], [])

        assert victim.is_dir()

    def test_a_study_swapped_for_a_link_during_the_walk_is_not_followed(self, tmp_path, monkeypatch):
        """The window between listing a folder and removing a study in it."""
        victim = Path(_seed_sentinel(tmp_path, "net-1", "other", "A9")).parent
        study = Path(_seed_sentinel(tmp_path, "net-1", "proj", "A2")).parent
        listed = image_cache._subdir_names

        def swap_after_listing(dir_fd):
            names = listed(dir_fd)
            if names == ["A2"]:
                shutil.rmtree(study)
                study.symlink_to(victim, target_is_directory=True)
            return names

        monkeypatch.setattr(image_cache, "_subdir_names", swap_after_listing)

        assert remove_accessions_except(str(tmp_path), "proj", set()) == ([], ["net-1/proj/A2"])

        assert list(victim.iterdir())

    def test_a_study_that_cannot_be_removed_does_not_keep_the_others(self, tmp_path, monkeypatch):
        """Else one folder a client made unremovable would keep every study that left the cohort on disk."""
        for accession in ("A1", "A2", "A3"):
            _seed_sentinel(tmp_path, "net-1", "proj", accession)
        rmtree = shutil.rmtree

        def refuse_a2(path, **kwargs):
            if path == "A2":
                raise PermissionError(path)
            rmtree(path, **kwargs)

        monkeypatch.setattr(image_cache.shutil, "rmtree", refuse_a2)

        removed, failed = remove_accessions_except(str(tmp_path), "proj", set())

        assert (removed, failed) == (["A1", "A3"], ["net-1/proj/A2"])
        assert [p.name for p in (tmp_path / "net-1" / "proj").iterdir()] == ["A2"]

    def test_a_project_with_nothing_cached_removes_nothing(self, tmp_path):
        assert remove_accessions_except(str(tmp_path), "proj", set()) == ([], [])
        assert remove_accessions_except(str(tmp_path / "missing"), "proj", set()) == ([], [])

    @pytest.mark.parametrize("project", ["", "..", "a/b"])
    def test_rejects_a_project_id_that_is_a_path(self, tmp_path, project):
        with pytest.raises(ValueError, match="Path traversal"):
            remove_accessions_except(str(tmp_path), project, set())
