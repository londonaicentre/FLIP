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

"""Unit tests for the DB-backed user profile/role helpers (`flip_api.utils.user_roles`)."""

from unittest.mock import Mock
from uuid import uuid4

from flip_api.domain.schemas.users import CognitoUser
from flip_api.utils.user_roles import apply_user_profile


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
