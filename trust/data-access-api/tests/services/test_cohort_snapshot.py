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

"""Unit tests for the approved-cohort snapshot file store (FLIP#857)."""

import json
import uuid
from unittest.mock import patch

import pandas as pd
import pytest

from data_access_api.services import cohort_snapshot
from data_access_api.services.cohort_snapshot import (
    SnapshotStoreDisabled,
    SnapshotTooLarge,
    delete_snapshot,
    ensure_store,
    get_snapshot,
    normalised_query_hash,
    save_snapshot,
    snapshot_enabled,
)

PROJECT_ID = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"
QUERY_HASH = normalised_query_hash("SELECT * FROM omop.person")


@pytest.fixture
def store(tmp_path):
    """A configured, writable snapshot store rooted in a per-test temp directory."""
    with patch("data_access_api.services.cohort_snapshot.get_settings") as mock_settings:
        mock_settings.return_value.COHORT_SNAPSHOT_DIR = str(tmp_path)
        mock_settings.return_value.SNAPSHOT_MAX_BYTES = 536_870_912
        yield tmp_path


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "accession_id": ["A1", "A2", "A3"],
            "age": pd.array([40, None, 61], dtype="Int64"),
            "measured_at": pd.to_datetime(["2021-01-01", "2022-06-30", "2023-12-31"]),
        }
    )


def test_save_then_get_round_trips_the_frame_dtype_faithfully(store):
    df = _sample_df()
    meta = save_snapshot(PROJECT_ID, df, query_hash=QUERY_HASH)

    snapshot = get_snapshot(PROJECT_ID)
    assert snapshot is not None
    pd.testing.assert_frame_equal(snapshot.df, df)
    assert snapshot.meta.row_count == 3
    assert snapshot.meta.columns == ["accession_id", "age", "measured_at"]
    assert snapshot.meta.query_hash == QUERY_HASH
    assert snapshot.meta.has_accessions is True
    assert meta.row_count == 3


def test_save_overwrites_atomically_on_reapproval(store):
    save_snapshot(PROJECT_ID, _sample_df(), query_hash=QUERY_HASH)
    replacement = pd.DataFrame({"person_id": [1, 2]})
    save_snapshot(PROJECT_ID, replacement, query_hash=normalised_query_hash("SELECT person_id FROM omop.person"))

    snapshot = get_snapshot(PROJECT_ID)
    assert snapshot is not None
    pd.testing.assert_frame_equal(snapshot.df, replacement)
    assert snapshot.meta.has_accessions is False
    # No write debris left behind after the swap.
    leftovers = [p.name for p in store.iterdir() if p.name.startswith((".tmp-", ".old-"))]
    assert leftovers == []


def test_get_returns_none_for_unknown_project_and_non_uuid_ids(store):
    assert get_snapshot(str(uuid.uuid4())) is None
    # A non-UUID id must never touch the filesystem — it is a path component.
    assert get_snapshot("../../etc/passwd") is None
    assert get_snapshot("my_project") is None


def test_save_rejects_non_uuid_project_id(store):
    with pytest.raises(ValueError, match="UUID"):
        save_snapshot("../escape", _sample_df(), query_hash=QUERY_HASH)
    assert list(store.iterdir()) == []


def test_save_refuses_oversized_snapshot_without_truncating(store):
    with patch("data_access_api.services.cohort_snapshot.get_settings") as mock_settings:
        mock_settings.return_value.COHORT_SNAPSHOT_DIR = str(store)
        mock_settings.return_value.SNAPSHOT_MAX_BYTES = 10
        with pytest.raises(SnapshotTooLarge, match="SNAPSHOT_MAX_BYTES"):
            save_snapshot(PROJECT_ID, _sample_df(), query_hash=QUERY_HASH)
    # Refused means nothing persisted — no partial artefact to poison training data.
    assert get_snapshot(PROJECT_ID) is None


def test_disabled_store_reads_none_and_refuses_writes():
    with patch("data_access_api.services.cohort_snapshot.get_settings") as mock_settings:
        mock_settings.return_value.COHORT_SNAPSHOT_DIR = ""
        assert snapshot_enabled() is False
        assert get_snapshot(PROJECT_ID) is None
        assert delete_snapshot(PROJECT_ID) is False
        with pytest.raises(SnapshotStoreDisabled):
            save_snapshot(PROJECT_ID, _sample_df(), query_hash=QUERY_HASH)


def test_corrupt_meta_is_treated_as_absent(store):
    save_snapshot(PROJECT_ID, _sample_df(), query_hash=QUERY_HASH)
    (store / PROJECT_ID / "meta.json").write_text("{not json")
    assert get_snapshot(PROJECT_ID) is None


def test_unknown_format_version_is_treated_as_absent(store):
    save_snapshot(PROJECT_ID, _sample_df(), query_hash=QUERY_HASH)
    meta_path = store / PROJECT_ID / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["format_version"] = 999
    meta_path.write_text(json.dumps(meta))
    assert get_snapshot(PROJECT_ID) is None


def test_delete_is_idempotent(store):
    save_snapshot(PROJECT_ID, _sample_df(), query_hash=QUERY_HASH)
    assert delete_snapshot(PROJECT_ID) is True
    assert get_snapshot(PROJECT_ID) is None
    assert delete_snapshot(PROJECT_ID) is False


def test_ensure_store_sweeps_stale_write_debris(store):
    save_snapshot(PROJECT_ID, _sample_df(), query_hash=QUERY_HASH)
    (store / ".tmp-crashed-write").mkdir()
    (store / ".old-crashed-swap").mkdir()

    ensure_store()

    survivors = sorted(p.name for p in store.iterdir())
    assert survivors == [PROJECT_ID]
    assert get_snapshot(PROJECT_ID) is not None


def test_ensure_store_survives_unwritable_directory(tmp_path):
    target = tmp_path / "readonly"
    target.mkdir()
    target.chmod(0o500)
    try:
        with patch("data_access_api.services.cohort_snapshot.get_settings") as mock_settings:
            mock_settings.return_value.COHORT_SNAPSHOT_DIR = str(target)
            # Must log and return, never raise: a broken store cannot take OMOP serving down.
            ensure_store()
    finally:
        target.chmod(0o700)


def test_normalised_query_hash_ignores_case_and_whitespace_only():
    base = normalised_query_hash("SELECT * FROM omop.person")
    assert normalised_query_hash("  select *\n  FROM   omop.person  ") == base
    assert normalised_query_hash("SELECT person_id FROM omop.person") != base


def test_hash_key_matches_module_constant_shape():
    # cohort_snapshot deliberately does not import query_cache: pin that its normalisation
    # stays self-contained and deterministic.
    assert len(cohort_snapshot.normalised_query_hash("x")) == 64
