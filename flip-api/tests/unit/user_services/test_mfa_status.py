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

import uuid
from unittest.mock import patch

import pytest
from fastapi import HTTPException, status

from flip_api.user_services.mfa_status import get_own_mfa_status

USERNAME = "user@example.com"


@pytest.fixture
def token_id():
    return uuid.uuid4()


def test_returns_enabled_true_when_totp_active(fake_idp, mock_request, token_id):
    """Caller with an active TOTP device gets enabled=True."""
    fake_idp.get_username.return_value = USERNAME
    fake_idp.is_mfa_enabled.return_value = True

    with patch("flip_api.user_services.mfa_status.get_settings") as mock_get_settings:
        mock_get_settings.return_value.ENFORCE_MFA = True

        result = get_own_mfa_status(mock_request, token_id, idp=fake_idp)

        assert result == {"enabled": True, "required": True}
        fake_idp.get_username.assert_called_once_with(token_id)
        fake_idp.is_mfa_enabled.assert_called_once_with(USERNAME)


def test_returns_enabled_false_when_totp_not_active(fake_idp, mock_request, token_id):
    """Post-reset or first-invite user sees enabled=False, which is the cue to enrol."""
    fake_idp.get_username.return_value = USERNAME
    fake_idp.is_mfa_enabled.return_value = False

    with patch("flip_api.user_services.mfa_status.get_settings") as mock_get_settings:
        mock_get_settings.return_value.ENFORCE_MFA = True

        result = get_own_mfa_status(mock_request, token_id, idp=fake_idp)

        assert result == {"enabled": False, "required": True}


def test_required_false_signals_dev_bypass_to_ui(fake_idp, mock_request, token_id):
    """ENFORCE_MFA=False flips the `required` flag so the UI knows it can
    skip the /auth/mfa-setup redirect — independent of whether the user
    happens to have TOTP already enabled."""
    fake_idp.get_username.return_value = USERNAME
    fake_idp.is_mfa_enabled.return_value = False

    with patch("flip_api.user_services.mfa_status.get_settings") as mock_get_settings:
        mock_get_settings.return_value.ENFORCE_MFA = False

        result = get_own_mfa_status(mock_request, token_id, idp=fake_idp)

        assert result == {"enabled": False, "required": False}


def test_raises_404_when_identity_user_missing(fake_idp, mock_request, token_id):
    """A valid JWT whose sub has no matching identity-provider user bubbles get_username's 404."""
    fake_idp.get_username.side_effect = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="User not registered"
    )

    with pytest.raises(HTTPException) as exc_info:
        get_own_mfa_status(mock_request, token_id, idp=fake_idp)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    fake_idp.is_mfa_enabled.assert_not_called()
