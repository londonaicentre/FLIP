import json
from unittest.mock import patch

from flip_api.domain.interfaces.fl import (
    ASSETS_DIR,
    ClientStatus,
    IClientStatus,
    JobRequiredFiles,
    _load_job_types_config,
    required_job_types_file,
)
from flip_api.domain.schemas.types import FLBackend


class TestIClientStatusSchema:
    def test_creation(self):
        client_status = IClientStatus(name="client1", status="no_jobs")

        assert client_status.name == "client1"
        assert client_status.status == "no_jobs"

    def test_online_true_when_status_not_no_reply(self):
        client_status = IClientStatus(
            name="client1",
            status=ClientStatus.NO_JOBS.value,
        )

        assert client_status.online is True

    def test_online_false_when_status_no_reply(self):
        client_status = IClientStatus(
            name="client1",
            status=ClientStatus.NO_REPLY.value,
        )

        assert client_status.online is False

    def test_online_true_when_status_connected(self):
        client_status = IClientStatus(
            name="client1",
            status=ClientStatus.CONNECTED.value,
        )

        assert client_status.online is True

    def test_online_false_when_status_disconnected(self):
        client_status = IClientStatus(
            name="client1",
            status=ClientStatus.DISCONNECTED.value,
        )

        assert client_status.online is False

    def test_online_true_when_status_disconnected_lowercase(self):
        client_status = IClientStatus(
            name="client1",
            status="disconnected",
        )

        assert client_status.online is True

    def test_online_reacts_to_status_change(self):
        client_status = IClientStatus(
            name="client1",
            status=ClientStatus.NO_JOBS.value,
        )

        assert client_status.online is True

        client_status.status = ClientStatus.NO_REPLY.value
        assert client_status.online is False


class TestJobRequiredFiles:
    def test_init_does_not_raise(self):
        # Regression: __init__ previously called self._load_job_types_config(), which doesn't exist
        # on the class and would raise AttributeError if anything ever instantiated the model.
        JobRequiredFiles()

    def test_get_required_files_reads_per_backend_config(self):
        # Required files are data, loaded per-backend from the on-disk manifest at call time.
        # job_type is a plain string (no enum) validated against the manifest keys.
        fake_config = {"standard": ["trainer.py", "config.json"], "evaluation": ["evaluator.py"]}
        with patch("flip_api.domain.interfaces.fl._load_job_types_config", return_value=fake_config) as mock_load:
            assert JobRequiredFiles.get_required_files("standard", FLBackend.NVFLARE) == [
                "trainer.py",
                "config.json",
            ]
            assert JobRequiredFiles.get_required_files("evaluation", FLBackend.NVFLARE) == ["evaluator.py"]

        # The backend is forwarded to the manifest loader.
        assert mock_load.call_args[0][0] == FLBackend.NVFLARE

    def test_is_valid_job_type_checks_manifest_keys(self):
        # The valid set is data (manifest keys), not a hard-coded enum.
        fake_config = {"standard": ["trainer.py"], "evaluation": ["evaluator.py"]}
        with patch("flip_api.domain.interfaces.fl._load_job_types_config", return_value=fake_config) as mock_load:
            assert JobRequiredFiles.is_valid_job_type("standard", FLBackend.FLOWER) is True
            assert JobRequiredFiles.is_valid_job_type("nonexistent", FLBackend.FLOWER) is False

        assert mock_load.call_args[0][0] == FLBackend.FLOWER

    def test_get_all_job_types_with_files_returns_config_copy(self):
        fake_config = {"standard": ["trainer.py", "config.json"], "evaluation": ["evaluator.py"]}
        with patch("flip_api.domain.interfaces.fl._load_job_types_config", return_value=fake_config):
            result = JobRequiredFiles.get_all_job_types_with_files(FLBackend.FLOWER)

        assert result == fake_config

    def test_get_required_files_empty_config_returns_empty_list(self):
        with patch("flip_api.domain.interfaces.fl._load_job_types_config", return_value={}):
            assert JobRequiredFiles.get_required_files("standard", FLBackend.NVFLARE) == []


class TestManifestLoading:
    def test_required_job_types_file_builds_per_backend_path(self):
        # The manifest path is per-backend, anchored under the assets dir.
        assert required_job_types_file(FLBackend.NVFLARE) == ASSETS_DIR / "job_types_and_required_files.nvflare.json"
        assert required_job_types_file(FLBackend.FLOWER) == ASSETS_DIR / "job_types_and_required_files.flower.json"

    def test_load_job_types_config_reads_manifest_from_disk(self, tmp_path):
        # _load_job_types_config reads and parses the on-disk manifest for the backend.
        manifest = tmp_path / "job_types_and_required_files.nvflare.json"
        manifest.write_text(json.dumps({"standard": ["trainer.py", "config.json"]}))
        with patch("flip_api.domain.interfaces.fl.required_job_types_file", return_value=manifest):
            assert _load_job_types_config(FLBackend.NVFLARE) == {"standard": ["trainer.py", "config.json"]}

    def test_load_job_types_config_returns_empty_when_manifest_missing(self, tmp_path):
        # If the manifest was never pulled (e.g. S3 unreachable), loading must not crash.
        with patch("flip_api.domain.interfaces.fl.required_job_types_file", return_value=tmp_path / "absent.json"):
            assert _load_job_types_config(FLBackend.FLOWER) == {}
