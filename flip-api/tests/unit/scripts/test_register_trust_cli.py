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

"""Tests for the register_trust deploy CLI's core (``register_one_trust``).

The ``register_trust`` *service* is covered in
``tests/unit/trusts_services/services/test_register_trust.py``; here we test the
thin CLI wrapper — the idempotent skip and the kit-dict shaping.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.scripts.register_trust import register_one_trust
from flip_api.trusts_services.services.register_trust import (
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


def test_registers_new_trust_and_returns_one_kit(monkeypatch, session):
    """A new trust → register_trust is called and a single kit dict is returned."""
    captured = {}

    def fake_register_trust(*, name, code, region, session):  # noqa: ARG001
        captured["args"] = (name, code, region)
        return _kit(name)

    monkeypatch.setattr("flip_api.scripts.register_trust.register_trust", fake_register_trust)
    session.exec.return_value.first.return_value = None  # no existing trust

    kits = register_one_trust("Open Trust (EC2)", "OPEN", "London", session)

    assert captured["args"] == ("Open Trust (EC2)", "OPEN", "London")
    assert len(kits) == 1
    kit = kits[0]
    assert kit["trust_name"] == "Open Trust (EC2)"
    assert kit["trust_api_key"] == "plain-api-Open Trust (EC2)"
    assert kit["trust_internal_service_key"] == "plain-internal-Open Trust (EC2)"
    assert kit["fl_kit_slot"] == "Trust_007"
    assert kit["fl_kit_slot_number"] == 7
    assert set(kit) == {
        "trust_id",
        "trust_name",
        "trust_api_key",
        "trust_internal_service_key",
        "fl_kit_slot",
        "fl_kit_slot_number",
    }


def test_skips_already_registered_trust(monkeypatch, session):
    """An existing trust → register_trust is NOT called and [] is returned (idempotent)."""
    monkeypatch.setattr(
        "flip_api.scripts.register_trust.register_trust",
        MagicMock(side_effect=AssertionError("register_trust must not be called")),
    )
    session.exec.return_value.first.return_value = Trust(id=uuid4(), name="Existing Trust")

    kits = register_one_trust("Existing Trust", None, None, session)

    assert kits == []


def test_strips_whitespace_from_name(monkeypatch, session):
    """The name is stripped before the existence check and registration."""

    def fake_register_trust(*, name, code, region, session):  # noqa: ARG001
        return _kit(name)

    monkeypatch.setattr("flip_api.scripts.register_trust.register_trust", fake_register_trust)
    session.exec.return_value.first.return_value = None

    kits = register_one_trust("  Padded Trust  ", None, None, session)

    assert kits[0]["trust_name"] == "Padded Trust"


def test_propagates_registration_error(monkeypatch, session):
    """A service-level failure propagates so the CLI can exit non-zero."""

    def fake_register_trust(*, name, code, region, session):  # noqa: ARG001
        raise NoFreeKitSlotError("No FL kit slots available.")

    monkeypatch.setattr("flip_api.scripts.register_trust.register_trust", fake_register_trust)
    session.exec.return_value.first.return_value = None

    with pytest.raises(NoFreeKitSlotError):
        register_one_trust("New Trust", None, None, session)
