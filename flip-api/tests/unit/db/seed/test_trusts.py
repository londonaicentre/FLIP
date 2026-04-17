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
def test_seed_trusts_creates_new_trusts(mock_get_settings, mock_session):
    """Test seeding trusts creates new trust records by name."""
    trust_names = ["Trust A", "Trust B"]
    mock_get_settings.return_value = SimpleNamespace(ENV="development", TRUST_NAMES=trust_names)

    trust_a = MagicMock(spec=Trust)
    trust_a.name = "Trust A"
    trust_b = MagicMock(spec=Trust)
    trust_b.name = "Trust B"
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
        {"name": "Trust A"},
        {"name": "Trust B"},
    ]


@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_skips_existing(mock_get_settings, mock_session):
    """Test that seeding does not duplicate existing trusts."""
    mock_get_settings.return_value = SimpleNamespace(ENV="development", TRUST_NAMES=["Trust Existing"])

    existing_trust = MagicMock(spec=Trust)
    existing_trust.name = "Trust Existing"

    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing_trust)),
        MagicMock(all=MagicMock(return_value=[existing_trust])),
    ]

    result = seed_trusts(mock_session)

    mock_session.add.assert_not_called()
    assert mock_session.commit.call_count == 1
    assert result == [{"name": "Trust Existing"}]


@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_raises_on_lookup_exception(mock_get_settings, mock_session):
    """Test that trust lookup errors are raised and stop seeding."""
    mock_get_settings.return_value = SimpleNamespace(
        ENV="development", TRUST_NAMES=["Broken Trust"]
    )

    mock_session.exec.side_effect = Exception("lookup failed")

    with pytest.raises(Exception, match="lookup failed"):
        seed_trusts(mock_session)

    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()
