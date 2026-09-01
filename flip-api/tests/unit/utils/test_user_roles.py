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

"""Unit tests for ``apply_user_profile`` (``flip_api.utils.user_roles``).

The module's other three helpers (``get_user_role_data``, ``get_all_roles``,
``validate_roles``) have no direct unit tests — a gap inherited from before the split,
where they were only exercised via mocked patches in ``tests/unit/user_services/``.
"""

from unittest.mock import Mock
from uuid import uuid4

from flip_api.db.models.user_models import Role, UserProfile
from flip_api.domain.schemas.users import CognitoUser
from flip_api.utils.paging_utils import PagingInfo
from flip_api.utils.user_roles import apply_user_profile, get_all_roles, get_user_role_data


class TestApplyUserProfile:
    """`apply_user_profile` merges DB profile fields onto a Cognito user."""

    def _make_user(self):
        return CognitoUser(
            id=uuid4(),
            email="alice@example.com",
            is_disabled=False,
        )  # type: ignore[call-arg]

    def test_returns_user_unchanged_when_session_has_no_get(self):
        """Duck-typed guard: list-listing call-sites pass `None` for `session`
        when they don't have one. Don't blow up — just return the input.
        """
        user = self._make_user()

        result = apply_user_profile(user, session=None)  # type: ignore[arg-type]

        assert result is user

    def test_returns_user_unchanged_when_no_profile_row_exists(self):
        """No UserProfile row → no fields to apply; return the input verbatim."""
        user = self._make_user()
        session = Mock()
        session.get.return_value = None

        result = apply_user_profile(user, session=session)

        # session.get was probed with the user id under UserProfile.
        assert session.get.call_count == 1
        assert result is user

    def test_merges_profile_fields_when_available(self):
        """A matching UserProfile row supplies the name + organisation that
        Cognito doesn't carry. is_disabled is preserved verbatim.
        """
        user = self._make_user()
        profile = Mock(name="…", organisation="London AI Centre")
        # Mock(name=…) on a stock Mock is reserved — use side-effect attrs.
        profile.name = "Alice Example"
        profile.organisation = "London AI Centre"
        session = Mock()
        session.get.return_value = profile

        result = apply_user_profile(user, session=session)

        assert result.id == user.id
        assert result.email == user.email
        assert result.is_disabled is False
        assert result.name == "Alice Example"
        assert result.organisation == "London AI Centre"


def _exec_result(rows):
    """Stand in for the object `session.exec()` returns (only `.all()` is used)."""
    result = Mock()
    result.all.return_value = rows
    return result


def _session(role_rows, profile_rows):
    """`get_user_role_data` calls `session.exec` twice: roles first, then profiles."""
    session = Mock()
    session.exec.side_effect = [_exec_result(role_rows), _exec_result(profile_rows)]
    return session


def _paging(offset=0, page_size=10, search_str=""):
    return PagingInfo(offset=offset, page_number=1, page_size=page_size, search_str=search_str)


def _user(email, user_id=None):
    return CognitoUser(id=user_id or uuid4(), email=email, is_disabled=False)  # type: ignore[call-arg]


class TestGetUserRoleData:
    """`get_user_role_data` joins Cognito users to their DB roles and profiles.

    This is the only path that attaches roles to a user in the admin `GET /users`
    listing, so a regression here renders every user with zero roles.
    """

    def test_attaches_roles_to_each_user(self):
        """Roles are grouped per user; a user with no role rows gets an empty list."""
        alice, bob = _user("alice@example.com"), _user("bob@example.com")
        admin = Role(name="admin", description="Administrator")
        researcher = Role(name="researcher", description="Researcher")
        session = _session(
            role_rows=[(alice.id, admin), (alice.id, researcher)],
            profile_rows=[],
        )

        result = get_user_role_data(_paging(), [alice, bob], session)

        by_email = {u.email: u for u in result}
        assert [(r.rolename, r.roledescription) for r in by_email["alice@example.com"].roles] == [
            ("admin", "Administrator"),
            ("researcher", "Researcher"),
        ]
        assert [r.id for r in by_email["alice@example.com"].roles] == [admin.id, researcher.id]
        # Bob has no role rows — he must still appear, with no roles.
        assert by_email["bob@example.com"].roles == []

    def test_skips_role_rows_with_no_usable_role(self):
        """The `if role and role.id is not None` guard drops unjoinable rows."""
        alice = _user("alice@example.com")
        orphaned = Role(name="orphan", description="No id")
        orphaned.id = None  # type: ignore[assignment]
        session = _session(role_rows=[(alice.id, None), (alice.id, orphaned)], profile_rows=[])

        result = get_user_role_data(_paging(), [alice], session)

        assert result[0].roles == []

    def test_merges_profile_fields_and_defaults_when_absent(self):
        """Profile rows supply name/organisation; users without one get empty strings."""
        alice, bob = _user("alice@example.com"), _user("bob@example.com")
        session = _session(
            role_rows=[],
            profile_rows=[UserProfile(user_id=alice.id, name="Alice Example", organisation="London AI Centre")],
        )

        result = get_user_role_data(_paging(), [alice, bob], session)

        by_email = {u.email: u for u in result}
        assert (by_email["alice@example.com"].name, by_email["alice@example.com"].organisation) == (
            "Alice Example",
            "London AI Centre",
        )
        assert (by_email["bob@example.com"].name, by_email["bob@example.com"].organisation) == ("", "")

    def test_search_matches_email_name_or_organisation_case_insensitively(self):
        """Any of the three fields may satisfy the search; non-matches are dropped."""
        by_email_match = _user("kingsteam@example.com")
        by_name = _user("b@example.com")
        by_org = _user("c@example.com")
        excluded = _user("d@example.com")
        session = _session(
            role_rows=[],
            profile_rows=[
                UserProfile(user_id=by_name.id, name="Kings Person", organisation="Elsewhere"),
                UserProfile(user_id=by_org.id, name="Someone", organisation="KINGS College"),
                UserProfile(user_id=excluded.id, name="Nobody", organisation="Other"),
            ],
        )

        result = get_user_role_data(
            _paging(search_str="KiNgS"), [by_email_match, by_name, by_org, excluded], session
        )

        assert {u.email for u in result} == {"kingsteam@example.com", "b@example.com", "c@example.com"}

    def test_applies_offset_and_page_size_after_sorting_by_email(self):
        """Paging slices the email-sorted list, not the caller's order."""
        users = [_user(e) for e in ("d@example.com", "b@example.com", "a@example.com", "c@example.com")]
        session = _session(role_rows=[], profile_rows=[])

        result = get_user_role_data(_paging(offset=1, page_size=2), users, session)

        assert [u.email for u in result] == ["b@example.com", "c@example.com"]


class TestGetAllRoles:
    """`get_all_roles` returns the role IDs used to validate a user's requested roles."""

    def test_returns_role_ids_from_the_database(self):
        role_ids = [uuid4(), uuid4()]
        db = Mock()
        db.exec.return_value = _exec_result(role_ids)

        assert get_all_roles(db) == role_ids

    def test_returns_empty_list_when_no_roles_exist(self):
        db = Mock()
        db.exec.return_value = _exec_result([])

        assert get_all_roles(db) == []
