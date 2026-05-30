from unittest.mock import patch

from flip_api.domain.interfaces.fl import ClientStatus, IClientStatus, JobRequiredFiles


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
            assert JobRequiredFiles.get_required_files("standard", "nvflare") == [
                "trainer.py",
                "config.json",
            ]
            assert JobRequiredFiles.get_required_files("evaluation", "nvflare") == ["evaluator.py"]

        # The backend is forwarded to the manifest loader.
        assert mock_load.call_args[0][0] == "nvflare"

    def test_is_valid_job_type_checks_manifest_keys(self):
        # The valid set is data (manifest keys), not a hard-coded enum.
        fake_config = {"standard": ["trainer.py"], "evaluation": ["evaluator.py"]}
        with patch("flip_api.domain.interfaces.fl._load_job_types_config", return_value=fake_config) as mock_load:
            assert JobRequiredFiles.is_valid_job_type("standard", "flower") is True
            assert JobRequiredFiles.is_valid_job_type("nonexistent", "flower") is False

        assert mock_load.call_args[0][0] == "flower"

    def test_get_all_job_types_with_files_returns_config_copy(self):
        fake_config = {"standard": ["trainer.py", "config.json"], "evaluation": ["evaluator.py"]}
        with patch("flip_api.domain.interfaces.fl._load_job_types_config", return_value=fake_config):
            result = JobRequiredFiles.get_all_job_types_with_files("flower")

        assert result == fake_config

    def test_get_required_files_empty_config_returns_empty_list(self):
        with patch("flip_api.domain.interfaces.fl._load_job_types_config", return_value={}):
            assert JobRequiredFiles.get_required_files("standard", "nvflare") == []
