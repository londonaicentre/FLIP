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
"""The XNAT data account's memberships and state (FLIP#2011)."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from imaging_api.config import get_settings
from imaging_api.routers.schemas import Project
from imaging_api.services import data_account

FLIP_A = "6d3c7c9e-7a51-4b1a-9c2e-0d7f2f0f6a11"
FLIP_B = "0b7e8c43-2a54-4d39-a1a3-3f1f9b6a2c55"


@pytest.fixture(autouse=True)
def account(monkeypatch):
    monkeypatch.setattr(get_settings(), "XNAT_DATA_USER", "flip_data")
    monkeypatch.setattr(get_settings(), "XNAT_DATA_PASSWORD", "pw")
    monkeypatch.setattr(data_account, "_state", data_account.DataAccountState.PENDING)


def _project(project_id: str) -> Project:
    return Project(
        ID=project_id, secondary_ID="hub", name=project_id, pi_firstname="", pi_lastname="", URI=f"/p/{project_id}"
    )


def _response(status: int, text: str = "") -> MagicMock:
    return MagicMock(status_code=status, text=text)


def _members(*logins: str) -> list[dict[str, str]]:
    return [{"login": "admin", "displayname": "Owners"}] + [{"login": x, "displayname": "Members"} for x in logins]


def _answer(status: int, rows: list | None = None, text: str = "") -> MagicMock:
    response = _response(status, text)
    response.json.return_value = {"ResultSet": {"Result": rows or []}}
    return response


class TestGrant:
    def test_it_adds_the_data_account_as_a_member_on_the_callers_session(self):
        with patch.object(data_account.requests, "put", return_value=_answer(200, _members("flip_data"))) as put:
            data_account.grant_data_access(FLIP_A, {"Cookie": "JSESSIONID=admin"})
        url = put.call_args.args[0]
        assert url.endswith(f"/data/projects/{FLIP_A}/users/Members/flip_data")
        assert put.call_args.kwargs["headers"] == {"Cookie": "JSESSIONID=admin"}

    def test_a_200_that_does_not_list_the_account_is_a_missing_account(self):
        """XNAT 1.10.2.1 answers 200 to adding a user it does not have, adds nobody, and returns the member list."""
        with (
            patch.object(data_account.requests, "put", return_value=_answer(200, _members())),
            pytest.raises(data_account.DataAccountMissingError),
        ):
            data_account.grant_data_access(FLIP_A, {})

    def test_the_account_listed_under_another_role_is_not_a_member(self):
        rows = [{"login": "flip_data", "displayname": "Owners"}]
        with (
            patch.object(data_account.requests, "put", return_value=_answer(200, rows)),
            pytest.raises(data_account.DataAccountMissingError),
        ):
            data_account.grant_data_access(FLIP_A, {})

    def test_a_404_is_a_missing_account(self):
        with (
            patch.object(data_account.requests, "put", return_value=_answer(404)),
            pytest.raises(data_account.DataAccountMissingError),
        ):
            data_account.grant_data_access(FLIP_A, {})

    def test_an_answer_that_is_not_a_member_list_is_an_error_not_a_success(self):
        response = _response(200, "<html>")
        response.json.side_effect = ValueError("not json")
        with (
            patch.object(data_account.requests, "put", return_value=response),
            pytest.raises(Exception, match="not a member list"),
        ):
            data_account.grant_data_access(FLIP_A, {})

    def test_any_other_refusal_is_an_error_with_the_status(self):
        with (
            patch.object(data_account.requests, "put", return_value=_response(500, "boom")),
            pytest.raises(Exception, match="500 - boom"),
        ):
            data_account.grant_data_access(FLIP_A, {})


class TestBackfill:
    def test_it_covers_flip_projects_and_leaves_hand_made_ones_alone(self):
        projects = [_project(FLIP_A), _project("ADMIN_SANDBOX"), _project(FLIP_B)]
        with (
            patch.object(data_account, "get_all_projects", return_value=projects),
            patch.object(data_account, "grant_data_access") as grant,
        ):
            assert data_account.backfill({"h": "1"}) == 2
        assert [c.args[0] for c in grant.call_args_list] == [FLIP_A, FLIP_B]

    def test_a_pass_that_succeeds_marks_it_ready_and_stops(self):
        with (
            patch("imaging_api.utils.auth.get_xnat_auth_headers", return_value={}),
            patch.object(data_account, "backfill", return_value=3),
            patch("imaging_api.utils.auth.xnat_data_token_factory") as data_login,
        ):
            asyncio.run(data_account.run_backfill())
        data_login.get_xnat_cookie.assert_called_once()
        assert data_account.state() == data_account.DataAccountState.READY

    def test_an_unconfigured_account_is_reported_and_xnat_is_not_called(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "XNAT_DATA_PASSWORD", "")
        with patch.object(data_account, "backfill") as backfill:
            asyncio.run(data_account.run_backfill())
        backfill.assert_not_called()
        assert data_account.state() == data_account.DataAccountState.NOT_CONFIGURED

    @pytest.mark.parametrize(
        ("failure", "where", "expected"),
        [
            (data_account.DataAccountMissingError("no user"), "backfill", data_account.DataAccountState.MISSING),
            (HTTPException(status_code=401), "login", data_account.DataAccountState.MISSING),
            (HTTPException(status_code=504), "login", data_account.DataAccountState.PENDING),
            (ConnectionError("XNAT not up yet"), "backfill", data_account.DataAccountState.PENDING),
        ],
    )
    def test_a_failed_pass_sets_the_state_and_tries_again(self, failure, where, expected):
        """Missing and refused are reported as such; XNAT being down is not mistaken for a missing account."""

        async def stop(_seconds):
            raise asyncio.CancelledError

        data_login = MagicMock(get_xnat_cookie=MagicMock(side_effect=failure if where == "login" else None))
        with (
            patch("imaging_api.utils.auth.get_xnat_auth_headers", return_value={}),
            patch.object(data_account, "backfill", side_effect=failure if where == "backfill" else None),
            patch("imaging_api.utils.auth.xnat_data_token_factory", data_login),
            patch.object(data_account.asyncio, "sleep", side_effect=stop) as sleep,
            pytest.raises(asyncio.CancelledError),
        ):
            asyncio.run(data_account.run_backfill())
        sleep.assert_called_once_with(data_account.BACKFILL_RETRY_SECONDS)
        assert data_account.state() == expected


class TestRemedy:
    def test_an_unconfigured_account_points_at_the_kit(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "XNAT_DATA_PASSWORD", "")
        text = data_account.remedy()
        assert "'flip_data'" in text
        assert "XNAT_DATA_PASSWORD" in text
        assert "upgrade command" in text
        assert "generate-xnat-credentials" in text

    def test_a_refused_account_points_at_xnat_init(self):
        text = data_account.remedy()
        assert "XNAT refused the data account 'flip_data'" in text
        assert "xnat-init" in text
