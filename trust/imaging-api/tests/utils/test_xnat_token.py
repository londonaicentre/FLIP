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

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi import HTTPException

from imaging_api.utils.xnat_token import LOGIN_TIMEOUT_SECONDS, SESSION_CHECK_TIMEOUT_SECONDS, XnatTokenFactory

URL = "http://localhost:8080"
USERNAME = "admin"
PASSWORD = "secret"  # pragma: allowlist secret

SESSION_CHECK_URL = f"{URL}/xapi/users/username"
LOGIN_URL = f"{URL}/data/JSESSION"
# The list endpoint the session check used to call: every XNAT user, on every request.
USER_LIST_URL = f"{URL}/xapi/users"

CACHED_TOKEN = "8F3A1C9E7B2D4F60A5E1C3B7D9F2B4E6"  # pragma: allowlist secret
NEW_TOKEN = "C4E2A9F1B7D3E5A0C8F6B2D4E1A3C5F7"  # pragma: allowlist secret


def _response(status_code: int, text: str = "") -> MagicMock:
    return MagicMock(status_code=status_code, text=text)


# Each way a cached session can stop being usable: XNAT names someone else, refuses it, or cannot
# be reached. Every one must lead to exactly one fresh login.
INVALID_SESSION_CHECKS = [
    pytest.param(_response(200, "another-user"), id="another-user"),
    pytest.param(_response(401, "Unauthorized"), id="401"),
    pytest.param(requests.exceptions.ConnectionError("connection refused"), id="connection-error"),
]


@pytest.fixture
def factory():
    return XnatTokenFactory(url=URL, username=USERNAME, password=PASSWORD)


def _called_urls(mock_get: MagicMock) -> list[str]:
    return [call.args[0] for call in mock_get.call_args_list]


class TestIsTokenValid:
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_valid_session_for_the_service_user(self, mock_get, factory):
        mock_get.return_value = _response(200, USERNAME)

        assert factory.is_token_valid("valid-token") is True
        mock_get.assert_called_once_with(
            SESSION_CHECK_URL,
            headers={"Cookie": "JSESSIONID=valid-token"},
            timeout=SESSION_CHECK_TIMEOUT_SECONDS,
        )

    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_surrounding_whitespace_in_the_answer_is_ignored(self, mock_get, factory):
        mock_get.return_value = _response(200, f"{USERNAME}\n")
        assert factory.is_token_valid("valid-token") is True

    @pytest.mark.parametrize(
        "answer",
        ["another-user", "guest", f"{USERNAME}2", USERNAME[:-1], USERNAME.upper(), "", "<html>login</html>"],
    )
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_a_session_naming_anyone_else_is_invalid(self, mock_get, factory, answer):
        mock_get.return_value = _response(200, answer)
        assert factory.is_token_valid("someone-elses-token") is False

    @pytest.mark.parametrize("status_code", [401, 403, 404, 500, 503])
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_non_200_is_invalid_even_if_the_body_names_the_service_user(self, mock_get, factory, status_code):
        mock_get.return_value = _response(status_code, USERNAME)
        assert factory.is_token_valid("expired-token") is False

    @pytest.mark.parametrize(
        "error",
        [requests.exceptions.ConnectionError("connection refused"), requests.exceptions.Timeout("timed out")],
    )
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_connection_error_is_invalid(self, mock_get, factory, error):
        mock_get.side_effect = error
        assert factory.is_token_valid("any-token") is False


class TestGetXnatCookie:
    @patch("imaging_api.utils.xnat_token.requests.post")
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_valid_session_is_reused_without_a_login(self, mock_get, mock_post, factory):
        factory.xnat_cookie = {"token": CACHED_TOKEN}
        mock_get.return_value = _response(200, USERNAME)

        assert factory.get_xnat_cookie() == CACHED_TOKEN
        mock_get.assert_called_once_with(
            SESSION_CHECK_URL,
            headers={"Cookie": f"JSESSIONID={CACHED_TOKEN}"},
            timeout=SESSION_CHECK_TIMEOUT_SECONDS,
        )
        mock_post.assert_not_called()

    @pytest.mark.parametrize("session_check", INVALID_SESSION_CHECKS)
    @patch("imaging_api.utils.xnat_token.requests.post")
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_unusable_session_triggers_exactly_one_login(self, mock_get, mock_post, factory, session_check):
        factory.xnat_cookie = {"token": CACHED_TOKEN}
        if isinstance(session_check, Exception):
            mock_get.side_effect = session_check
        else:
            mock_get.return_value = session_check
        mock_post.return_value = _response(200, NEW_TOKEN)

        assert factory.get_xnat_cookie() == NEW_TOKEN
        mock_get.assert_called_once()
        mock_post.assert_called_once_with(LOGIN_URL, auth=(USERNAME, PASSWORD), timeout=LOGIN_TIMEOUT_SECONDS)
        assert factory.xnat_cookie == {"token": NEW_TOKEN}

    @patch("imaging_api.utils.xnat_token.requests.post")
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_first_call_logs_in_and_later_calls_reuse_the_session(self, mock_get, mock_post, factory):
        # No cached session yet: XNAT refuses the empty cookie, so the factory logs in once.
        mock_get.side_effect = [_response(401), _response(200, USERNAME), _response(200, USERNAME)]
        mock_post.return_value = _response(200, NEW_TOKEN)

        assert [factory.get_xnat_cookie() for _ in range(3)] == [NEW_TOKEN] * 3

        mock_post.assert_called_once()
        assert _called_urls(mock_get) == [SESSION_CHECK_URL] * 3
        assert [call.kwargs["headers"] for call in mock_get.call_args_list[1:]] == [
            {"Cookie": f"JSESSIONID={NEW_TOKEN}"}
        ] * 2

    @patch("imaging_api.utils.xnat_token.requests.post")
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_the_user_list_is_never_requested(self, mock_get, mock_post, factory):
        mock_get.side_effect = [_response(401), _response(200, USERNAME), _response(200, "another-user")]
        mock_post.return_value = _response(200, NEW_TOKEN)

        for _ in range(3):
            factory.get_xnat_cookie()

        assert USER_LIST_URL not in _called_urls(mock_get)
        assert all(url == SESSION_CHECK_URL for url in _called_urls(mock_get))

    @patch("imaging_api.utils.xnat_token.requests.post")
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_login_timeout_raises_http_exception(self, mock_get, mock_post, factory):
        mock_get.return_value = _response(401)
        mock_post.side_effect = requests.exceptions.ReadTimeout("read timed out")

        with pytest.raises(HTTPException) as exc_info:
            factory.get_xnat_cookie()
        assert exc_info.value.status_code == 504
        assert exc_info.value.detail.startswith("Failed to authenticate with XNAT: ")
        assert mock_post.call_args.kwargs["timeout"] == LOGIN_TIMEOUT_SECONDS
        assert factory.xnat_cookie == {}

    @patch("imaging_api.utils.xnat_token.requests.post")
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_auth_failure_raises_http_exception(self, mock_get, mock_post, factory):
        mock_get.return_value = _response(401)
        mock_post.return_value = _response(403, "Forbidden")

        with pytest.raises(HTTPException) as exc_info:
            factory.get_xnat_cookie()
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Failed to authenticate with XNAT: Forbidden"

    @patch("imaging_api.utils.xnat_token.requests.post")
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_empty_response_raises_http_exception(self, mock_get, mock_post, factory):
        mock_get.return_value = _response(401)
        mock_post.return_value = _response(200, "   ")

        with pytest.raises(HTTPException) as exc_info:
            factory.get_xnat_cookie()
        assert exc_info.value.status_code == 401


class TestSessionTokenIsNeverLogged:
    @pytest.mark.parametrize(
        "session_check",
        [pytest.param(_response(200, USERNAME), id="valid"), *INVALID_SESSION_CHECKS],
    )
    @patch("imaging_api.utils.xnat_token.requests.post")
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_no_part_of_either_token_reaches_the_log(self, mock_get, mock_post, factory, session_check, caplog):
        caplog.set_level(logging.DEBUG)
        factory.xnat_cookie = {"token": CACHED_TOKEN}
        if isinstance(session_check, Exception):
            mock_get.side_effect = session_check
        else:
            mock_get.return_value = session_check
        mock_post.return_value = _response(200, NEW_TOKEN)

        factory.get_xnat_cookie()

        # The check does log on every path, so an empty log cannot pass this vacuously.
        assert "XNAT session check" in caplog.text
        for token in (CACHED_TOKEN, NEW_TOKEN):
            # The previous code logged the first ten characters; any six would already be a leak.
            assert not any(token[i : i + 6] in caplog.text for i in range(len(token) - 5))

    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_a_session_for_another_user_warns_without_naming_either_user(self, mock_get, factory, caplog):
        """A mismatch makes every request log in again, so it must be visible: one WARNING, no names, no token."""
        caplog.set_level(logging.DEBUG)
        mock_get.return_value = _response(200, "another-user")

        assert factory.is_token_valid(CACHED_TOKEN) is False

        warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "a different user than XNAT_SERVICE_USER" in warnings[0]
        assert USERNAME not in caplog.text
        assert "another-user" not in caplog.text
        assert not any(CACHED_TOKEN[i : i + 6] in caplog.text for i in range(len(CACHED_TOKEN) - 5))

    @pytest.mark.parametrize(
        "session_check",
        [
            pytest.param(_response(200, USERNAME), id="valid"),
            pytest.param(_response(401, "Unauthorized"), id="401"),
            pytest.param(requests.exceptions.ConnectionError("connection refused"), id="connection-error"),
        ],
    )
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_an_expired_or_unreachable_session_stays_at_debug(self, mock_get, factory, session_check, caplog):
        """An expired session or a blip is routine, so only the user mismatch warns."""
        caplog.set_level(logging.DEBUG)
        if isinstance(session_check, Exception):
            mock_get.side_effect = session_check
        else:
            mock_get.return_value = session_check

        factory.is_token_valid(CACHED_TOKEN)

        assert "XNAT session check" in caplog.text
        assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
