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

"""One NVFLARE admin session per study (FLIP#1678), and the admin certificate's organisation."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from nvflare.fuel.flare_api.api_spec import AuthenticationError

from fl_api.utils.study_sessions import StudySessions, UnknownStudy, admin_org


def _write_cert(directory, org: str | None) -> None:
    attributes = [x509.NameAttribute(NameOID.COMMON_NAME, "admin@nvidia.com")]
    if org is not None:
        attributes.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, org))
    name = x509.Name(attributes)
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    (directory / "client.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def test_admin_org_reads_the_certificate_organisation(tmp_path):
    _write_cert(tmp_path, "AICentre")

    assert admin_org(tmp_path) == "AICentre"


def test_admin_org_refuses_a_certificate_without_one(tmp_path):
    _write_cert(tmp_path, None)

    with pytest.raises(ValueError, match="organisation"):
        admin_org(tmp_path)


@pytest.fixture
def opened():
    """The study sessions the factory opened, by study."""
    return {}


@pytest.fixture
def studies(tmp_path, opened):
    _write_cert(tmp_path, "AICentre")

    def factory(study: str):
        return opened.setdefault(study, MagicMock(name=f"session-{study}"))

    return StudySessions(MagicMock(name="default"), factory, tmp_path, max_sessions=2)


@pytest.mark.parametrize("study", [None, "", "default"])
def test_get_without_a_study_is_the_default_session(studies, opened, study):
    assert studies.get(study) is studies.default
    assert opened == {}


def test_get_opens_a_study_session_once(studies, opened):
    first = studies.get("p1")

    assert studies.get("p1") is first
    assert list(opened) == ["p1"]


def test_get_evicts_the_least_recently_used_session_and_closes_it(studies, opened):
    studies.get("p1")
    studies.get("p2")
    studies.get("p1")

    studies.get("p3")

    opened["p2"].close.assert_called_once()
    opened["p1"].close.assert_not_called()


@pytest.mark.parametrize("auth_code", ["AUTH_UNKNOWN_STUDY", "AUTH_STUDY_NOT_CONFIGURED"])
def test_get_raises_unknown_study_when_the_server_rejects_the_login(tmp_path, auth_code):
    _write_cert(tmp_path, "AICentre")

    def factory(study: str):
        raise AuthenticationError("unknown study", auth_code=auth_code)

    with pytest.raises(UnknownStudy):
        StudySessions(MagicMock(), factory, tmp_path).get("p1")


def test_get_reraises_any_other_login_failure(tmp_path):
    _write_cert(tmp_path, "AICentre")

    def factory(study: str):
        raise AuthenticationError("bad certificate", auth_code="AUTH_BAD_CERT")

    with pytest.raises(AuthenticationError):
        StudySessions(MagicMock(), factory, tmp_path).get("p1")


def test_forget_closes_the_session_and_the_next_get_opens_a_new_one(studies, opened):
    first = studies.get("p1")

    studies.forget("p1")
    opened.clear()

    first.close.assert_called_once()
    assert studies.get("p1") is not first
    assert list(opened) == ["p1"]


def test_forget_of_a_study_without_a_session_does_nothing(studies):
    studies.forget("p1")


def test_register_uses_site_org_pairs_from_the_admin_certificate(studies):
    studies.register("p1", ["Trust_1", "Trust_2"])

    studies.default.register_study.assert_called_once_with("p1", site_orgs=["AICentre:Trust_1", "AICentre:Trust_2"])


def test_close_all_closes_every_study_session(studies, opened):
    studies.get("p1")
    studies.get("p2")

    studies.close_all()

    opened["p1"].close.assert_called_once()
    opened["p2"].close.assert_called_once()


def test_open_study_session_connects_once_in_that_study():
    from fl_api.startup import session_manager

    with (
        patch.object(session_manager, "FLIP_Session") as session_class,
        patch.object(session_manager, "get_settings") as settings,
    ):
        settings.return_value.TIMEOUT_SESSION_CONNECT = 5.0
        session = session_manager.open_study_session("p1")

    assert session_class.call_args.kwargs["study"] == "p1"
    session.try_connect.assert_called_once_with(5.0)
