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


def _settings(trust_names, display_names=None):
    """Minimal settings stub for seed_trusts (TRUST_NAMES + TRUST_DISPLAY_NAMES)."""
    return SimpleNamespace(TRUST_NAMES=trust_names, TRUST_DISPLAY_NAMES=display_names or {})


@patch("flip_api.db.seed.trusts._bootstrap_trust_hashes", return_value={})
@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_creates_new_trusts(mock_get_settings, mock_hashes, mock_session):
    """Env-slot names with no display override are seeded under their slot name."""
    mock_get_settings.return_value = _settings(["Trust_1", "Trust_2"])

    trust_a = MagicMock(spec=Trust)
    trust_a.name = "Trust_1"
    trust_b = MagicMock(spec=Trust)
    trust_b.name = "Trust_2"
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(all=MagicMock(return_value=[trust_a, trust_b])),
    ]

    result = seed_trusts(mock_session)

    mock_get_settings.assert_called_once()
    assert mock_session.add.call_count == 2
    assert mock_session.commit.call_count == 1

    added = {c.args[0].name: c.args[0] for c in mock_session.add.call_args_list}
    assert set(added) == {"Trust_1", "Trust_2"}
    assert all(isinstance(t, Trust) for t in added.values())
    assert all(t.region == "London" for t in added.values())
    assert result == [{"name": "Trust_1"}, {"name": "Trust_2"}]


@patch("flip_api.db.seed.trusts._bootstrap_trust_hashes", return_value={})
@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_applies_display_name(mock_get_settings, mock_hashes, mock_session):
    """TRUST_DISPLAY_NAMES sets the persisted Trust.name; unlisted slots keep their slot name."""
    mock_get_settings.return_value = _settings(["Trust_1", "Trust_2"], {"Trust_1": "Open Trust (EC2)"})
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(all=MagicMock(return_value=[])),
    ]

    seed_trusts(mock_session)

    added = {c.args[0].name for c in mock_session.add.call_args_list}
    assert added == {"Open Trust (EC2)", "Trust_2"}


@patch("flip_api.db.seed.trusts._bootstrap_trust_hashes", return_value={"Trust_1": "slothash"})
@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_inserts_with_hash_keyed_by_env_slot(mock_get_settings, mock_hashes, mock_session):
    """A new row's api_key_hash comes from the bootstrap source, keyed by the env-slot name."""
    mock_get_settings.return_value = _settings(["Trust_1"], {"Trust_1": "Open Trust (EC2)"})

    final = MagicMock(spec=Trust)
    final.name = "Open Trust (EC2)"
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(all=MagicMock(return_value=[final])),
    ]

    seed_trusts(mock_session)

    added = mock_session.add.call_args.args[0]
    assert added.name == "Open Trust (EC2)"
    assert added.api_key_hash == "slothash"


@patch("flip_api.db.seed.trusts._bootstrap_trust_hashes", return_value={"Trust_1": "abc123"})
@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_backfills_api_key_hash(mock_get_settings, mock_hashes, mock_session):
    """A pre-existing row with a null hash gets the bootstrap hash backfilled."""
    mock_get_settings.return_value = _settings(["Trust_1"], {"Trust_1": "Open Trust (EC2)"})

    existing = MagicMock(spec=Trust)
    existing.name = "Open Trust (EC2)"
    existing.region = "London"
    existing.api_key_hash = None
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing)),
        MagicMock(all=MagicMock(return_value=[existing])),
    ]

    seed_trusts(mock_session)

    mock_session.add.assert_not_called()
    assert existing.api_key_hash == "abc123"


@patch("flip_api.db.seed.trusts._bootstrap_trust_hashes", return_value={})
@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_backfills_region_and_preserves_hash(mock_get_settings, mock_hashes, mock_session):
    """A pre-existing row gets region backfilled; an already-set hash is left untouched."""
    mock_get_settings.return_value = _settings(["Trust_1"])

    existing = MagicMock(spec=Trust)
    existing.name = "Trust_1"
    existing.region = None
    existing.api_key_hash = "preserved"
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing)),
        MagicMock(all=MagicMock(return_value=[existing])),
    ]

    seed_trusts(mock_session)

    mock_session.add.assert_not_called()
    assert existing.region == "London"
    assert existing.api_key_hash == "preserved"


@patch("flip_api.db.seed.trusts._bootstrap_trust_hashes", return_value={})
@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_skips_existing(mock_get_settings, mock_hashes, mock_session):
    """Seeding does not duplicate an existing trust."""
    mock_get_settings.return_value = _settings(["Trust Existing"])

    existing_trust = MagicMock(spec=Trust)
    existing_trust.name = "Trust Existing"
    existing_trust.region = "London"
    existing_trust.api_key_hash = "h"
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing_trust)),
        MagicMock(all=MagicMock(return_value=[existing_trust])),
    ]

    result = seed_trusts(mock_session)

    mock_session.add.assert_not_called()
    assert mock_session.commit.call_count == 1
    assert result == [{"name": "Trust Existing"}]


@patch("flip_api.db.seed.trusts._bootstrap_trust_hashes", return_value={})
@patch("flip_api.db.seed.trusts.get_settings")
def test_seed_trusts_raises_on_lookup_exception(mock_get_settings, mock_hashes, mock_session):
    """Trust lookup errors are raised and stop seeding."""
    mock_get_settings.return_value = _settings(["Broken Trust"])
    mock_session.exec.side_effect = Exception("lookup failed")

    with pytest.raises(Exception, match="lookup failed"):
        seed_trusts(mock_session)

    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()
