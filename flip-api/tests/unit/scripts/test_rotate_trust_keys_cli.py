# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Tests for the rotate_trust_keys deploy CLI's core (``rotate_one_trust``)."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.scripts.rotate_trust_keys import rotate_one_trust
from flip_api.trusts_services.services.register_trust import (
    MissingKitSlotError,
    RegisteredTrust,
)


def _rotated(name: str = "GSTT") -> RegisteredTrust:
    return RegisteredTrust(
        trust=Trust(id=uuid4(), name=name, code=name),
        fl_kit_slot=FLKitSlot(slot_name="Trust_003", slot_number=3),
        trust_api_key="plain-api",  # pragma: allowlist secret
        trust_internal_service_key="plain-internal",  # pragma: allowlist secret
    )


@patch("flip_api.scripts.rotate_trust_keys.rotate_trust_keys")
def test_emits_a_full_kit(mock_rotate):
    """The caller asked for new credentials, so the kit always carries them."""
    mock_rotate.return_value = _rotated()
    session = MagicMock()

    kits = rotate_one_trust("GSTT", None, session)

    assert len(kits) == 1
    kit = kits[0]
    assert kit["trust_name"] == "GSTT"
    assert kit["trust_api_key"] == "plain-api"  # pragma: allowlist secret
    assert kit["trust_internal_service_key"] == "plain-internal"  # pragma: allowlist secret
    assert kit["fl_kit_slot"] == "Trust_003"
    assert kit["fl_kit_slot_number"] == 3
    assert "hub_shared" in kit
    mock_rotate.assert_called_once_with(session, name="GSTT", code=None)


@patch("flip_api.scripts.rotate_trust_keys.rotate_trust_keys")
def test_hub_shared_block_never_carries_empty_strings(mock_rotate, monkeypatch):
    """Unset hub values are omitted, so the distributor does not blank a live trust's env."""
    mock_rotate.return_value = _rotated()
    monkeypatch.setenv("TRUST_API_KEY_HEADER", "X-Trust-Api-Key")
    monkeypatch.delenv("AES_KEY_BASE64", raising=False)

    kit = rotate_one_trust("GSTT", None, MagicMock())[0]

    assert kit["hub_shared"]["TRUST_API_KEY_HEADER"] == "X-Trust-Api-Key"  # pragma: allowlist secret
    assert "AES_KEY_BASE64" not in kit["hub_shared"]


@patch("flip_api.scripts.rotate_trust_keys.rotate_trust_keys")
def test_propagates_service_errors(mock_rotate):
    """A trust with no kit slot must fail loudly rather than print an empty kit."""
    mock_rotate.side_effect = MissingKitSlotError("no slot")

    with pytest.raises(MissingKitSlotError, match="no slot"):
        rotate_one_trust("GSTT", None, MagicMock())
