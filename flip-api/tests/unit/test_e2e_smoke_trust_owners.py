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

"""Unit tests for the smoke's Trust Owner bootstrap (ensure_trust_owners).

Approving a project is a site decision (FLIP#1258), so the admin who approves needs
CAN_APPROVE_FOR_TRUST *at each trust* — which no role carries platform-wide, and which nobody
holds at a trust registered after the continuity migration ran. These pin the call the smoke
makes to close that, without a hub.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest

from tests import e2e_smoke
from tests.e2e_smoke import SmokeFailure, ensure_trust_owners

TRUSTS = [
    {"id": "id-1", "name": "Guy's and St Thomas' Trust", "code": "GSTT"},
    {"id": "id-2", "name": "Bangkok Dusit Medical Services", "code": "BDMS"},
]
CALLER = "6f1c2f7e-0000-4000-8000-000000000001"


class _Response:
    """The slice of ``requests.Response`` the smoke's helpers touch."""

    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = {} if payload is None else payload
        self.text = "" if status_code < 300 else f"HTTP {status_code}"

    def json(self) -> Any:
        return self._payload


@dataclass
class _Recorder:
    """What the stubbed transport saw."""

    gets: list[str] = field(default_factory=list)
    posts: list[tuple[str, dict]] = field(default_factory=list)


def _stub(monkeypatch: pytest.MonkeyPatch, *, post_status: int = 201) -> _Recorder:
    """Stub the smoke's transport; ``/users/me`` answers as the caller, owners POSTs as asked."""
    rec = _Recorder()

    def _get(client: Any, path: str, headers: dict, timeout: int = 30) -> _Response:
        rec.gets.append(path)
        return _Response(200, {"id": CALLER})

    def _post(client: Any, path: str, json: dict, headers: dict, timeout: int = 30) -> _Response:
        rec.posts.append((path, json))
        return _Response(post_status, {"user_id": json["user_id"]})

    monkeypatch.setattr(e2e_smoke, "_get", _get)
    monkeypatch.setattr(e2e_smoke, "_post", _post)
    return rec


def test_bootstraps_every_trust_as_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """One POST per trust, nominating the authenticated caller — the bootstrap exemption.

    The caller is resolved from the token (``/users/me``) rather than supplied by the smoke,
    so the grant names the identity the hub will later authorise the approve as.
    """
    rec = _stub(monkeypatch)

    ensure_trust_owners(client=None, headers={}, trusts=TRUSTS)  # type: ignore[arg-type]

    assert rec.gets == ["/users/me"]
    assert rec.posts == [
        ("/admin/trusts/id-1/owners", {"user_id": CALLER}),
        ("/admin/trusts/id-2/owners", {"user_id": CALLER}),
    ]


def test_a_trust_owned_by_someone_else_is_reported_but_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """403 is a governance fact, not a bootstrap failure — the remaining trusts still run.

    A trust that already has an owner the caller is not is a stack where approval rests with
    that owner; the smoke cannot fix it from here, and the approve step names the trust.
    """
    rec = _stub(monkeypatch, post_status=403)

    ensure_trust_owners(client=None, headers={}, trusts=TRUSTS)  # type: ignore[arg-type]

    assert [path for path, _ in rec.posts] == ["/admin/trusts/id-1/owners", "/admin/trusts/id-2/owners"]


def test_an_existing_grant_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment the continuity migration reached already has the admin as owner.

    POST is idempotent there: it returns the existing grant rather than a conflict, which is
    what lets the same code path serve a fresh stack and an upgraded one.
    """
    rec = _stub(monkeypatch, post_status=201)

    ensure_trust_owners(client=None, headers={}, trusts=TRUSTS)  # type: ignore[arg-type]

    assert len(rec.posts) == len(TRUSTS)


def test_an_unexpected_status_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything else is a real failure and stops the run rather than papering over it."""
    _stub(monkeypatch, post_status=500)

    with pytest.raises(SmokeFailure, match="bootstrapping a Trust Owner for GSTT failed with HTTP 500"):
        ensure_trust_owners(client=None, headers={}, trusts=[TRUSTS[0]])  # type: ignore[arg-type]


def test_trust_without_a_code_is_named_by_its_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure has to identify the trust even when the hub returns no code."""
    _stub(monkeypatch, post_status=500)
    trust = [{"id": "id-3", "name": "No Code Trust", "code": None}]

    with pytest.raises(SmokeFailure, match="Trust Owner for No Code Trust failed"):
        ensure_trust_owners(client=None, headers={}, trusts=trust)  # type: ignore[arg-type]
