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

"""Tests for the ``flip-xnat`` CLI.

The exit code is the whole machine-readable contract — it is what ``e2e_smoke``'s data-enrichment
hook consumes — so it is pinned here rather than left to the entry point.
"""

import json
import runpy
from pathlib import Path

import pytest

from flip.xnat import cli
from flip.xnat.client import XnatClient
from tests.unit.xnat.helpers import FakeResponse, FakeSession, resolvable_project_routes


@pytest.fixture
def label(tmp_path: Path) -> Path:
    """Path: A throwaway local label file."""
    path = tmp_path / "label.nii.gz"
    path.write_bytes(b"volume")
    return path


@pytest.fixture
def manifest(tmp_path: Path, label: Path) -> Path:
    """Path: A one-row manifest naming an accession the stub project holds."""
    path = tmp_path / "manifest.csv"
    path.write_text(f"accession_id,file_path\nFAK001,{label}\n")
    return path


def _stub_servers(monkeypatch, *routes: dict) -> list[FakeSession]:
    """Make every credentials file resolve to a stubbed client, in order.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        *routes (dict): One route table per server the CLI will be given.

    Returns:
        list[FakeSession]: The sessions, so tests can assert on the PUTs each received.
    """
    sessions = [FakeSession(route_table) for route_table in routes]
    clients = []
    for index, session in enumerate(sessions):
        client = XnatClient(server=f"http://xnat-{index}.example", user="u", password="p")
        client._session = session  # type: ignore[assignment]
        clients.append(client)

    remaining = iter(clients)
    monkeypatch.setattr(XnatClient, "from_config_file", classmethod(lambda cls, path, **kw: next(remaining)))
    monkeypatch.setattr(XnatClient, "from_env", classmethod(lambda cls, **kw: next(remaining)))
    return sessions


def _credentials(tmp_path: Path, name: str) -> str:
    """Write a credentials file. Its contents are irrelevant — the client is stubbed.

    Args:
        tmp_path (Path): pytest temporary directory.
        name (str): Filename to create.

    Returns:
        str: Path to the file.
    """
    path = tmp_path / name
    path.write_text(json.dumps({"server": f"http://{name}", "user": "u", "password": "p"}))
    return str(path)


def _argv(manifest: Path, *extra: str) -> list[str]:
    """Build an argument vector for the upload subcommand.

    Args:
        manifest (Path): The manifest to pass.
        *extra (str): Additional flags.

    Returns:
        list[str]: The argument vector.
    """
    return ["upload", "--manifest", str(manifest), "--flip-project-id", "flip-uuid", *extra]


class TestArgumentHandling:
    """Bad input fails in-hand, before anything touches XNAT."""

    def test_malformed_rename_exits_non_zero(self, manifest, monkeypatch):
        _stub_servers(monkeypatch, resolvable_project_routes(["input_spleen_2.nii.gz"]))

        assert cli.main(_argv(manifest, "--rename", "nocolon")) == 1

    def test_unreadable_manifest_exits_non_zero(self, tmp_path, monkeypatch):
        _stub_servers(monkeypatch, resolvable_project_routes(["input_spleen_2.nii.gz"]))

        assert cli.main(_argv(tmp_path / "missing.csv")) == 1


class TestExitCode:
    """A zero exit must mean labels landed, because that is all the automation can see."""

    def test_successful_upload_exits_zero(self, manifest, monkeypatch):
        sessions = _stub_servers(monkeypatch, resolvable_project_routes(["input_spleen_2.nii.gz"]))

        assert cli.main(_argv(manifest)) == 0
        assert len(sessions[0].puts) == 1

    def test_no_matching_scan_anywhere_exits_non_zero(self, tmp_path, label, monkeypatch):
        manifest = tmp_path / "manifest.csv"
        manifest.write_text(f"accession_id,file_path\nNOT_IN_XNAT,{label}\n")
        _stub_servers(monkeypatch, resolvable_project_routes(["input_spleen_2.nii.gz"]))

        # Nothing failed, so `ok` is True — but no destination was found, and previously this
        # reported success and let the smoke walk into a training run with no labels.
        assert cli.main(_argv(manifest)) == 1

    def test_unconverted_scans_exit_non_zero(self, manifest, monkeypatch):
        _stub_servers(monkeypatch, resolvable_project_routes(["something_else.nii.gz"]))

        # Ran before DICOM-to-NIfTI conversion, or the Container Service is broken.
        assert cli.main(_argv(manifest)) == 1

    def test_allow_no_op_accepts_a_run_that_resolved_nothing(self, tmp_path, label, monkeypatch):
        manifest = tmp_path / "manifest.csv"
        manifest.write_text(f"accession_id,file_path\nNOT_IN_XNAT,{label}\n")
        _stub_servers(monkeypatch, resolvable_project_routes(["input_spleen_2.nii.gz"]))

        assert cli.main(_argv(manifest, "--allow-no-op")) == 0

    def test_empty_manifest_exits_zero(self, tmp_path, monkeypatch):
        manifest = tmp_path / "manifest.csv"
        manifest.write_text("accession_id,file_path\n")
        _stub_servers(monkeypatch, resolvable_project_routes(["input_spleen_2.nii.gz"]))

        # Nothing was requested, so nothing resolving is not a failure.
        assert cli.main(_argv(manifest)) == 0

    def test_dry_run_predicts_the_real_runs_exit_code(self, tmp_path, label, manifest, monkeypatch):
        _stub_servers(monkeypatch, resolvable_project_routes(["input_spleen_2.nii.gz"]))
        assert cli.main(_argv(manifest, "--dry-run")) == 0

        unmatched = tmp_path / "unmatched.csv"
        unmatched.write_text(f"accession_id,file_path\nNOT_IN_XNAT,{label}\n")
        _stub_servers(monkeypatch, resolvable_project_routes(["input_spleen_2.nii.gz"]))
        assert cli.main(_argv(unmatched, "--dry-run")) == 1

    def test_require_full_coverage_fails_a_partial_project(self, manifest, monkeypatch):
        _stub_servers(monkeypatch, resolvable_project_routes(["input_spleen_2.nii.gz"]))

        # The stub project holds two scans; the manifest names one.
        assert cli.main(_argv(manifest, "--require-full-coverage")) == 1


class TestMultipleTrusts:
    """One invocation covers the roster — each Trust holds only its own studies."""

    def test_every_credentials_file_is_visited(self, tmp_path, manifest, monkeypatch):
        sessions = _stub_servers(
            monkeypatch,
            resolvable_project_routes(["input_spleen_2.nii.gz"]),
            resolvable_project_routes(["input_spleen_2.nii.gz"]),
        )

        exit_code = cli.main(
            _argv(
                manifest,
                "--credentials-file",
                _credentials(tmp_path, "gstt.json"),
                "--credentials-file",
                _credentials(tmp_path, "kch.json"),
            )
        )

        assert exit_code == 0
        assert [len(session.puts) for session in sessions] == [1, 1]

    def test_one_unreachable_trust_fails_the_run_but_not_the_other_upload(self, tmp_path, manifest, monkeypatch):
        sessions = _stub_servers(
            monkeypatch,
            resolvable_project_routes(["input_spleen_2.nii.gz"]),
            {"/data/projects": FakeResponse(status_code=401, text="denied")},
        )

        exit_code = cli.main(
            _argv(
                manifest,
                "--credentials-file",
                _credentials(tmp_path, "gstt.json"),
                "--credentials-file",
                _credentials(tmp_path, "kch.json"),
            )
        )

        # The healthy Trust is still enriched, and the run still reports failure.
        assert len(sessions[0].puts) == 1
        assert exit_code == 1

    def test_a_trust_without_the_project_does_not_fail_the_roster(self, tmp_path, manifest, monkeypatch):
        sessions = _stub_servers(
            monkeypatch,
            resolvable_project_routes(["input_spleen_2.nii.gz"]),
            resolvable_project_routes(["input_spleen_2.nii.gz"], flip_project_id=None),
        )

        exit_code = cli.main(
            _argv(
                manifest,
                "--credentials-file",
                _credentials(tmp_path, "gstt.json"),
                "--credentials-file",
                _credentials(tmp_path, "kch.json"),
            )
        )

        assert exit_code == 0
        assert [len(session.puts) for session in sessions] == [1, 0]


class TestModuleEntryPoint:
    """`python -m flip.xnat` is a documented alternative to the console script."""

    def test_module_execution_delegates_to_main_and_propagates_its_exit_code(self, monkeypatch):
        monkeypatch.setattr(cli, "main", lambda *args, **kwargs: 3)

        with pytest.raises(SystemExit) as excinfo:
            runpy.run_module("flip.xnat", run_name="__main__")

        # The exit code is the contract the enrichment hook consumes, so it must survive the
        # `python -m` path unchanged rather than collapsing to a bare 0/1.
        assert excinfo.value.code == 3
