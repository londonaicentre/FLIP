from flip_api.domain.interfaces.fl import ClientStatus, IClientStatus


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
