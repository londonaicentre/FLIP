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


@patch("flip_api.db.seed.trusts.SEED_NAME_OVERRIDES", {})
@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_creates_new_trusts(mock_get_settings, mock_session):
    """Test seeding trusts creates new trust records with derived code and region."""
    trust_names = ["Trust_1", "Trust_2"]
    mock_get_settings.return_value = SimpleNamespace(
        ENV="development", TRUST_NAMES=trust_names, TRUST_API_KEY_HASHES={}
    )

    trust_a = MagicMock(spec=Trust)
    trust_a.name = "Trust_1"
    trust_b = MagicMock(spec=Trust)
    trust_b.name = "Trust_2"
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
    by_name = {t.name: t for t in added_trusts}
    assert by_name["Trust_1"].code == "T1"
    assert by_name["Trust_2"].code == "T2"
    assert all(t.region == "London" for t in added_trusts)

    assert result == [
        {"name": "Trust_1"},
        {"name": "Trust_2"},
    ]


@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_passes_through_non_devstyle_names(mock_get_settings, mock_session):
    """Real NHS-style names (e.g. 'GSTT') are kept as-is for the display code."""
    mock_get_settings.return_value = SimpleNamespace(
        ENV="development", TRUST_NAMES=["GSTT"], TRUST_API_KEY_HASHES={}
    )

    final = MagicMock(spec=Trust)
    final.name = "GSTT"
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(all=MagicMock(return_value=[final])),
    ]

    seed_trusts(mock_session)

    added = mock_session.add.call_args.args[0]
    assert added.code == "GSTT"
    assert added.region == "London"


@patch("flip_api.db.seed.trusts.SEED_NAME_OVERRIDES", {})
@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_backfills_missing_code_and_region(mock_get_settings, mock_session):
    """Pre-existing rows without code/region get backfilled in place."""
    mock_get_settings.return_value = SimpleNamespace(
        ENV="development", TRUST_NAMES=["Trust_1"], TRUST_API_KEY_HASHES={}
    )

    existing = MagicMock(spec=Trust)
    existing.name = "Trust_1"
    existing.code = None
    existing.region = None
    existing.api_key_hash = "preserved"

    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing)),
        MagicMock(all=MagicMock(return_value=[existing])),
    ]

    seed_trusts(mock_session)

    mock_session.add.assert_not_called()
    assert existing.code == "T1"
    assert existing.region == "London"
    # Hash is not in env, and existing already had one — left untouched.
    assert existing.api_key_hash == "preserved"


@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_backfills_api_key_hash_from_env(mock_get_settings, mock_session):
    """Pre-existing rows with null hash get the env hash copied in (the migration path)."""
    env_hash = "abc123"
    mock_get_settings.return_value = SimpleNamespace(
        ENV="development",
        TRUST_NAMES=["Trust_1"],
        TRUST_API_KEY_HASHES={"Trust_1": env_hash},
    )

    existing = MagicMock(spec=Trust)
    existing.name = "Trust_1"
    existing.code = "T1"
    existing.region = "London"
    existing.api_key_hash = None

    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing)),
        MagicMock(all=MagicMock(return_value=[existing])),
    ]

    seed_trusts(mock_session)

    assert existing.api_key_hash == env_hash


@patch(
    "flip_api.db.seed.trusts.SEED_NAME_OVERRIDES",
    {"Trust_1": ("(Mock) Friendly Name", "FRIEND")},
)
@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_applies_name_override(mock_get_settings, mock_session):
    """A seed entry with an override gets the friendly name + code persisted, not the env key."""
    mock_get_settings.return_value = SimpleNamespace(
        ENV="development",
        TRUST_NAMES=["Trust_1"],
        TRUST_API_KEY_HASHES={"Trust_1": "envhash"},
    )
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(all=MagicMock(return_value=[])),
    ]

    seed_trusts(mock_session)

    added = mock_session.add.call_args.args[0]
    assert added.name == "(Mock) Friendly Name"
    assert added.code == "FRIEND"
    assert added.region == "London"
    assert added.api_key_hash == "envhash"


@patch("flip_api.db.seed.trusts.SEED_NAME_OVERRIDES", {})
@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_inserts_with_env_hash_when_available(mock_get_settings, mock_session):
    """New trusts are seeded with their env hash so they can authenticate immediately."""
    env_hash = "newrowhash"
    mock_get_settings.return_value = SimpleNamespace(
        ENV="development",
        TRUST_NAMES=["Trust_1"],
        TRUST_API_KEY_HASHES={"Trust_1": env_hash},
    )

    final = MagicMock(spec=Trust)
    final.name = "Trust_1"
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(all=MagicMock(return_value=[final])),
    ]

    seed_trusts(mock_session)

    added = mock_session.add.call_args.args[0]
    assert added.api_key_hash == env_hash


@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_skips_existing(mock_get_settings, mock_session):
    """Test that seeding does not duplicate existing trusts."""
    mock_get_settings.return_value = SimpleNamespace(
        ENV="development", TRUST_NAMES=["Trust Existing"], TRUST_API_KEY_HASHES={}
    )

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
        ENV="development", TRUST_NAMES=["Broken Trust"], TRUST_API_KEY_HASHES={}
    )

    mock_session.exec.side_effect = Exception("lookup failed")

    with pytest.raises(Exception, match="lookup failed"):
        seed_trusts(mock_session)

    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()
