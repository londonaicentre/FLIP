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

import json
from unittest.mock import ANY, MagicMock, patch
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.scripts.register_trust import _write_kit_to_ssm, register_one_trust
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
        trust_aes_key=f"plain-aes-{name}",
        trust_aes_kid=f"trust-{name}",
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
    assert kit["trust_aes_key"] == "plain-aes-Open Trust (EC2)"
    assert kit["trust_aes_kid"] == "trust-Open Trust (EC2)"
    assert set(kit) == {
        "trust_id",
        "trust_name",
        "trust_api_key",
        "trust_internal_service_key",
        "trust_aes_key",
        "trust_aes_kid",
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

# Mirror of HUB_SHARED_ENV_KEYS in flip_api/scripts/register_trust.py — kept
# local (rather than importing the production tuple) so the tests fail loudly
# if the production list changes without an explicit test update.
HUB_SHARED_KEYS = (
    "AES_KEY_BASE64",
    "CENTRAL_HUB_API_URL",
    "TRUST_API_KEY_HEADER",
    "FL_BACKEND",
    "FLOWER_KIT_DATE",
    "FLARE_KIT_DATE",
    "DOCKER_TAG",
    "DOCKER_REGISTRY",
    "DOCKER_FL_TAG",
    "DOCKER_FL_REGISTRY",
    "NLB_SUBDOMAIN",
    "FL_SERVER_PORT",
)


def test_kit_dict_includes_hub_shared_block_from_env(monkeypatch, session):
    """Hub-shared values from os.environ are copied into the emitted kit dict."""
    monkeypatch.setattr("flip_api.scripts.register_trust.register_trust", lambda **kw: _kit(kw["name"]))
    session.exec.return_value.first.return_value = None

    # Clear all hub-shared keys first so the host shell env doesn't leak into
    # the result and cause a spurious mismatch — only the explicit `env` dict
    # below should appear in the emitted hub_shared block.
    for key in HUB_SHARED_KEYS:
        monkeypatch.delenv(key, raising=False)

    env = {
        "AES_KEY_BASE64": "Zm9vYmFy",
        "CENTRAL_HUB_API_URL": "https://hub.example/api",
        "TRUST_API_KEY_HEADER": "Authorization",
        "FL_BACKEND": "flower",
        "FLOWER_KIT_DATE": "20260401",
        "FLARE_KIT_DATE": "20260318",
        "DOCKER_TAG": "stag",
        "DOCKER_REGISTRY": "ghcr.io/londonaicentre/",
        "DOCKER_FL_TAG": "stag",
        "DOCKER_FL_REGISTRY": "ghcr.io/londonaicentre/",
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


def test_write_kit_to_ssm_uses_securestring_and_overwrites():
    """The SSM handoff path must write SecureString (encrypted at rest) and
    allow ``Overwrite=True`` so that a retried deploy run isn't blocked by
    a leftover parameter from a previous attempt.

    Locks in S-1's transport: the kit body must NEVER go to stdout in the
    SSM mode — the deploy script consumes it out-of-band via the parameter
    name, and CloudWatch only ever sees the name.
    """
    fake_client = MagicMock()
    with patch("flip_api.scripts.register_trust.boto3.session.Session") as session_cls:
        session_cls.return_value.client.return_value = fake_client
        kits = [{"trust_id": str(uuid4()), "trust_api_key": "super-secret"}]

        _write_kit_to_ssm(kits, "/flip/trust-kits/ephemeral/abc")

    session_cls.return_value.client.assert_called_once_with("ssm", region_name=ANY)
    fake_client.put_parameter.assert_called_once()
    kwargs = fake_client.put_parameter.call_args.kwargs
    assert kwargs["Name"] == "/flip/trust-kits/ephemeral/abc"
    assert kwargs["Type"] == "SecureString"
    assert kwargs["Overwrite"] is True
    assert json.loads(kwargs["Value"]) == kits


def test_write_kit_to_ssm_exits_on_put_failure():
    """A failed PUT must exit non-zero — the deploy script must NOT proceed
    on a partial write (the credentials block is unrecoverable from the hub).
    """
    fake_client = MagicMock()
    fake_client.put_parameter.side_effect = RuntimeError("ssm down")
    with patch("flip_api.scripts.register_trust.boto3.session.Session") as session_cls:
        session_cls.return_value.client.return_value = fake_client

        with pytest.raises(SystemExit) as exc_info:
            _write_kit_to_ssm([{"x": 1}], "/flip/trust-kits/ephemeral/abc")

    assert exc_info.value.code == 1


def test_hub_shared_keys_in_lockstep():
    """flip-api ``HUB_SHARED_ENV_KEYS`` and ``trust_kit_lib.HUB_SHARED_KEYS`` must match.

    ``register_trust.py`` (the emitter) runs inside the flip-api container and
    cannot import the root ``scripts/`` package, so the hub-shared key set is
    defined in two places. ``scripts/trust_kit_lib.py`` is the single
    operator-side definition — ``sync_trust_kit.py`` and
    ``distribute_trust_kits.py`` both import it, and the prod
    ``register-trusts.sh`` delegates kit writing to the distributor. Drift
    between the emitter and the writer produces silent half-syncs (a key added
    to one but not the other survives a register run but vanishes on sync); this
    test catches that on PR review rather than at next-deploy time.
    """
    import re
    from pathlib import Path

    from flip_api.scripts.register_trust import HUB_SHARED_ENV_KEYS

    # Walk up from this test file to find the FLIP repo root (the directory
    # that contains both `flip-api/` and `scripts/`).
    repo_root = Path(__file__).resolve()
    while repo_root.name and not (repo_root / "flip-api").is_dir():
        repo_root = repo_root.parent
    assert repo_root.name, "Could not locate FLIP repo root from test file"

    lib_path = repo_root / "scripts" / "trust_kit_lib.py"

    # Parse scripts/trust_kit_lib.py:HUB_SHARED_KEYS — a tuple of string literals.
    lib_src = lib_path.read_text()
    match = re.search(r"HUB_SHARED_KEYS\s*:[^=]*=\s*\((?P<body>[^)]*)\)", lib_src)
    assert match, f"HUB_SHARED_KEYS tuple not found in {lib_path}"
    lib_keys = tuple(re.findall(r'"([A-Z0-9_]+)"', match.group("body")))

    assert HUB_SHARED_ENV_KEYS == lib_keys, (
        f"flip-api register_trust.py and scripts/trust_kit_lib.py disagree:\n"
        f"  register_trust.py:HUB_SHARED_ENV_KEYS = {HUB_SHARED_ENV_KEYS}\n"
        f"  trust_kit_lib.py:HUB_SHARED_KEYS      = {lib_keys}"
    )
