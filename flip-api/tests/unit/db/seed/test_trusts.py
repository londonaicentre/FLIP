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

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from flip_api.db.models.main_models import Trust
from flip_api.db.seed.trusts import seed_trusts


@pytest.fixture
def mock_session():
    """Create a mock database session."""
    return MagicMock(spec=Session)


@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_non_production_creates_new_trusts(mock_get_settings, mock_session):
    """Test seeding trusts in non-production mode using settings endpoints."""
    trust_endpoints = {
        "Trust A": "https://trust-a.example.com",
        "Trust B": "https://trust-b.example.com",
    }
    mock_get_settings.return_value = SimpleNamespace(ENV="development", TRUST_ENDPOINTS=trust_endpoints)

    trust_a = MagicMock(spec=Trust)
    trust_a.name = "Trust A"
    trust_a.endpoint = "https://trust-a.example.com"
    trust_b = MagicMock(spec=Trust)
    trust_b.name = "Trust B"
    trust_b.endpoint = "https://trust-b.example.com"
    final_trusts = [trust_a, trust_b]

    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(all=MagicMock(return_value=final_trusts)),
    ]

    result = seed_trusts(mock_session)

    mock_get_settings.assert_called_once()
    assert mock_session.add.call_count == 2
    assert mock_session.commit.call_count == 1

    added_trusts = [c.args[0] for c in mock_session.add.call_args_list]
    assert all(isinstance(t, Trust) for t in added_trusts)
    assert {t.name for t in added_trusts} == {"Trust A", "Trust B"}

    assert result == [
        {"name": "Trust A", "endpoint": "https://trust-a.example.com"},
        {"name": "Trust B", "endpoint": "https://trust-b.example.com"},
    ]


@patch("flip_api.db.seed.trusts.get_secret")
@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_production_uses_secret_and_updates_existing(
    mock_get_settings,
    mock_get_secret,
    mock_session,
):
    """Test production mode loads endpoints from secrets and updates existing trust."""
    mock_get_settings.return_value = SimpleNamespace(ENV="production", TRUST_ENDPOINTS={"unused": "unused"})
    mock_get_secret.return_value = {"Trust Existing": "https://new-endpoint.example.com"}

    existing_trust = MagicMock(spec=Trust)
    existing_trust.name = "Trust Existing"
    existing_trust.endpoint = "https://old-endpoint.example.com"

    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing_trust)),
        MagicMock(all=MagicMock(return_value=[existing_trust])),
    ]

    result = seed_trusts(mock_session)

    mock_get_secret.assert_called_once_with("trust_endpoints")
    mock_session.add.assert_not_called()
    assert existing_trust.endpoint == "https://new-endpoint.example.com"
    assert mock_session.commit.call_count == 1
    assert result == [{"name": "Trust Existing", "endpoint": "https://new-endpoint.example.com"}]


@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_raises_on_lookup_exception(mock_get_settings, mock_session):
    """Test that trust lookup errors are raised and stop seeding."""
    mock_get_settings.return_value = SimpleNamespace(
        ENV="development", TRUST_ENDPOINTS={"Broken Trust": "https://broken.example.com"}
    )

    mock_session.exec.side_effect = Exception("lookup failed")

    with pytest.raises(Exception, match="lookup failed"):
        seed_trusts(mock_session)

    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()
