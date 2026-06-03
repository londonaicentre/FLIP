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

"""Tests for the delete_trust deploy CLI.

`delete_one_trust` is the service-layer entry point that the CLI's `main()`
wraps. Both layers are tested here — the script is small enough that
splitting them across two files would just add ceremony.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.scripts.delete_trust import _DEPENDENT_TABLES, delete_one_trust


def _make_trust(name: str = "GSTT") -> Trust:
    return Trust(
        id=uuid4(),
        name=name,
        code="GSTT",
        region="London",
        api_key_hash="hash",
        created_at=datetime.now(timezone.utc),
    )


def _make_session(*, trust: Trust | None, slot: FLKitSlot | None = None, dependent_rowcount: int = 1) -> MagicMock:
    """Build a session-shaped mock that drives the script's two `exec` calls
    (trust lookup, then slot lookup) and reports a consistent rowcount for the
    bulk dependent-table deletes.
    """
    session = MagicMock()

    # `session.exec(...).first()` is called twice — once for the Trust SELECT,
    # once for the FLKitSlot SELECT. Return distinct mocks per call so the
    # tests can vary "trust missing" vs "slot missing" independently.
    trust_exec = MagicMock()
    trust_exec.first.return_value = trust
    slot_exec = MagicMock()
    slot_exec.first.return_value = slot
    session.exec.side_effect = [trust_exec, slot_exec]

    # session.execute(delete(...)) returns a CursorResult-like object with
    # .rowcount; the script writes one row per dependent table.
    cursor_result = MagicMock()
    cursor_result.rowcount = dependent_rowcount
    session.execute.return_value = cursor_result

    return session


def test_delete_one_trust_returns_not_found_when_no_row_matches():
    """When no Trust matches the name, the script returns `not_found` and
    performs no writes — no slot NULL, no dependent deletes, no Trust delete.
    """
    session = _make_session(trust=None)

    result = delete_one_trust("Nope", session)

    assert result == {"status": "not_found", "name": "Nope"}
    session.execute.assert_not_called()
    session.delete.assert_not_called()
    session.commit.assert_not_called()


def test_delete_one_trust_strips_whitespace_before_lookup():
    """`name` is `.strip()`'d so the deploy script can pass a value out of a
    `$(slot_name)` shell expansion without worrying about trailing newlines.
    """
    session = _make_session(trust=None)

    delete_one_trust("  GSTT  ", session)

    # The where-clause is opaque (it's a SQLModel column expression) — assert
    # via the second-trip arg: the "not_found" branch returned the stripped
    # name as the echoed value.
    result = delete_one_trust("  GSTT  ", session=_make_session(trust=None))
    assert result["name"] == "GSTT"


def test_delete_one_trust_happy_path_cascades_dependents_and_frees_slot():
    """End-to-end: trust found + slot found + every dependent table cleared +
    trust row deleted + session committed.
    """
    trust = _make_trust("GSTT")
    slot = FLKitSlot(
        slot_name="Trust_007",
        slot_number=7,
        assigned_to_trust_id=trust.id,
        assigned_at=datetime.now(timezone.utc),
    )
    session = _make_session(trust=trust, slot=slot, dependent_rowcount=3)

    result = delete_one_trust("GSTT", session)

    # Status payload — including the trust metadata and the per-table deletion
    # counts that the deploy script logs.
    assert result["status"] == "deleted"
    assert result["trust_id"] == str(trust.id)
    assert result["name"] == "GSTT"
    assert result["freed_fl_kit_slot"] == "Trust_007"

    # One row per dependent table; the script reports its `__tablename__`.
    counts = result["dependent_rows_deleted"]
    for model, _ in _DEPENDENT_TABLES:
        assert counts[model.__tablename__] == 3

    # The slot's FK + timestamp are cleared in-place and re-added to the
    # session so they get persisted on commit.
    assert slot.assigned_to_trust_id is None
    assert slot.assigned_at is None
    session.add.assert_any_call(slot)

    # One bulk `execute(delete(...))` per dependent table.
    assert session.execute.call_count == len(_DEPENDENT_TABLES)

    # Final wave: the Trust row itself is deleted and the transaction
    # commits once at the very end.
    session.delete.assert_called_once_with(trust)
    session.commit.assert_called_once()


def test_delete_one_trust_proceeds_when_no_slot_is_assigned():
    """A trust without an FL kit slot assignment still gets cleanly removed —
    `freed_fl_kit_slot` is None in the returned payload.
    """
    from flip_api.db.models.main_models import FLKitSlot as _FLKitSlot
    from flip_api.db.models.main_models import TrustsAudit

    trust = _make_trust("ORPHAN")
    session = _make_session(trust=trust, slot=None)

    result = delete_one_trust("ORPHAN", session)

    assert result["status"] == "deleted"
    assert result["freed_fl_kit_slot"] is None
    # The only `session.add` call is the TrustsAudit row — no slot to mutate.
    for call in session.add.call_args_list:
        added = call.args[0]
        assert isinstance(added, TrustsAudit), (
            f"Unexpected session.add({added!r}) — only TrustsAudit + FLKitSlot are allowed"
        )
        assert not isinstance(added, _FLKitSlot)
    session.delete.assert_called_once_with(trust)


def test_delete_one_trust_writes_audit_row_with_null_user():
    """The deploy-CLI path stamps the audit row with ``modified_by_user_id=None``
    (operator identity lives in CloudTrail, not in the FLIP user table)."""
    from flip_api.db.models.main_models import TrustsAudit
    from flip_api.domain.schemas.actions import TrustAuditAction

    trust = _make_trust("GSTT")
    session = _make_session(trust=trust, slot=None)

    delete_one_trust("GSTT", session)

    audit_calls = [
        call for call in session.add.call_args_list if isinstance(call.args[0], TrustsAudit)
    ]
    assert len(audit_calls) == 1
    audit_row = audit_calls[0].args[0]
    assert audit_row.action == TrustAuditAction.DELETED
    assert audit_row.modified_by_user_id is None
    assert audit_row.trust_name == "GSTT"
    assert audit_row.trust_id == trust.id


# ---------------------------------------------------------------------------
# CLI entry point — `main()` parses argv, calls `delete_one_trust`, prints
# the JSON status. Mirrors the test_register_trust_cli `_patch_session` shape.
# ---------------------------------------------------------------------------


def _patch_session(monkeypatch):
    """Replace `Session(engine)` in the CLI with a session-shaped MagicMock."""
    session_mock = MagicMock()
    session_ctx = MagicMock()
    session_ctx.__enter__.return_value = session_mock
    session_ctx.__exit__.return_value = False
    monkeypatch.setattr(
        "flip_api.scripts.delete_trust.Session", lambda _engine: session_ctx
    )
    return session_mock


def test_main_prints_deleted_status_json(monkeypatch, capsys):
    from flip_api.scripts import delete_trust as cli

    _patch_session(monkeypatch)
    monkeypatch.setattr(
        cli,
        "delete_one_trust",
        lambda name, session: {"status": "deleted", "name": name, "freed_fl_kit_slot": "Trust_001"},
    )
    monkeypatch.setattr("sys.argv", ["delete_trust", "--name", "GSTT"])

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "deleted", "name": "GSTT", "freed_fl_kit_slot": "Trust_001"}


def test_main_prints_not_found_status_json(monkeypatch, capsys):
    from flip_api.scripts import delete_trust as cli

    _patch_session(monkeypatch)
    monkeypatch.setattr(
        cli, "delete_one_trust", lambda name, session: {"status": "not_found", "name": name}
    )
    monkeypatch.setattr("sys.argv", ["delete_trust", "--name", "Ghost"])

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "not_found", "name": "Ghost"}


def test_main_requires_name(monkeypatch, capsys):
    """argparse rejects a call without --name and exits 2 (argparse's convention)."""
    from flip_api.scripts import delete_trust as cli

    _patch_session(monkeypatch)
    monkeypatch.setattr("sys.argv", ["delete_trust"])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 2
    assert "--name" in capsys.readouterr().err
