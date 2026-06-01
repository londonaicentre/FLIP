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

    def test_init_sets_attribute_per_job_type_from_config(self):
        fake_config = {"standard": ["trainer.py", "config.json"], "evaluation": ["evaluator.py"]}
        with patch("flip_api.domain.interfaces.fl._load_job_types_config", return_value=fake_config):
            instance = JobRequiredFiles()

        assert instance.standard == ["trainer.py", "config.json"]
        assert instance.evaluation == ["evaluator.py"]

    def test_init_with_empty_config_sets_no_job_type_attributes(self):
        with patch("flip_api.domain.interfaces.fl._load_job_types_config", return_value={}):
            instance = JobRequiredFiles()

        assert not hasattr(instance, "standard")
