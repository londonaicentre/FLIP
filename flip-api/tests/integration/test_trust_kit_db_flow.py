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

"""Downloading a trust's kit from the UI, against real rows (FLIP#1852).

Who may download, when the keys are minted, and that a later download leaves them alone are properties
of the committed ``trust`` and ``trusts_audit`` rows, so they are checked here rather than against mocks.
"""

import base64
import io
import json
import logging
import tarfile
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from fastapi import HTTPException, Response
from sqlmodel import Session, create_engine, select

from flip_api.auth.access_manager import authenticate_trust
from flip_api.config import get_settings
from flip_api.db.models.main_models import Trust, TrustsAudit
from flip_api.db.models.user_models import RoleRef, UserProfile, UserRole
from flip_api.domain.interfaces.trust import ICreateTrust, IRotateTrustKit, ITrustKitDownload
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.domain.schemas.types import FLBackend
from flip_api.trusts_services.admin_create_trust import admin_create_trust
from flip_api.trusts_services.trust_kit import download_trust_kit, get_trust_kit_status, rotate_trust_kit
from tests.integration.conftest import admin_user

FLARE_STARTUP = ("client.crt", "client.key", "rootCA.pem", "fed_client.json", "sub_start.sh")


@pytest.fixture
def kits(tmp_path: Path, monkeypatch) -> Path:
    """A FLARE kit tree holding slot Trust_801, and a hub configured to read it."""
    for name in FLARE_STARTUP:
        path = tmp_path / "net-1/services/Trust_801/startup" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    monkeypatch.setattr(get_settings(), "FL_KITS_URI", str(tmp_path))
    monkeypatch.setattr(get_settings(), "FL_BACKEND", FLBackend.NVFLARE)
    monkeypatch.setattr(get_settings(), "NET_ENDPOINTS", {"net-1": "http://fl-api-net-1:8000"})
    monkeypatch.setenv("CENTRAL_HUB_API_URL", "https://hub.example/api")
    monkeypatch.setenv("FLARE_KIT_DATE", "20261101")
    return tmp_path


@pytest.fixture
def world(session, kits) -> dict:
    """A trust added from the UI, its Trust Admin, another trust's Trust Admin, and a hub admin."""
    hub_admin = admin_user(session)
    with patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names") as slots:
        slots.return_value = ["Trust_801", "Trust_802"]
        # The trust table outlives a test (conftest's _PRESERVED_TABLES), so names are unique per test.
        tag = uuid4().hex[:8]
        created = admin_create_trust(ICreateTrust(name=f"Kit {tag}", code="KIT"), db=session, token_id=hub_admin)
        other = admin_create_trust(ICreateTrust(name=f"Other {tag}", code="OTH"), db=session, token_id=hub_admin)
    trust_admin, other_admin = uuid4(), uuid4()
    session.add(UserRole(user_id=trust_admin, role_id=RoleRef.TRUST_ADMIN.value, trust_id=created.id))
    session.add(UserRole(user_id=other_admin, role_id=RoleRef.TRUST_ADMIN.value, trust_id=other.id))
    session.add(UserProfile(user_id=trust_admin, name="Art Admin", organisation="KIT"))
    session.commit()
    return {"trust": created.id, "hub_admin": hub_admin, "trust_admin": trust_admin, "other_admin": other_admin}


def _download(session, trust_id: UUID, user_id: UUID) -> ITrustKitDownload:
    return download_trust_kit(response=Response(), trust_id=trust_id, db=session, user_id=user_id)


def _kit_json(download: ITrustKitDownload) -> dict:
    content = base64.b64decode(download.content_base64)
    derived = PBKDF2HMAC(algorithm=hashes.SHA256(), length=48, salt=content[8:16], iterations=600_000).derive(
        download.passphrase.encode()
    )
    decryptor = Cipher(algorithms.AES(derived[:32]), modes.CBC(derived[32:])).decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(decryptor.update(content[16:]) + decryptor.finalize()) + unpadder.finalize()
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tar:
        return json.load(tar.extractfile("kit.json"))  # type: ignore[arg-type]


def _actions(session, trust_id: UUID) -> list[TrustAuditAction]:
    session.expire_all()
    rows = session.exec(select(TrustsAudit).where(TrustsAudit.trust_id == trust_id)).all()
    return [row.action for row in sorted(rows, key=lambda row: row.audit_date)]


def test_a_trust_added_from_the_ui_has_no_keys_until_its_kit_is_downloaded(session, world):
    trust = session.get(Trust, world["trust"])
    assert trust.api_key_hash is None

    status = get_trust_kit_status(trust_id=world["trust"], db=session, user_id=world["trust_admin"])

    assert status.keys_issued is False
    assert status.download_count == 0
    assert status.last_download is None
    assert status.fl_kit_slot == "Trust_801"
    assert status.update_available is False


def test_the_first_download_mints_the_keys_and_later_ones_leave_them_alone(session, world):
    response = Response()
    first = download_trust_kit(response=response, trust_id=world["trust"], db=session, user_id=world["trust_admin"])

    assert response.headers["Cache-Control"] == "no-store"
    kit = _kit_json(first)["kit"]
    assert first.kind == "full"
    session.expire_all()
    key_hash = session.get(Trust, world["trust"]).api_key_hash
    assert key_hash is not None
    # The hub accepting the key is the proof that the stored hash is this key's.
    assert authenticate_trust(api_key=kit["trust_api_key"], db=session).id == world["trust"]

    second = _download(session, world["trust"], world["hub_admin"])

    assert second.kind == "update"
    assert second.passphrase != first.passphrase
    assert "trust_api_key" not in _kit_json(second)["kit"]
    session.expire_all()
    assert session.get(Trust, world["trust"]).api_key_hash == key_hash
    assert _actions(session, world["trust"]) == [
        TrustAuditAction.REGISTERED,
        TrustAuditAction.KIT_DOWNLOADED,
        TrustAuditAction.KEYS_ISSUED,
        TrustAuditAction.KIT_DOWNLOADED,
    ]


def test_status_reports_the_last_download_and_a_moved_hub_value(session, world, monkeypatch):
    _download(session, world["trust"], world["trust_admin"])

    status = get_trust_kit_status(trust_id=world["trust"], db=session, user_id=world["hub_admin"])

    assert status.keys_issued is True
    assert status.download_count == 1
    assert status.update_available is False
    assert status.last_download.kind == "full"
    assert status.last_download.by == "Art Admin"
    assert status.last_download.at.utcoffset() is not None

    monkeypatch.setenv("FLARE_KIT_DATE", "20270101")
    assert get_trust_kit_status(trust_id=world["trust"], db=session, user_id=world["hub_admin"]).update_available


def test_issuing_new_keys_refuses_the_old_one(session, world):
    old = _kit_json(_download(session, world["trust"], world["trust_admin"]))["kit"]["trust_api_key"]

    rotated = rotate_trust_kit(
        IRotateTrustKit(confirm_code="KIT"),
        response=Response(),
        trust_id=world["trust"],
        db=session,
        user_id=world["trust_admin"],
    )

    new = _kit_json(rotated)["kit"]["trust_api_key"]
    assert rotated.kind == "full"
    assert new != old
    assert authenticate_trust(api_key=new, db=session).id == world["trust"]
    with pytest.raises(HTTPException) as refused:
        authenticate_trust(api_key=old, db=session)
    assert refused.value.status_code == 401
    session.expire_all()
    rotation = session.exec(select(TrustsAudit).where(TrustsAudit.action == TrustAuditAction.ROTATED)).one()
    assert rotation.modified_by_user_id == world["trust_admin"]


def test_a_first_download_waits_for_one_already_under_way(session, world):
    """Two first downloads must not both mint keys: the second waits on the trust's row, then finds them issued."""
    # A first download part-way: the row locked and the new hash written, not yet committed.
    trust = session.exec(select(Trust).where(Trust.id == world["trust"]).with_for_update()).one()
    trust.api_key_hash = "the-first-download's"
    session.add(trust)
    session.flush()
    # The suite's engine has one connection for every session; a lock needs a second to wait on it.
    other = create_engine(session.get_bind().url)

    def download() -> ITrustKitDownload:
        with Session(other) as own:
            return _download(own, world["trust"], world["hub_admin"])

    with ThreadPoolExecutor(1) as pool:
        waiting = pool.submit(download)
        with pytest.raises(FutureTimeout):
            waiting.result(timeout=1)
        session.commit()
        assert waiting.result(timeout=30).kind == "update"
    other.dispose()

    session.expire_all()
    assert session.get(Trust, world["trust"]).api_key_hash == "the-first-download's"


def test_no_secret_reaches_the_audit_log_or_the_hub_log(session, world, caplog):
    with caplog.at_level(logging.DEBUG):
        first = _download(session, world["trust"], world["trust_admin"])
        rotated = rotate_trust_kit(
            IRotateTrustKit(confirm_code="KIT"),
            response=Response(),
            trust_id=world["trust"],
            db=session,
            user_id=world["trust_admin"],
        )

    secrets = {first.passphrase, rotated.passphrase}
    for download in (first, rotated):
        kit = _kit_json(download)["kit"]
        secrets |= {kit["trust_api_key"], kit["trust_internal_service_key"], kit["hub_shared"]["AES_KEY_BASE64"]}
    session.expire_all()
    rows = session.exec(select(TrustsAudit).where(TrustsAudit.trust_id == world["trust"])).all()
    recorded = json.dumps([row.detail for row in rows]) + caplog.text
    assert "downloaded the full kit" in caplog.text, "the hub's log was not captured"
    assert not [secret for secret in secrets if secret in recorded]


def test_issuing_new_keys_needs_the_trust_code_typed_back(session, world):
    _download(session, world["trust"], world["trust_admin"])
    key_hash = session.get(Trust, world["trust"]).api_key_hash

    with pytest.raises(HTTPException) as refused:
        rotate_trust_kit(
            IRotateTrustKit(confirm_code="kit"),
            response=Response(),
            trust_id=world["trust"],
            db=session,
            user_id=world["trust_admin"],
        )

    assert refused.value.status_code == 400
    session.expire_all()
    assert session.get(Trust, world["trust"]).api_key_hash == key_hash


@pytest.mark.parametrize("route", ["status", "download", "rotate"])
def test_only_the_trusts_own_admin_or_a_hub_admin_may_touch_its_kit(session, world, route):
    """Another trust's admin and a plain researcher are refused, and nothing is minted."""
    researcher = uuid4()
    session.add(UserRole(user_id=researcher, role_id=RoleRef.RESEARCHER.value))
    session.commit()

    calls = {
        "status": lambda user: get_trust_kit_status(trust_id=world["trust"], db=session, user_id=user),
        "download": lambda user: _download(session, world["trust"], user),
        "rotate": lambda user: rotate_trust_kit(
            IRotateTrustKit(confirm_code="KIT"), response=Response(), trust_id=world["trust"], db=session, user_id=user
        ),
    }

    for user in (world["other_admin"], researcher):
        with pytest.raises(HTTPException) as refused:
            calls[route](user)
        assert refused.value.status_code == 403
        session.rollback()

    session.expire_all()
    assert session.get(Trust, world["trust"]).api_key_hash is None
    assert _actions(session, world["trust"]) == [TrustAuditAction.REGISTERED]


def test_an_unknown_trust_is_a_404(session, world):
    with pytest.raises(HTTPException) as missing:
        _download(session, uuid4(), world["hub_admin"])

    assert missing.value.status_code == 404


def test_a_kit_that_cannot_be_built_does_not_use_up_the_keys(session, world, kits):
    """With the FL files gone the download is a 409, and the next one still carries the keys."""
    (kits / "net-1/services/Trust_801/startup/client.key").unlink()

    with pytest.raises(HTTPException) as conflict:
        _download(session, world["trust"], world["trust_admin"])

    assert conflict.value.status_code == 409
    assert "client.key" in conflict.value.detail
    session.expire_all()
    assert session.get(Trust, world["trust"]).api_key_hash is None
    assert _actions(session, world["trust"]) == [TrustAuditAction.REGISTERED]

    (kits / "net-1/services/Trust_801/startup/client.key").write_text("client.key")
    assert _download(session, world["trust"], world["trust_admin"]).kind == "full"
