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
        "hub_shared",
    }


def test_skip_path_emits_metadata_without_credentials(monkeypatch, session):
    """An existing trust → register_trust is NOT called; returns one metadata-only kit (no creds)."""
    monkeypatch.setattr(
        "flip_api.scripts.register_trust.register_trust",
        MagicMock(side_effect=AssertionError("register_trust must not be called")),
    )
    existing_trust = Trust(id=uuid4(), name="Existing Trust")
    existing_slot = FLKitSlot(slot_name="Trust_2", slot_number=2, assigned_to_trust_id=existing_trust.id)
    session.exec.return_value.first.side_effect = [existing_trust, existing_slot]

    kits = register_one_trust("Existing Trust", None, None, session)

    assert len(kits) == 1
    kit = kits[0]
    assert kit["trust_name"] == "Existing Trust"
    assert kit["fl_kit_slot"] == "Trust_2"
    assert kit["fl_kit_slot_number"] == 2
    assert "trust_api_key" not in kit
    assert "trust_internal_service_key" not in kit
    assert "hub_shared" in kit


def test_strips_whitespace_from_name(monkeypatch, session):
    """The name is stripped before the existence check and registration."""

    def fake_register_trust(*, name, code, region, session):  # noqa: ARG001
        return _kit(name)

    monkeypatch.setattr("flip_api.scripts.register_trust.register_trust", fake_register_trust)
    session.exec.return_value.first.return_value = None

    kits = register_one_trust("  Padded Trust  ", None, None, session)

    assert kits[0]["trust_name"] == "Padded Trust"


def test_require_existing_errors_on_missing_trust(session):
    """--require-existing aborts with SystemExit(1) when the trust has never been registered."""
    session.exec.return_value.first.return_value = None  # trust not found

    with pytest.raises(SystemExit) as excinfo:
        register_one_trust("Ghost Trust", None, None, session, require_existing=True)

    assert excinfo.value.code == 1


def test_require_existing_allows_existing_trust(monkeypatch, session):
    """--require-existing returns the metadata-only kit when the trust already exists."""
    monkeypatch.setattr(
        "flip_api.scripts.register_trust.register_trust",
        MagicMock(side_effect=AssertionError("register_trust must not be called")),
    )
    existing_trust = Trust(id=uuid4(), name="Known Trust")
    existing_slot = FLKitSlot(slot_name="Trust_1", slot_number=1, assigned_to_trust_id=existing_trust.id)
    session.exec.return_value.first.side_effect = [existing_trust, existing_slot]

    kits = register_one_trust("Known Trust", None, None, session, require_existing=True)

    assert len(kits) == 1
    kit = kits[0]
    assert kit["trust_name"] == "Known Trust"
    assert kit["fl_kit_slot"] == "Trust_1"
    assert "trust_api_key" not in kit
    assert "trust_internal_service_key" not in kit


def test_propagates_registration_error(monkeypatch, session):
    """A service-level failure propagates so the CLI can exit non-zero."""

    def fake_register_trust(*, name, code, region, session):  # noqa: ARG001
        raise NoFreeKitSlotError("No FL kit slots available.")

    monkeypatch.setattr("flip_api.scripts.register_trust.register_trust", fake_register_trust)
    session.exec.return_value.first.return_value = None

    with pytest.raises(NoFreeKitSlotError):
        register_one_trust("New Trust", None, None, session)


# ---------------------------------------------------------------------------
# CLI entry point — `main()` parses argv, calls `register_one_trust`, writes
# the kit JSON to stdout, and exits 1 on TrustRegistrationError. The body
# below is what `deploy/providers/AWS/scripts/register-trusts.sh` (and the
# Makefile's `register-trust-<n>` targets) drive, so it is worth its own
# coverage.
# ---------------------------------------------------------------------------


def _patch_session(monkeypatch):
    """Replace `Session(engine)` in the CLI with a session-shaped MagicMock."""
    session_mock = MagicMock()
    session_ctx = MagicMock()
    session_ctx.__enter__.return_value = session_mock
    session_ctx.__exit__.return_value = False
    monkeypatch.setattr(
        "flip_api.scripts.register_trust.Session", lambda _engine: session_ctx
    )
    return session_mock


def test_main_happy_path_prints_kit_json(monkeypatch, capsys):
    """Happy path: CLI emits a JSON-array of one kit object on stdout."""
    from flip_api.scripts import register_trust as cli

    _patch_session(monkeypatch)
    monkeypatch.setattr(
        cli,
        "register_one_trust",
        lambda name, code, region, session, require_existing=False: [
            {
                "trust_id": "tid",
                "trust_name": name,
                "trust_api_key": "k",
                "trust_internal_service_key": "ik",
                "fl_kit_slot": "Trust_001",
                "fl_kit_slot_number": 1,
            }
        ],
    )
    monkeypatch.setattr(
        "sys.argv", ["register_trust", "--name", "Open Trust", "--code", "OPEN", "--region", "London"]
    )

    cli.main()

    out = capsys.readouterr().out
    import json as _json

    payload = _json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["trust_name"] == "Open Trust"
    assert payload[0]["fl_kit_slot_number"] == 1


def test_main_skip_prints_metadata_only_kit(monkeypatch, capsys):
    """Idempotent skip: an already-registered trust → stdout is a one-element JSON array without credentials."""
    from flip_api.scripts import register_trust as cli

    _patch_session(monkeypatch)
    monkeypatch.setattr(
        cli,
        "register_one_trust",
        lambda name, code, region, session, require_existing=False: [
            {
                "trust_id": "tid",
                "trust_name": name,
                "fl_kit_slot": "Trust_002",
                "fl_kit_slot_number": 2,
                "hub_shared": {"AES_KEY_BASE64": "Zm9vYmFy"},
            }
        ],
    )
    monkeypatch.setattr("sys.argv", ["register_trust", "--name", "Existing Trust"])

    cli.main()

    import json as _json

    payload = _json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert len(payload) == 1
    kit = payload[0]
    assert kit["trust_name"] == "Existing Trust"
    assert kit["fl_kit_slot_number"] == 2
    assert "trust_api_key" not in kit
    assert "trust_internal_service_key" not in kit
    assert kit["hub_shared"] == {"AES_KEY_BASE64": "Zm9vYmFy"}


def test_main_exits_1_on_registration_error(monkeypatch, capsys):
    """Service failure: rollback, print `[]`, sys.exit(1)."""
    from flip_api.scripts import register_trust as cli

    session = _patch_session(monkeypatch)

    def boom(*_a, **_kw):
        raise TrustRegistrationError("kit slot pool exhausted")

    monkeypatch.setattr(cli, "register_one_trust", boom)
    monkeypatch.setattr("sys.argv", ["register_trust", "--name", "Doomed"])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    assert capsys.readouterr().out.strip() == "[]"
    session.rollback.assert_called_once()


def test_main_requires_name(monkeypatch, capsys):
    """argparse rejects a call with no --name and exits 2 (the argparse convention)."""
    from flip_api.scripts import register_trust as cli

    _patch_session(monkeypatch)
    monkeypatch.setattr("sys.argv", ["register_trust"])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 2  # argparse error code
    # argparse writes the "the following arguments are required" message to stderr.
    assert "--name" in capsys.readouterr().err


# TrustRegistrationError needs to be importable at the module level (used by main).
from flip_api.trusts_services.services.register_trust import TrustRegistrationError  # noqa: E402

# ---------------------------------------------------------------------------
# hub_shared block
# ---------------------------------------------------------------------------

HUB_SHARED_KEYS = (
    "AES_KEY_BASE64",
    "CENTRAL_HUB_API_URL",
    "TRUST_API_KEY_HEADER",
    "FL_BACKEND",
    "FLOWER_KIT_DATE",
    "FLARE_KIT_DATE",
    "DOCKER_TAG",
    "DOCKER_FL_TAG",
    "DOCKER_FL_REGISTRY",
    "DOCKER_FL_CLIENT_NAME",
    "UPLOADED_FEDERATED_DATA_BUCKET",
    "NLB_SUBDOMAIN",
    "FL_SERVER_PORT",
)


def test_kit_dict_includes_hub_shared_block_from_env(monkeypatch, session):
    """Hub-shared values from os.environ are copied into the emitted kit dict."""
    monkeypatch.setattr("flip_api.scripts.register_trust.register_trust", lambda **kw: _kit(kw["name"]))
    session.exec.return_value.first.return_value = None

    env = {
        "AES_KEY_BASE64": "Zm9vYmFy",
        "CENTRAL_HUB_API_URL": "https://hub.example/api",
        "TRUST_API_KEY_HEADER": "Authorization",
        "FL_BACKEND": "flower",
        "FLOWER_KIT_DATE": "20260401",
        "FLARE_KIT_DATE": "20260318",
        "DOCKER_TAG": "stag",
        "DOCKER_FL_TAG": "stag",
        "DOCKER_FL_REGISTRY": "ghcr.io/londonaicentre/",
        "DOCKER_FL_CLIENT_NAME": "flower-fl-client",
        "UPLOADED_FEDERATED_DATA_BUCKET": "flip-federated-data",
        "NLB_SUBDOMAIN": "fl-server.example.internal",
        "FL_SERVER_PORT": "8002",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    kit = register_one_trust("Open Trust (EC2)", None, None, session)[0]

    assert kit["hub_shared"] == env


def test_kit_dict_hub_shared_omits_unset_env_vars(monkeypatch, session):
    """Env vars that are unset are simply not included (no empty strings)."""
    monkeypatch.setattr("flip_api.scripts.register_trust.register_trust", lambda **kw: _kit(kw["name"]))
    session.exec.return_value.first.return_value = None
    for key in HUB_SHARED_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AES_KEY_BASE64", "Zm9vYmFy")

    kit = register_one_trust("Open Trust (EC2)", None, None, session)[0]

    assert kit["hub_shared"] == {"AES_KEY_BASE64": "Zm9vYmFy"}


def test_skip_path_also_emits_hub_shared(monkeypatch, session):
    """Idempotent skip still returns one kit dict so the distributor can sync shared values."""
    existing_trust = Trust(id=uuid4(), name="Open Trust (EC2)")
    existing_slot = FLKitSlot(slot_name="Trust_1", slot_number=1, assigned_to_trust_id=existing_trust.id)
    session.exec.return_value.first.side_effect = [existing_trust, existing_slot]

    monkeypatch.setenv("AES_KEY_BASE64", "Zm9vYmFy")

    kits = register_one_trust("Open Trust (EC2)", None, None, session)

    assert len(kits) == 1
    kit = kits[0]
    assert kit["trust_name"] == "Open Trust (EC2)"
    assert kit["hub_shared"]["AES_KEY_BASE64"] == "Zm9vYmFy"
    assert "trust_api_key" not in kit  # creds are NOT in the skip-path kit
    assert "trust_internal_service_key" not in kit
