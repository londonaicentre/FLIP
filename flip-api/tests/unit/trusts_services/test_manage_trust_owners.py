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

"""The trust-row lock that makes the last-owner rule safe (FLIP#1258).

The rule itself is a count followed by a write, so what stops two concurrent removals from
stranding a trust is not the count but the lock taken before it. These pin the statement the
lock renders as; that the mutating endpoints actually ask for it is pinned against real
Postgres in ``tests/integration/test_trust_owners_db_flow.py``.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from flip_api.trusts_services.manage_trust_owners import _require_trust


def _compiled(session: MagicMock) -> str:
    """The SQL of the statement the endpoint handed to the session."""
    statement = session.exec.call_args.args[0]
    return str(statement.compile(dialect=postgresql.dialect()))


def test_lock_renders_a_for_update_clause():
    """``lock=True`` must actually lock the row, not merely read it."""
    session = MagicMock()
    session.exec.return_value.first.return_value = MagicMock()

    _require_trust(session, uuid4(), lock=True)

    assert "FOR UPDATE" in _compiled(session)


def test_the_read_path_does_not_lock():
    """The default stays an unlocked read — the list endpoint must not serialize browsers."""
    session = MagicMock()
    session.exec.return_value.first.return_value = MagicMock()

    _require_trust(session, uuid4())

    assert "FOR UPDATE" not in _compiled(session)


def test_an_unknown_nominee_is_404(monkeypatch):
    """A sub Cognito does not know is refused as absent, not accepted as a phantom owner."""
    import flip_api.trusts_services.manage_trust_owners as module

    def _not_found(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="not found")

    monkeypatch.setattr(module, "get_username", _not_found)

    with pytest.raises(HTTPException) as exc_info:
        module._require_existing_user(uuid4())

    assert exc_info.value.status_code == 404


def test_a_cognito_read_failure_is_503_not_404(monkeypatch):
    """A transient Cognito failure must not be reported as "this user does not exist".

    The distinction is load-bearing: the caller retries a 503, and would stop retrying a 404.
    """
    import flip_api.trusts_services.manage_trust_owners as module

    def _unavailable(*_args, **_kwargs):
        raise HTTPException(status_code=500, detail="cognito down")

    monkeypatch.setattr(module, "get_username", _unavailable)

    with pytest.raises(HTTPException) as exc_info:
        module._require_existing_user(uuid4())

    assert exc_info.value.status_code == 503


def test_a_missing_trust_is_404_in_both_modes():
    """The 404 precedes every permission question, locked or not."""
    for lock in (False, True):
        session = MagicMock()
        session.exec.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            _require_trust(session, uuid4(), lock=lock)

        assert exc_info.value.status_code == 404
