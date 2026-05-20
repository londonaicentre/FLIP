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

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.scripts.register_deploy_trusts import DeployTrust, register_deploy_trusts
from flip_api.trusts_services.services.register_trust import (
    DuplicateTrustError,
    NoFreeKitSlotError,
    RegisteredTrust,
)


def _kit(name: str, slot_name: str = "Trust_007", slot_number: int = 7) -> RegisteredTrust:
    return RegisteredTrust(
        trust=Trust(id=uuid4(), name=name),
        fl_kit_slot=FLKitSlot(slot_name=slot_name, slot_number=slot_number),
        trust_api_key=f"plain-api-{name}",
        trust_internal_service_key=f"plain-internal-{name}",
    )


@pytest.fixture
def session():
    return MagicMock()


def test_validates_pydantic_schema():
    """Each entry needs a non-empty name; code / region are optional."""
    DeployTrust(name="Open Trust (EC2)", code="OPEN", region="London")
    DeployTrust(name="(Mock) GSTT")  # code/region optional

    with pytest.raises(Exception, match="name"):
        DeployTrust(name="")  # blank


def test_registers_new_trusts_and_returns_kits(monkeypatch, session):
    """Each new entry calls register_trust and contributes one kit to the output."""
    captured: list[tuple[str, str | None, str | None]] = []

    def fake_register_trust(*, name, code, region, session):  # noqa: ARG001
        captured.append((name, code, region))
        return _kit(name)

    monkeypatch.setattr(
        "flip_api.scripts.register_deploy_trusts.register_trust", fake_register_trust
    )
    # Existing-row lookup: both entries return None (no clash) so both proceed.
    session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(first=MagicMock(return_value=None)),
    ]

    kits, failures = register_deploy_trusts(
        session,
        [DeployTrust(name="Open Trust (EC2)"), DeployTrust(name="(Mock) GSTT")],
    )

    assert failures == 0
    assert captured == [("Open Trust (EC2)", None, None), ("(Mock) GSTT", None, None)]
    assert {k["trust_name"] for k in kits} == {"Open Trust (EC2)", "(Mock) GSTT"}
    assert kits[0]["trust_api_key"] == "plain-api-Open Trust (EC2)"
    assert kits[0]["fl_kit_slot"] == "Trust_007"


def test_skips_already_registered_trusts(monkeypatch, session):
    """A pre-existing trust row is skipped silently, no new kit emitted."""
    register_called: list[str] = []

    def fake_register_trust(*, name, code, region, session):  # noqa: ARG001
        register_called.append(name)
        return _kit(name)

    monkeypatch.setattr(
        "flip_api.scripts.register_deploy_trusts.register_trust", fake_register_trust
    )
    # Pre-existing trust returned for the first lookup; second is fresh.
    session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=Trust(id=uuid4(), name="Open Trust (EC2)"))),
        MagicMock(first=MagicMock(return_value=None)),
    ]

    kits, failures = register_deploy_trusts(
        session,
        [DeployTrust(name="Open Trust (EC2)"), DeployTrust(name="(Mock) GSTT")],
    )

    assert failures == 0
    assert register_called == ["(Mock) GSTT"]
    assert [k["trust_name"] for k in kits] == ["(Mock) GSTT"]


def test_continues_past_failure_returns_nonzero_count(monkeypatch, session):
    """A failure on one entry doesn't abort the batch; successful kits still emit."""
    def fake_register_trust(*, name, code, region, session):  # noqa: ARG001
        if name == "Open Trust (EC2)":
            raise NoFreeKitSlotError("No FL kit slots available.")
        return _kit(name)

    monkeypatch.setattr(
        "flip_api.scripts.register_deploy_trusts.register_trust", fake_register_trust
    )
    session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(first=MagicMock(return_value=None)),
    ]

    kits, failures = register_deploy_trusts(
        session,
        [DeployTrust(name="Open Trust (EC2)"), DeployTrust(name="(Mock) GSTT")],
    )

    assert failures == 1
    assert [k["trust_name"] for k in kits] == ["(Mock) GSTT"]
    # The failure path rolls back so the slot lock is released.
    session.rollback.assert_called_once()


def test_idempotent_when_all_already_registered(monkeypatch, session):
    """Empty-output contract: every entry exists already → kits=[], failures=0."""
    monkeypatch.setattr(
        "flip_api.scripts.register_deploy_trusts.register_trust",
        MagicMock(side_effect=AssertionError("register_trust must not be called")),
    )
    session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=Trust(id=uuid4(), name="A"))),
        MagicMock(first=MagicMock(return_value=Trust(id=uuid4(), name="B"))),
    ]

    kits, failures = register_deploy_trusts(
        session, [DeployTrust(name="A"), DeployTrust(name="B")]
    )

    assert kits == []
    assert failures == 0


def test_failure_propagates_only_for_failed_entry(monkeypatch, session):
    """A DuplicateTrustError from register_trust counts as a failure (kept off kits)."""
    def fake_register_trust(*, name, code, region, session):  # noqa: ARG001
        raise DuplicateTrustError("dup")

    monkeypatch.setattr(
        "flip_api.scripts.register_deploy_trusts.register_trust", fake_register_trust
    )
    session.exec.side_effect = [MagicMock(first=MagicMock(return_value=None))]

    kits, failures = register_deploy_trusts(session, [DeployTrust(name="A")])

    assert kits == []
    assert failures == 1
