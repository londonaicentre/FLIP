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

"""Route-level tests for approved-cohort snapshot serving and creation (FLIP#857)."""

from datetime import UTC, datetime
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from data_access_api.main import app
from data_access_api.routers.cohort import _BELOW_THRESHOLD_DETAIL, _NO_SNAPSHOT_DETAIL
from data_access_api.services.cohort_snapshot import (
    Snapshot,
    SnapshotMeta,
    SnapshotTooLarge,
    normalised_query_hash,
)
from tests.conftest import AUTH_HEADERS, WRITE_AUTH_HEADERS

client = TestClient(app)

FROZEN_QUERY = "SELECT * FROM omop.person"

sample_dataframe_query = {
    "encrypted_project_id": "encrypted_my_project",
    "query": FROZEN_QUERY,
}


def _snapshot(df: pd.DataFrame, query: str = FROZEN_QUERY) -> Snapshot:
    return Snapshot(
        df=df,
        meta=SnapshotMeta(
            row_count=len(df),
            columns=[str(column) for column in df.columns],
            query_hash=normalised_query_hash(query),
            created_at=datetime.now(UTC).isoformat(),
        ),
    )


# ---------------------------------------------------------------------------
# Frozen serving on /cohort/dataframe
# ---------------------------------------------------------------------------


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_serves_frozen_snapshot_and_ignores_client_sql(
    mock_get_records, mock_validate_query, mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """With a snapshot present, even hostile SQL is never validated or executed."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    frozen = pd.DataFrame({"accession_id": ["A1", "A2", "A3"], "label": [0, 1, 0]})
    mock_get_snapshot.return_value = _snapshot(frozen)

    body = {**sample_dataframe_query, "query": "SELECT * FROM omop.person; DROP TABLE omop.person"}
    response = client.post("/cohort/dataframe", json=body, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == frozen.to_dict(orient="list")
    mock_validate_query.assert_not_called()
    mock_get_records.assert_not_called()


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
def test_dataframe_frozen_below_threshold_uses_the_fixed_refusal(
    mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """The frozen count is gated with the same fixed text as the live path — the threshold
    is read live, so an operator raising their floor bites already-approved projects."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_get_snapshot.return_value = _snapshot(pd.DataFrame({"accession_id": ["A1"]}))

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == _BELOW_THRESHOLD_DETAIL


@pytest.mark.parametrize("path", ["/cohort/dataframe", "/cohort/accession-ids"])
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.get_records")
def test_row_level_routes_refuse_projects_without_a_snapshot(
    mock_get_records, mock_validate_query, mock_get_snapshot, mock_decrypt, path
):
    """No snapshot ⇒ no row-level data, fail-closed: there is no live-SQL serving path."""
    mock_decrypt.return_value = "my_project"
    mock_get_snapshot.return_value = None

    response = client.post(path, json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == _NO_SNAPSHOT_DETAIL
    mock_validate_query.assert_not_called()
    mock_get_records.assert_not_called()


# ---------------------------------------------------------------------------
# Frozen serving on /cohort/accession-ids
# ---------------------------------------------------------------------------


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_accession_ids_serves_frozen_pointer_set(mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings):
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(pd.DataFrame({"accession_id": [101, 102], "label": [0, 1]}))

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"accession_ids": ["101", "102"]}
    mock_get_records.assert_not_called()


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
def test_accession_ids_tabular_snapshot_returns_empty_list_not_an_error(
    mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """A frozen cohort with no accession_id column is a tabular project: imaging no-ops."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(pd.DataFrame({"person_id": [1, 2, 3], "label": [0, 1, 0]}))

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"accession_ids": []}


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
def test_accession_ids_frozen_below_threshold_is_indistinguishable_from_zero(
    mock_get_snapshot, mock_decrypt, mock_get_settings
):
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10

    details = []
    for rows in (0, 9):
        frozen = pd.DataFrame({"accession_id": [f"A{i}" for i in range(rows)]})
        mock_get_snapshot.return_value = _snapshot(frozen)
        response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)
        assert response.status_code == 403
        details.append(response.json()["detail"])

    assert details[0] == details[1] == _BELOW_THRESHOLD_DETAIL


# ---------------------------------------------------------------------------
# POST /cohort/snapshot (creation) and /cohort/snapshot/delete
# ---------------------------------------------------------------------------


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_freezes_the_validated_query_result(
    mock_snapshot_enabled, mock_decrypt, mock_validate_query, mock_get_records, mock_save_snapshot, mock_get_settings
):
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    frozen = pd.DataFrame({"accession_id": ["A1", "A2", "A3"]})
    mock_get_records.return_value = frozen
    mock_save_snapshot.return_value = SnapshotMeta(
        row_count=3,
        columns=["accession_id"],
        query_hash=normalised_query_hash(FROZEN_QUERY),
        created_at="2026-08-26T00:00:00+00:00",
    )

    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["row_count"] == 3
    assert payload["has_accessions"] is True
    assert payload["query_hash"] == normalised_query_hash(FROZEN_QUERY)
    # The frame is what validate_query's emission produced, read FRESH (use_cache=False —
    # a re-approval must not re-freeze the stale frame the statistics run cached);
    # the hash is of the RAW query.
    mock_get_records.assert_called_once_with(mock_validate_query.return_value, use_cache=False)
    mock_save_snapshot.assert_called_once()
    assert mock_save_snapshot.call_args.kwargs["query_hash"] == normalised_query_hash(FROZEN_QUERY)


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_below_threshold_persists_nothing(
    mock_snapshot_enabled, mock_decrypt, mock_validate_query, mock_get_records, mock_save_snapshot, mock_get_settings
):
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_get_records.return_value = pd.DataFrame({"accession_id": ["A1"]})

    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == _BELOW_THRESHOLD_DETAIL
    mock_save_snapshot.assert_not_called()


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_oversize_returns_413_without_detail_leakage(
    mock_snapshot_enabled, mock_decrypt, mock_validate_query, mock_get_records, mock_save_snapshot, mock_get_settings
):
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_records.return_value = pd.DataFrame({"accession_id": ["A1", "A2", "A3"]})
    mock_save_snapshot.side_effect = SnapshotTooLarge("snapshot is 999 bytes, over the 10-byte limit")

    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 413
    assert "byte" not in response.json()["detail"]  # category-only, no internals


@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_store_disabled_returns_503(mock_snapshot_enabled):
    mock_snapshot_enabled.return_value = False
    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)
    assert response.status_code == 503


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_non_uuid_project_id_returns_400(
    mock_snapshot_enabled, mock_decrypt, mock_validate_query, mock_get_records, mock_save_snapshot, mock_get_settings
):
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = "not-a-uuid"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_records.return_value = pd.DataFrame({"accession_id": ["A1", "A2", "A3"]})
    mock_save_snapshot.side_effect = ValueError("project_id must be a UUID")

    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 400


@patch("data_access_api.routers.cohort.delete_snapshot")
@patch("data_access_api.routers.cohort.decrypt")
def test_delete_snapshot_route_is_idempotent(mock_decrypt, mock_delete_snapshot):
    mock_decrypt.return_value = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"
    mock_delete_snapshot.side_effect = [True, False]

    first = client.post("/cohort/snapshot/delete", json={"encrypted_project_id": "enc"}, headers=WRITE_AUTH_HEADERS)
    second = client.post("/cohort/snapshot/delete", json={"encrypted_project_id": "enc"}, headers=WRITE_AUTH_HEADERS)

    assert first.json() == {"deleted": True}
    assert second.json() == {"deleted": False}


# (Auth coverage for the snapshot routes lives in test_cohort.py: the parametrised
# missing-key / wrong-key tests cover the trust-internal gate alongside every other
# /cohort route, and the cohort-admin tests cover the extra AES-possession gate the
# WRITE routes carry — a valid trust-internal key without the proof is refused 403.)
