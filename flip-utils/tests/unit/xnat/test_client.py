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

"""Tests for the flip.xnat REST client."""

import json

import pytest
import requests

from flip.exceptions import XnatError
from flip.xnat.client import XnatClient, XnatScan
from tests.unit.xnat.helpers import FakeResponse, FakeSession, make_client


class TestFromEnv:
    """Building a client from the Container Service environment variables."""

    def test_builds_client_when_all_variables_present(self, monkeypatch):
        monkeypatch.setenv("XNAT_HOST", "http://xnat.example/")
        monkeypatch.setenv("XNAT_USER", "someone")
        monkeypatch.setenv("XNAT_PASS", "secret")

        client = XnatClient.from_env()

        assert client.server == "http://xnat.example"  # trailing slash trimmed

    def test_names_every_missing_variable(self, monkeypatch):
        monkeypatch.delenv("XNAT_HOST", raising=False)
        monkeypatch.setenv("XNAT_USER", "someone")
        monkeypatch.delenv("XNAT_PASS", raising=False)

        with pytest.raises(XnatError) as err:
            XnatClient.from_env()

        assert "XNAT_HOST" in str(err.value)
        assert "XNAT_PASS" in str(err.value)
        assert "XNAT_USER" not in str(err.value)

    def test_empty_value_counts_as_missing(self, monkeypatch):
        monkeypatch.setenv("XNAT_HOST", "http://xnat.example")
        monkeypatch.setenv("XNAT_USER", "")
        monkeypatch.setenv("XNAT_PASS", "secret")

        with pytest.raises(XnatError, match="XNAT_USER"):
            XnatClient.from_env()


class TestFromConfigFile:
    """Building a client from a .xnat.cfg-style JSON file."""

    def test_reads_server_user_password(self, tmp_path):
        config = tmp_path / ".xnat.cfg"
        config.write_text(json.dumps({"server": "http://xnat.example", "user": "u", "password": "p"}))

        assert XnatClient.from_config_file(config).server == "http://xnat.example"

    def test_missing_file_is_reported_by_path(self, tmp_path):
        with pytest.raises(XnatError, match="Could not read XNAT credentials file"):
            XnatClient.from_config_file(tmp_path / "absent.cfg")

    def test_invalid_json_is_reported(self, tmp_path):
        config = tmp_path / ".xnat.cfg"
        config.write_text("{not json")

        with pytest.raises(XnatError, match="not valid JSON"):
            XnatClient.from_config_file(config)

    def test_missing_key_is_named(self, tmp_path):
        config = tmp_path / ".xnat.cfg"
        config.write_text(json.dumps({"server": "http://xnat.example", "user": "u"}))

        with pytest.raises(XnatError, match="password"):
            XnatClient.from_config_file(config)


class TestResolveProjectByFlipProjectId:
    """FLIP's project id lives in the XNAT project's secondary_ID, not its primary ID."""

    def test_matches_on_secondary_id_not_the_primary_id(self):
        client = make_client(
            {
                "/data/projects": FakeResponse(
                    payload={
                        "ResultSet": {
                            "Result": [
                                {"ID": "XNAT_01", "secondary_ID": "other-uuid"},
                                {"ID": "XNAT_07", "secondary_ID": "wanted-uuid"},
                            ]
                        }
                    }
                )
            }
        )

        assert client.resolve_project_by_flip_project_id("wanted-uuid") == "XNAT_07"

    def test_no_match_explains_the_likely_cause(self):
        client = make_client(
            {"/data/projects": FakeResponse(payload={"ResultSet": {"Result": [{"ID": "X", "secondary_ID": "a"}]}})}
        )

        with pytest.raises(XnatError) as err:
            client.resolve_project_by_flip_project_id("missing-uuid")

        assert "missing-uuid" in str(err.value)
        assert "image pull" in str(err.value)

    def test_transport_failure_is_wrapped(self):
        client = make_client({})
        client._session.get_error = requests.ConnectionError("refused")

        with pytest.raises(XnatError, match="failed"):
            client.resolve_project_by_flip_project_id("any")

    def test_non_200_is_wrapped_with_status(self):
        client = make_client({"/data/projects": FakeResponse(status_code=401, text="unauthorised")})

        with pytest.raises(XnatError, match="401"):
            client.resolve_project_by_flip_project_id("any")


class TestListScans:
    """Scans are flattened across experiments and keyed by the accession (experiment label)."""

    def test_flattens_experiments_and_scans(self, project_session):
        client = XnatClient(server="http://xnat.example", user="u", password="p")
        client._session = project_session

        scans = client.list_scans("PROJ")

        assert scans == [
            XnatScan(accession_id="FAK001", subject_id="SUBJ_1", experiment_id="EXP_1", scan_id="1"),
            XnatScan(accession_id="FAK002", subject_id="SUBJ_2", experiment_id="EXP_2", scan_id="1"),
        ]

    def test_empty_project_yields_no_scans(self):
        client = make_client({"/data/projects/PROJ/experiments": FakeResponse(payload={"ResultSet": {"Result": []}})})

        assert client.list_scans("PROJ") == []


class TestListResourceFiles:
    """A missing resource is an expected state, not an error."""

    def test_returns_filenames(self):
        client = make_client(
            {
                "/resources/NIFTI/files": FakeResponse(
                    payload={"ResultSet": {"Result": [{"Name": "input_a.nii.gz"}, {"Name": "label_a.nii.gz"}]}}
                )
            }
        )
        scan = XnatScan("FAK001", "SUBJ_1", "EXP_1", "1")

        assert client.list_resource_files(scan, "NIFTI") == ["input_a.nii.gz", "label_a.nii.gz"]

    def test_absent_resource_returns_empty_list(self):
        client = make_client({})  # every request 404s
        scan = XnatScan("FAK001", "SUBJ_1", "EXP_1", "1")

        assert client.list_resource_files(scan, "NIFTI") == []

    def test_auth_failure_is_not_reported_as_an_empty_resource(self):
        """A 401 must not masquerade as 'no image in resource' — that reads as a benign skip."""
        client = make_client({"/resources/NIFTI/files": FakeResponse(status_code=401, text="unauthorised")})
        scan = XnatScan("FAK001", "SUBJ_1", "EXP_1", "1")

        with pytest.raises(XnatError, match="401"):
            client.list_resource_files(scan, "NIFTI")

    def test_server_error_is_not_reported_as_an_empty_resource(self):
        client = make_client({"/resources/NIFTI/files": FakeResponse(status_code=500, text="boom")})
        scan = XnatScan("FAK001", "SUBJ_1", "EXP_1", "1")

        with pytest.raises(XnatError, match="500"):
            client.list_resource_files(scan, "NIFTI")

    def test_transport_failure_is_not_reported_as_an_empty_resource(self):
        client = make_client({})
        client._session.get_error = requests.ConnectionError("refused")
        scan = XnatScan("FAK001", "SUBJ_1", "EXP_1", "1")

        with pytest.raises(XnatError, match="failed"):
            client.list_resource_files(scan, "NIFTI")


def _files(names: list[str]) -> dict:
    """Build a resource-file listing payload.

    Args:
        names (list[str]): Filenames the resource reports.

    Returns:
        dict: An XNAT ``ResultSet`` payload.
    """
    return {"ResultSet": {"Result": [{"Name": name} for name in names]}}


class TestUploadScanResourceFile:
    """Uploads target the project-scoped scan resource path used by imaging-api."""

    def test_puts_to_the_scan_resource_path(self, tmp_path):
        client = make_client({})
        client._session = FakeSession({}, put_response=FakeResponse(status_code=200))
        local = tmp_path / "label_a.nii.gz"
        local.write_bytes(b"volume")

        client.upload_scan_resource_file(
            scan=XnatScan("FAK001", "SUBJ_1", "EXP_1", "1"),
            project_id="PROJ",
            resource="NIFTI",
            local_path=local,
            target_filename="label_a.nii.gz",
        )

        assert client._session.puts == [
            "http://xnat.example/data/projects/PROJ/subjects/SUBJ_1/"
            "experiments/EXP_1/scans/1/resources/NIFTI/files/label_a.nii.gz?inbody=true"
        ]

    def test_conflict_without_overwrite_raises(self, tmp_path):
        client = make_client({})
        client._session = FakeSession({}, put_response=FakeResponse(status_code=409, text="exists"))
        local = tmp_path / "label_a.nii.gz"
        local.write_bytes(b"volume")

        with pytest.raises(XnatError, match="already exists"):
            client.upload_scan_resource_file(
                scan=XnatScan("FAK001", "SUBJ_1", "EXP_1", "1"),
                project_id="PROJ",
                resource="NIFTI",
                local_path=local,
                target_filename="label_a.nii.gz",
            )

    def test_error_status_raises_with_body(self, tmp_path):
        client = make_client({})
        client._session = FakeSession({}, put_response=FakeResponse(status_code=500, text="boom"))
        local = tmp_path / "label_a.nii.gz"
        local.write_bytes(b"volume")

        with pytest.raises(XnatError, match="boom"):
            client.upload_scan_resource_file(
                scan=XnatScan("FAK001", "SUBJ_1", "EXP_1", "1"),
                project_id="PROJ",
                resource="NIFTI",
                local_path=local,
                target_filename="label_a.nii.gz",
            )

    def test_existing_file_without_overwrite_raises_before_any_put(self, tmp_path):
        # XNAT's PUT replaces unconditionally — there is no overwrite query parameter — so the
        # only way overwrite=False can mean anything is to ask first, as imaging-api does.
        client = make_client({"/resources/NIFTI/files": FakeResponse(payload=_files(["label_a.nii.gz"]))})
        local = tmp_path / "label_a.nii.gz"
        local.write_bytes(b"volume")

        with pytest.raises(XnatError, match="already exists"):
            client.upload_scan_resource_file(
                scan=XnatScan("FAK001", "SUBJ_1", "EXP_1", "1"),
                project_id="PROJ",
                resource="NIFTI",
                local_path=local,
                target_filename="label_a.nii.gz",
            )

        assert client._session.puts == []

    def test_overwrite_replaces_an_existing_file(self, tmp_path):
        client = make_client({"/resources/NIFTI/files": FakeResponse(payload=_files(["label_a.nii.gz"]))})
        local = tmp_path / "label_a.nii.gz"
        local.write_bytes(b"volume")

        client.upload_scan_resource_file(
            scan=XnatScan("FAK001", "SUBJ_1", "EXP_1", "1"),
            project_id="PROJ",
            resource="NIFTI",
            local_path=local,
            target_filename="label_a.nii.gz",
            overwrite=True,
        )

        # The flag has to reach the wire, not just pick an error message.
        assert len(client._session.puts) == 1

    def test_path_characters_in_the_filename_are_escaped(self, tmp_path):
        client = make_client({})
        client._session = FakeSession({}, put_response=FakeResponse(status_code=200))
        local = tmp_path / "label_a.nii.gz"
        local.write_bytes(b"volume")

        client.upload_scan_resource_file(
            scan=XnatScan("FAK001", "SUBJ_1", "EXP_1", "1"),
            project_id="PROJ",
            resource="NIFTI",
            local_path=local,
            target_filename="../evil?x=1#f.nii.gz",
            overwrite=True,
        )

        url = client._session.puts[0]
        # requests splits on "?" and "#" before it quotes anything, so leaving them raw would both
        # redirect the write and silently drop inbody=true.
        assert url.endswith("/files/..%2Fevil%3Fx%3D1%23f.nii.gz?inbody=true")
        assert url.count("?") == 1

    def test_unreadable_local_file_raises_before_the_upload_is_claimed(self, tmp_path):
        client = make_client({})
        client._session = FakeSession({}, put_response=FakeResponse(status_code=200))
        missing = tmp_path / "gone.nii.gz"

        with pytest.raises(XnatError, match="Could not read"):
            client.upload_scan_resource_file(
                scan=XnatScan("FAK001", "SUBJ_1", "EXP_1", "1"),
                project_id="PROJ",
                resource="NIFTI",
                local_path=missing,
                target_filename="label_a.nii.gz",
                overwrite=True,
            )

        assert client._session.puts == []

    def test_transport_failure_during_upload_surfaces_as_xnat_error(self, tmp_path):
        client = make_client({})
        client._session = FakeSession({})
        client._session.put_error = requests.ConnectionError("refused")
        local = tmp_path / "label_a.nii.gz"
        local.write_bytes(b"volume")

        # A dropped connection must not be mistaken for a completed upload — the enrichment layer
        # records this accession as failed, which is what makes the run exit non-zero.
        with pytest.raises(XnatError, match="failed"):
            client.upload_scan_resource_file(
                scan=XnatScan("FAK001", "SUBJ_1", "EXP_1", "1"),
                project_id="PROJ",
                resource="NIFTI",
                local_path=local,
                target_filename="label_a.nii.gz",
                overwrite=True,
            )
