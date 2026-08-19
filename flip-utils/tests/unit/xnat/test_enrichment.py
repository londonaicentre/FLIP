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
#

"""Tests for manifest parsing and the enrichment upload."""

from pathlib import Path

import pytest

from flip.exceptions import XnatError
from flip.xnat.client import XnatClient
from flip.xnat.enrichment import EnrichmentItem, read_manifest, run_enrichment, upload_enrichment_files
from tests.unit.xnat.helpers import FakeResponse, FakeSession, project_routes, resolvable_project_routes


def _client(files: list[str], put_response: FakeResponse | None = None) -> XnatClient:
    """Build a stubbed client over a two-experiment project.

    Args:
        files (list[str]): Filenames each scan's NIFTI resource reports.
        put_response (FakeResponse | None): Response returned from every upload.

    Returns:
        XnatClient: The stubbed client.
    """
    client = XnatClient(server="http://xnat.example", user="u", password="p")
    client._session = FakeSession(project_routes(files), put_response=put_response)  # type: ignore[assignment]
    return client


def _resolvable_client(name: str, flip_project_id: str | None = "flip-uuid") -> XnatClient:
    """Build a stubbed client for one Trust's XNAT, able to resolve a FLIP project id.

    Args:
        name (str): Distinguishes the server URL, so per-server assertions are readable.
        flip_project_id (str | None): ``secondary_ID`` the server reports; ``None`` for a Trust
            that never pulled this project.

    Returns:
        XnatClient: The stubbed client.
    """
    client = XnatClient(server=f"http://xnat-{name}.example", user="u", password="p")
    routes = resolvable_project_routes(["input_spleen_2.nii.gz"], flip_project_id)
    client._session = FakeSession(routes)  # type: ignore[assignment]
    return client


def _label(tmp_path: Path, name: str = "label.nii.gz") -> Path:
    """Write a throwaway local label file.

    Args:
        tmp_path (Path): pytest temporary directory.
        name (str): Filename to create.

    Returns:
        Path: The created file.
    """
    path = tmp_path / name
    path.write_bytes(b"volume")
    return path


class TestReadManifest:
    """The manifest is the project-agnostic input to enrichment."""

    def test_parses_rows_and_resolves_paths_against_the_manifest(self, tmp_path):
        (tmp_path / "labels").mkdir()
        manifest = tmp_path / "manifest.csv"
        manifest.write_text("accession_id,file_path\nFAK001,labels/a.nii.gz\n")

        items = read_manifest(manifest)

        assert items == [EnrichmentItem("FAK001", tmp_path / "labels" / "a.nii.gz", None)]

    def test_absolute_paths_are_left_alone(self, tmp_path):
        manifest = tmp_path / "manifest.csv"
        manifest.write_text(f"accession_id,file_path\nFAK001,{tmp_path / 'a.nii.gz'}\n")

        assert read_manifest(manifest)[0].file_path == tmp_path / "a.nii.gz"

    def test_optional_target_filename_is_carried_through(self, tmp_path):
        manifest = tmp_path / "manifest.csv"
        manifest.write_text("accession_id,file_path,target_filename\nFAK001,a.nii.gz,seg_a.nii.gz\n")

        assert read_manifest(manifest)[0].target_filename == "seg_a.nii.gz"

    def test_blank_target_filename_is_treated_as_absent(self, tmp_path):
        manifest = tmp_path / "manifest.csv"
        manifest.write_text("accession_id,file_path,target_filename\nFAK001,a.nii.gz,\n")

        assert read_manifest(manifest)[0].target_filename is None

    def test_missing_column_is_named(self, tmp_path):
        manifest = tmp_path / "manifest.csv"
        manifest.write_text("accession_id\nFAK001\n")

        with pytest.raises(XnatError, match="file_path"):
            read_manifest(manifest)

    def test_missing_value_reports_the_line_number(self, tmp_path):
        manifest = tmp_path / "manifest.csv"
        manifest.write_text("accession_id,file_path\nFAK001,a.nii.gz\n,b.nii.gz\n")

        with pytest.raises(XnatError, match="line 3"):
            read_manifest(manifest)

    def test_empty_file_is_rejected(self, tmp_path):
        manifest = tmp_path / "manifest.csv"
        manifest.write_text("")

        with pytest.raises(XnatError, match="empty"):
            read_manifest(manifest)

    def test_unreadable_file_is_reported(self, tmp_path):
        with pytest.raises(XnatError, match="Could not read manifest"):
            read_manifest(tmp_path / "absent.csv")


class TestUploadEnrichmentFiles:
    """Uploading is keyed on accession and named after the image already in XNAT."""

    def test_target_name_is_derived_from_the_image(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])
        items = [EnrichmentItem("FAK001", _label(tmp_path))]

        summary = upload_enrichment_files(client, "PROJ", items)

        assert summary.uploaded == ["FAK001"]
        assert client._session.puts[0].endswith("/files/label_spleen_2.nii.gz?inbody=true")

    def test_explicit_target_filename_wins(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])
        items = [EnrichmentItem("FAK001", _label(tmp_path), target_filename="mask.nii.gz")]

        upload_enrichment_files(client, "PROJ", items)

        assert client._session.puts[0].endswith("/files/mask.nii.gz?inbody=true")

    def test_a_custom_rename_prefix_pair_is_honoured(self, tmp_path):
        client = _client(["img_case1.nii.gz"])
        items = [EnrichmentItem("FAK001", _label(tmp_path))]

        upload_enrichment_files(client, "PROJ", items, rename=("img_", "seg_"))

        assert client._session.puts[0].endswith("/files/seg_case1.nii.gz?inbody=true")

    def test_accession_with_no_scan_is_skipped(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])
        items = [EnrichmentItem("NOT_IN_XNAT", _label(tmp_path))]

        summary = upload_enrichment_files(client, "PROJ", items)

        assert summary.skipped_no_scan == ["NOT_IN_XNAT"]
        assert client._session.puts == []

    def test_scan_without_a_matching_image_is_skipped(self, tmp_path):
        """No input_* file means conversion has not run — uploading would guess at the name."""
        client = _client(["something_else.nii.gz"])
        items = [EnrichmentItem("FAK001", _label(tmp_path))]

        summary = upload_enrichment_files(client, "PROJ", items)

        assert summary.skipped_no_resource == ["FAK001"]
        assert client._session.puts == []

    def test_existing_target_is_skipped_not_overwritten(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz", "label_spleen_2.nii.gz"])
        items = [EnrichmentItem("FAK001", _label(tmp_path))]

        summary = upload_enrichment_files(client, "PROJ", items)

        assert summary.skipped_exists == ["FAK001"]
        assert client._session.puts == []

    def test_overwrite_uploads_over_an_existing_target(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz", "label_spleen_2.nii.gz"])
        items = [EnrichmentItem("FAK001", _label(tmp_path))]

        summary = upload_enrichment_files(client, "PROJ", items, overwrite=True)

        assert summary.uploaded == ["FAK001"]
        assert len(client._session.puts) == 1

    def test_dry_run_reports_without_uploading(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])
        items = [EnrichmentItem("FAK001", _label(tmp_path))]

        summary = upload_enrichment_files(client, "PROJ", items, dry_run=True)

        assert summary.uploaded == ["FAK001"]
        assert summary.dry_run is True
        assert client._session.puts == []

    def test_missing_local_file_is_a_failure_not_a_skip(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])
        items = [EnrichmentItem("FAK001", tmp_path / "absent.nii.gz")]

        summary = upload_enrichment_files(client, "PROJ", items)

        assert summary.failed[0][0] == "FAK001"
        assert "local file not found" in summary.failed[0][1]
        assert summary.ok is False

    def test_upload_error_is_recorded_and_other_items_continue(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"], put_response=FakeResponse(status_code=500, text="boom"))
        items = [EnrichmentItem("FAK001", _label(tmp_path)), EnrichmentItem("FAK002", _label(tmp_path, "b.nii.gz"))]

        summary = upload_enrichment_files(client, "PROJ", items)

        assert [accession for accession, _ in summary.failed] == ["FAK001", "FAK002"]
        assert summary.uploaded == []

    def test_multi_scan_accession_records_one_entry_per_scan(self, tmp_path):
        """Counts describe files written, so an accession spanning 2 scans counts twice."""
        client = XnatClient(server="http://xnat.example", user="u", password="p")
        routes = project_routes(["input_spleen_2.nii.gz"])
        # Give EXP_1 a second scan; the accession FAK001 now maps to two destination scans.
        routes["/data/experiments/EXP_1/scans"] = FakeResponse(
            payload={"ResultSet": {"Result": [{"ID": "1"}, {"ID": "2"}]}}
        )
        client._session = FakeSession(routes)  # type: ignore[assignment]

        summary = upload_enrichment_files(client, "PROJ", [EnrichmentItem("FAK001", _label(tmp_path))])

        assert summary.uploaded == ["FAK001", "FAK001"]
        assert len(client._session.puts) == 2
        assert "/scans/1/" in client._session.puts[0]
        assert "/scans/2/" in client._session.puts[1]

    def test_resource_listing_error_aborts_rather_than_reporting_a_skip(self, tmp_path):
        """An auth/server fault must not land in skipped_no_resource — that hides it."""
        client = XnatClient(server="http://xnat.example", user="u", password="p")
        routes = project_routes(["input_spleen_2.nii.gz"])
        routes["/resources/NIFTI/files"] = FakeResponse(status_code=401, text="unauthorised")
        client._session = FakeSession(routes)  # type: ignore[assignment]

        with pytest.raises(XnatError, match="401"):
            upload_enrichment_files(client, "PROJ", [EnrichmentItem("FAK001", _label(tmp_path))])

    def test_identical_rename_prefixes_are_refused(self, tmp_path):
        """Swapping a prefix for itself would target the image and destroy it."""
        client = _client(["input_spleen_2.nii.gz"])

        with pytest.raises(XnatError, match="must differ"):
            upload_enrichment_files(client, "PROJ", [], rename=("input_", "input_"))

    def test_each_accession_reaches_only_its_own_scan(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])
        items = [EnrichmentItem("FAK002", _label(tmp_path))]

        upload_enrichment_files(client, "PROJ", items)

        assert len(client._session.puts) == 1
        assert "/subjects/SUBJ_2/experiments/EXP_2/" in client._session.puts[0]


class TestEnrichmentSummary:
    """The summary is what the CLI prints and what callers gate on."""

    def test_ok_is_false_only_when_something_failed(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])

        summary = upload_enrichment_files(client, "PROJ", [EnrichmentItem("NOPE", _label(tmp_path))])

        assert summary.skipped_no_scan == ["NOPE"]
        assert summary.ok is True

    def test_render_marks_a_dry_run_differently(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])
        items = [EnrichmentItem("FAK001", _label(tmp_path))]

        assert "would upload: 1" in upload_enrichment_files(client, "PROJ", items, dry_run=True).render()
        assert "uploaded: 1" in upload_enrichment_files(client, "PROJ", items).render()

    def test_render_lists_failure_reasons(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])
        items = [EnrichmentItem("FAK001", tmp_path / "absent.nii.gz")]

        rendered = upload_enrichment_files(client, "PROJ", items).render()

        assert "FAK001" in rendered
        assert "local file not found" in rendered


class TestResolvedAny:
    """`ok` says nothing failed; `resolved_any` says something actually landed."""

    def test_false_when_no_accession_matched_a_scan(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])

        summary = upload_enrichment_files(client, "PROJ", [EnrichmentItem("NOPE", _label(tmp_path))])

        # The pairing that made this worth a property: nothing failed, yet nothing was achieved.
        assert summary.ok is True
        assert summary.resolved_any is False
        assert summary.requested == 1

    def test_false_when_every_scan_lacked_a_converted_image(self, tmp_path):
        client = _client(["something_else.nii.gz"])

        summary = upload_enrichment_files(client, "PROJ", [EnrichmentItem("FAK001", _label(tmp_path))])

        # Ran before DICOM-to-NIfTI conversion: the case the user guide leads its troubleshooting
        # with, and the one that used to exit 0.
        assert summary.skipped_no_resource == ["FAK001"]
        assert summary.resolved_any is False

    def test_true_when_the_file_was_already_present(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz", "label_spleen_2.nii.gz"])

        summary = upload_enrichment_files(client, "PROJ", [EnrichmentItem("FAK001", _label(tmp_path))])

        # An idempotent re-run has resolved its destination even though it wrote nothing.
        assert summary.skipped_exists == ["FAK001"]
        assert summary.resolved_any is True

    def test_true_on_a_dry_run_that_would_upload(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])

        summary = upload_enrichment_files(client, "PROJ", [EnrichmentItem("FAK001", _label(tmp_path))], dry_run=True)

        # A dry run has to predict the real run's exit code, or it is not a rehearsal.
        assert summary.resolved_any is True

    def test_records_the_project_scan_count_as_the_coverage_denominator(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])

        summary = upload_enrichment_files(client, "PROJ", [EnrichmentItem("FAK001", _label(tmp_path))])

        assert (summary.resolved, summary.scans_total) == (1, 2)
        assert "coverage: 1/2 scan(s)" in summary.render()

    def test_render_explains_a_run_that_resolved_nothing(self, tmp_path):
        client = _client(["input_spleen_2.nii.gz"])

        summary = upload_enrichment_files(client, "PROJ", [EnrichmentItem("NOPE", _label(tmp_path))])

        assert "Nothing was resolved" in summary.render()


class TestRunEnrichment:
    """Enrichment is per-Trust, so a complete run visits every Trust in the project."""

    def test_uploads_the_same_manifest_to_every_server(self, tmp_path):
        first, second = _resolvable_client("A"), _resolvable_client("B")
        items = [EnrichmentItem("FAK001", _label(tmp_path))]

        report = run_enrichment([first, second], items, flip_project_id="flip-uuid")

        # Self-selecting: each server takes the whole manifest and matches only its own studies.
        assert [len(client._session.puts) for client in (first, second)] == [1, 1]
        assert report.exit_code() == 0

    def test_a_trust_without_the_project_is_skipped_not_fatal(self, tmp_path):
        holder, other = _resolvable_client("A"), _resolvable_client("B", flip_project_id=None)

        items = [EnrichmentItem("FAK001", _label(tmp_path))]

        report = run_enrichment([holder, other], items, flip_project_id="flip-uuid")

        assert [outcome.fatal for outcome in report.outcomes] == [False, False]
        assert report.exit_code() == 0

    def test_the_only_trust_lacking_the_project_is_fatal(self, tmp_path):
        client = _resolvable_client("A", flip_project_id=None)

        report = run_enrichment([client], [EnrichmentItem("FAK001", _label(tmp_path))], flip_project_id="flip-uuid")

        # Alone, "not at this Trust" is the whole run failing, not a skip.
        assert report.outcomes[0].fatal is True
        assert report.exit_code() == 1

    def test_one_server_failing_does_not_abandon_the_others(self, tmp_path):
        healthy = _resolvable_client("A")
        broken = _resolvable_client("B")
        broken._session.routes = {"/data/projects": FakeResponse(status_code=401, text="denied")}

        items = [EnrichmentItem("FAK001", _label(tmp_path))]

        report = run_enrichment([healthy, broken], items, flip_project_id="flip-uuid")

        assert len(healthy._session.puts) == 1
        assert report.exit_code() == 1

    def test_requested_is_the_manifest_size_not_the_sum_across_servers(self, tmp_path):
        clients = [_resolvable_client("A"), _resolvable_client("B")]
        items = [EnrichmentItem("FAK001", _label(tmp_path)), EnrichmentItem("FAK002", _label(tmp_path, "b.nii.gz"))]

        report = run_enrichment(clients, items, flip_project_id="flip-uuid")

        # Summing would multiply the manifest by the roster and make coverage look unreachable.
        assert report.requested == 2

    def test_explicit_project_id_is_refused_for_a_roster(self, tmp_path):
        clients = [_resolvable_client("A"), _resolvable_client("B")]

        with pytest.raises(XnatError, match="differ per Trust"):
            run_enrichment(clients, [EnrichmentItem("FAK001", _label(tmp_path))], project_id="PROJ")

    def test_require_full_coverage_fails_a_partial_run(self, tmp_path):
        client = _resolvable_client("A")

        report = run_enrichment([client], [EnrichmentItem("FAK001", _label(tmp_path))], flip_project_id="flip-uuid")

        # One of the project's two scans enriched.
        assert report.exit_code() == 0
        assert report.exit_code(require_full_coverage=True) == 1
