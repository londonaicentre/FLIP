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

from unittest.mock import patch

import pytest
from fastapi import HTTPException


def test_get_xnat_auth_headers():
    with patch("imaging_api.utils.auth.xnat_token_factory") as mock_factory:
        mock_factory.get_xnat_cookie.return_value = "fake-session-token"

        from imaging_api.utils.auth import get_xnat_auth_headers

        headers = get_xnat_auth_headers()

        assert headers == {
            "Cookie": "JSESSIONID=fake-session-token",
            "Content-Type": "application/json",
        }
        mock_factory.get_xnat_cookie.assert_called_once()


@pytest.fixture
def data_account(monkeypatch):
    from imaging_api.config import get_settings
    from imaging_api.services import data_account

    monkeypatch.setattr(get_settings(), "XNAT_DATA_USER", "flip_data")
    monkeypatch.setattr(get_settings(), "XNAT_DATA_PASSWORD", "pw")
    monkeypatch.setattr(data_account, "_state", data_account.DataAccountState.PENDING)
    return data_account


def test_the_data_headers_log_in_as_the_data_account(data_account):
    from imaging_api.utils import auth

    with (
        patch.object(auth, "xnat_data_token_factory") as data_factory,
        patch.object(auth, "xnat_token_factory") as admin_factory,
    ):
        data_factory.get_xnat_cookie.return_value = "data-token"
        assert auth.get_xnat_data_auth_headers() == {
            "Cookie": "JSESSIONID=data-token",
            "Content-Type": "application/json",
        }
        admin_factory.get_xnat_cookie.assert_not_called()


def test_the_two_factories_are_two_identities():
    from imaging_api.config import get_settings
    from imaging_api.utils import auth

    assert auth.xnat_token_factory.username == get_settings().XNAT_SERVICE_USER
    assert auth.xnat_data_token_factory.username == get_settings().XNAT_DATA_USER
    assert auth.xnat_token_factory is not auth.xnat_data_token_factory


def test_an_unconfigured_data_account_is_a_503_naming_it(data_account, monkeypatch):
    from imaging_api.config import get_settings
    from imaging_api.utils import auth

    monkeypatch.setattr(get_settings(), "XNAT_DATA_PASSWORD", "")
    with patch.object(auth, "xnat_data_token_factory") as data_factory, pytest.raises(HTTPException) as caught:
        auth.get_xnat_data_auth_headers()
    assert caught.value.status_code == 503
    assert "'flip_data'" in caught.value.detail
    assert "XNAT_DATA_PASSWORD" in caught.value.detail
    data_factory.get_xnat_cookie.assert_not_called()


@pytest.mark.parametrize("refused", [401, 403])
def test_a_refused_data_account_is_a_503_naming_it_not_xnats_401(data_account, refused):
    from imaging_api.utils import auth

    login = HTTPException(status_code=refused, detail="Login attempt failed")
    with (
        patch.object(auth.xnat_data_token_factory, "get_xnat_cookie", side_effect=login),
        pytest.raises(HTTPException) as caught,
    ):
        auth.get_xnat_data_auth_headers()
    assert caught.value.status_code == 503
    assert "XNAT refused the data account 'flip_data'" in caught.value.detail
    assert "xnat-init" in caught.value.detail
    assert data_account.state() == data_account.DataAccountState.MISSING


def test_an_xnat_that_is_down_is_not_reported_as_a_missing_account(data_account):
    from imaging_api.utils import auth

    down = HTTPException(status_code=504, detail="no response")
    with (
        patch.object(auth.xnat_data_token_factory, "get_xnat_cookie", side_effect=down),
        pytest.raises(HTTPException) as caught,
    ):
        auth.get_xnat_data_auth_headers()
    assert caught.value.status_code == 504
    assert data_account.state() == data_account.DataAccountState.PENDING
