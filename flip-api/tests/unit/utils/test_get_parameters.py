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

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from flip_api.utils.get_parameters import get_parameter


@pytest.fixture
def mock_settings():
    """Mock settings for AWS region."""
    with patch("flip_api.utils.get_parameters.get_settings") as mock_get_settings:
        mock_get_settings.return_value.AWS_REGION = "test-west-1"
        yield mock_get_settings


@pytest.fixture
def mock_boto3_session():
    """Fixture to mock boto3 session and client."""
    with patch("boto3.session.Session") as mock_session:
        mock_client = MagicMock()
        mock_session.return_value.client.return_value = mock_client
        yield mock_session, mock_client


def test_get_parameter_returns_value(mock_settings, mock_boto3_session):
    mock_session, mock_client = mock_boto3_session
    mock_client.get_parameter.return_value = {"Parameter": {"Value": '["Trust_1"]'}}

    assert get_parameter("/flip/fl_kit_slot_names") == '["Trust_1"]'

    mock_session.return_value.client.assert_called_once_with(service_name="ssm", region_name="test-west-1")
    mock_client.get_parameter.assert_called_once_with(Name="/flip/fl_kit_slot_names")


def test_get_parameter_uses_explicit_region_over_settings(mock_settings, mock_boto3_session):
    mock_session, mock_client = mock_boto3_session
    mock_client.get_parameter.return_value = {"Parameter": {"Value": "x"}}

    get_parameter("/flip/fl_kit_slot_names", region_name="eu-central-1")

    mock_session.return_value.client.assert_called_once_with(service_name="ssm", region_name="eu-central-1")


def test_get_parameter_propagates_client_error(mock_settings, mock_boto3_session):
    """Callers own the error taxonomy (e.g. ParameterNotFound vs everything else)."""
    _, mock_client = mock_boto3_session
    mock_client.get_parameter.side_effect = ClientError({"Error": {"Code": "ParameterNotFound"}}, "GetParameter")

    with pytest.raises(ClientError):
        get_parameter("/flip/fl_kit_slot_names")
