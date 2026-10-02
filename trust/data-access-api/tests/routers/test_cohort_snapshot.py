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

"""Route-level tests for approved-cohort membership serving and creation (FLIP#857)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from data_access_api.main import app
from data_access_api.routers.cohort import _BELOW_THRESHOLD_DETAIL, _NO_SNAPSHOT_DETAIL
from data_access_api.services.cohort_snapshot import (
    Snapshot,
    SnapshotExists,
    SnapshotTooLarge,
    SnapshotUnreadable,
    normalised_query_hash,
)
from tests.conftest import AUTH_HEADERS, WRITE_AUTH_HEADERS

client = TestClient(app)

FROZEN_QUERY = "SELECT person_id, accession_id, label FROM omop.image_occurrence"
PROJECT_UUID = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"

sample_dataframe_query = {
    "encrypted_project_id": "encrypted_my_project",
    "query": FROZEN_QUERY,
}


def _snapshot(
    person_ids: list[str] | None = None,
    accession_ids: list[str] | None = None,
    subject_count: int = 3,
    query: str = FROZEN_QUERY,
) -> Snapshot:
    columns = [c for c, ids in (("person_id", person_ids), ("accession_id", accession_ids)) if ids is not None]
    return Snapshot(
        query=query,
        query_hash=normalised_query_hash(query),
        person_ids=person_ids,
        accession_ids=accession_ids,
        row_count=subject_count,
        subject_count=subject_count,
        columns=columns,
        created_at=datetime.now(UTC).isoformat(),
    )


@pytest.fixture
def imaging_lookup():
    """Stand in for omop.image_occurrence behind keep_imaging_accessions / count_distinct_subjects.

    ``known`` is the set of accession numbers that are imaging studies; each resolves to a subject
    of its own.
    """
    known: set[str] = set()

    def resolve(query=None, params=None, **kwargs):
        ids = [i for i in (params or {}).get("accession_ids", []) if i in known]
        if "COUNT(DISTINCT" in str(query):
            return pd.DataFrame({"subject_count": [len(set(ids))]})
        return pd.DataFrame({"accession_id": list(dict.fromkeys(ids))})

    with patch("data_access_api.services.cohort.get_records", side_effect=resolve) as stub:
        stub.known = known
        yield stub


# ---------------------------------------------------------------------------
# /cohort/dataframe: the query of record, restricted to the frozen membership
# ---------------------------------------------------------------------------


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_runs_the_query_of_record_and_ignores_client_sql(
    mock_get_records, mock_validate_query, mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """Hostile client SQL is never validated or executed; the stored query runs, uncached."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(person_ids=["1", "2", "3"])
    live = pd.DataFrame({"person_id": [1, 2, 3], "label": [0, 1, 0]})
    mock_get_records.return_value = live

    body = {**sample_dataframe_query, "query": "SELECT * FROM omop.person; DROP TABLE omop.person"}
    response = client.post("/cohort/dataframe", json=body, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == live.to_dict(orient="list")
    mock_validate_query.assert_called_once_with(FROZEN_QUERY)
    # Uncached: a removal from OMOP must reach training on the next fetch.
    mock_get_records.assert_called_once_with(mock_validate_query.return_value, use_cache=False)


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_cannot_grow_past_the_frozen_membership(
    mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """A patient who joined OMOP after approval, and a new study of an approved patient, are both
    dropped: every frozen column the row has a value in must hold a member."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(person_ids=["1", "2"], accession_ids=["A1", "A2"], subject_count=2)
    mock_get_records.return_value = pd.DataFrame(
        {
            "person_id": [1, 2, 3, 1],
            "accession_id": ["A1", "A2", "A3", "A9"],
            "label": [0, 1, 1, 1],
        }
    )

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"person_id": [1, 2], "accession_id": ["A1", "A2"], "label": [0, 1]}


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_serves_a_members_row_whose_other_frozen_column_is_null(
    mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """A LEFT-JOINed accession that is NULL was counted at approval and is served; a NULL never vouches
    for a non-member, and a row with no frozen value at all cannot be matched."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(person_ids=["1", "2"], accession_ids=["A1"], subject_count=2)
    mock_get_records.return_value = pd.DataFrame(
        {
            "person_id": [1, 2, 3, None, None],
            "accession_id": ["A1", None, None, "A1", None],
            "label": [0, 1, 1, 0, 1],
        }
    )

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["label"] == [0, 1, 0]


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_cohort_that_froze_no_accession_value_admits_no_new_study(
    mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """accession_id was projected but NULL on every row at approval: the frozen set is empty, not absent, so a
    study a member gains later is still kept out — only the member's NULL-accession rows are served."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 1
    mock_get_snapshot.return_value = _snapshot(person_ids=["1", "2"], accession_ids=[], subject_count=2)
    mock_get_records.return_value = pd.DataFrame(
        {"person_id": [1, 1, 2], "accession_id": [None, "A9", None], "label": [0, 1, 1]}
    )

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["label"] == [0, 1]
    assert "A9" not in response.json()["accession_id"]


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_accession_ids_cohort_with_no_accession_values_is_served_like_a_tabular_one(
    mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """A cohort that projected accession_id but froze no value has no imaging to pull: an empty list, not
    a refusal that would fail the project's imaging at this trust."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(person_ids=["1", "2"], accession_ids=[], subject_count=2)

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"accession_ids": []}
    mock_get_records.assert_not_called()


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_matches_ids_across_int_and_float_dtypes(
    mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """A NULL in a later run turns an int person_id column float64; 7.0 is still member 7."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(person_ids=["7", "8"], subject_count=2)
    mock_get_records.return_value = pd.DataFrame({"person_id": [7.0, 8.0, None], "label": [0, 1, 1]})

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["person_id"] == [7.0, 8.0]


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_that_shrinks_below_the_floor_is_refused(
    mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """Opt-outs remove patients; the threshold is re-counted on every fetch, so a cohort that
    shrinks under the floor stops being served, with the fixed refusal."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 3
    mock_get_snapshot.return_value = _snapshot(person_ids=["1", "2", "3"])
    mock_get_records.return_value = pd.DataFrame({"person_id": [1, 2], "label": [0, 1]})

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == _BELOW_THRESHOLD_DETAIL


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_gates_on_distinct_subjects_not_rows(
    mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """Twelve rows from three patients is three subjects, under a floor of ten."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_get_snapshot.return_value = _snapshot(person_ids=["1", "2", "3"])
    mock_get_records.return_value = pd.DataFrame({"person_id": [1, 2, 3] * 4})

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == _BELOW_THRESHOLD_DETAIL


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_releases_nothing_when_the_query_no_longer_projects_a_frozen_column(
    mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """Membership that can no longer be checked matches nothing — fail-closed."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 1
    mock_get_snapshot.return_value = _snapshot(person_ids=["1", "2", "3"])
    mock_get_records.return_value = pd.DataFrame({"label": [0, 1, 0]})

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
    """No frozen membership ⇒ no row-level data, fail-closed, and no SQL runs."""
    mock_decrypt.return_value = "my_project"
    mock_get_snapshot.return_value = None

    response = client.post(path, json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == _NO_SNAPSHOT_DETAIL
    mock_validate_query.assert_not_called()
    mock_get_records.assert_not_called()


# ---------------------------------------------------------------------------
# /cohort/accession-ids: the frozen accessions that are still imaging studies
# ---------------------------------------------------------------------------


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_accession_ids_serves_frozen_ids_without_running_the_cohort_sql(
    mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings, imaging_lookup
):
    """The imaging poll costs two image_occurrence lookups, never the cohort query; a study since removed
    from OMOP drops out."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(accession_ids=["A1", "A2", "A3"])
    imaging_lookup.known.update({"A1", "A3"})

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"accession_ids": ["A1", "A3"]}
    mock_get_records.assert_not_called()


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
def test_accession_ids_never_releases_values_that_are_not_imaging_accessions(
    mock_get_snapshot, mock_decrypt, mock_get_settings, imaging_lookup
):
    """Person data aliased to accession_id rides along with real accessions that clear the floor
    only if unresolved values are released — they are not, and they do not count (FLIP#1259)."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(accession_ids=["1|1950|8507", "ACC1", "ACC2", "2|1962|8532"])
    imaging_lookup.known.update({"ACC1", "ACC2"})

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"accession_ids": ["ACC1", "ACC2"]}

    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 3
    padded = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)
    assert padded.status_code == 403


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
def test_accession_ids_tabular_cohort_returns_empty_list_not_an_error(
    mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """A cohort frozen without accession_id is a tabular project: imaging no-ops."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(person_ids=["1", "2", "3"])

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"accession_ids": []}


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
def test_accession_ids_below_threshold_is_indistinguishable_from_zero(
    mock_get_snapshot, mock_decrypt, mock_get_settings, imaging_lookup
):
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10

    details = []
    for rows in (0, 9):
        ids = [f"A{i}" for i in range(rows)]
        imaging_lookup.known.update(ids)
        mock_get_snapshot.return_value = _snapshot(accession_ids=ids)
        response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)
        assert response.status_code == 403
        details.append(response.json()["detail"])

    assert details[0] == details[1] == _BELOW_THRESHOLD_DETAIL


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.keep_imaging_accessions")
def test_accession_ids_lookup_failure_is_refused_as_below_threshold(
    mock_keep, mock_get_snapshot, mock_decrypt, mock_get_settings
):
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(accession_ids=["A1", "A2", "A3"])
    mock_keep.side_effect = RuntimeError("relation omop.image_occurrence does not exist")

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == _BELOW_THRESHOLD_DETAIL


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
def test_accession_ids_tabular_cohort_below_threshold_is_refused_not_emptied(
    mock_get_snapshot, mock_decrypt, mock_get_settings
):
    """A tabular cohort under the floor gets the fixed 403, never ``[]``: an empty list would tell
    a below-threshold tabular project apart from the refusal every other small cohort gets."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_get_snapshot.return_value = _snapshot(person_ids=["1", "2", "3"], subject_count=3)

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == _BELOW_THRESHOLD_DETAIL


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
def test_accession_ids_reads_image_occurrence_uncached(
    mock_get_snapshot, mock_decrypt, mock_get_settings, imaging_lookup
):
    """Both lookups bypass the query cache, so a study removed from OMOP drops out on the next
    poll rather than after CACHE_TTL_DAYS."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 1
    mock_get_snapshot.return_value = _snapshot(accession_ids=["A1", "A2"])
    imaging_lookup.known.update({"A1", "A2"})

    client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert imaging_lookup.call_count == 2
    assert all(call.kwargs.get("use_cache") is False for call in imaging_lookup.call_args_list)


@pytest.mark.parametrize(
    ("path", "status", "body"),
    [
        ("/cohort/dataframe", 403, {"detail": _BELOW_THRESHOLD_DETAIL}),
        ("/cohort/accession-ids", 200, {"accession_ids": []}),
    ],
)
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_a_membership_freezing_no_id_column_releases_nothing(
    mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings, path, status, body
):
    """Second line of defence behind ``Snapshot``'s own validation: a record with no frozen id
    column must never leave the filter open."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 1
    unfrozen = MagicMock(
        query=FROZEN_QUERY,
        query_hash=normalised_query_hash(FROZEN_QUERY),
        person_ids=None,
        accession_ids=None,
        subject_count=100,
    )
    mock_get_snapshot.return_value = unfrozen
    mock_get_records.return_value = pd.DataFrame({"person_id": [1, 2, 3], "label": [0, 1, 0]})

    response = client.post(path, json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == status
    assert response.json() == body


@pytest.mark.parametrize(
    ("failure", "status", "detail"),
    [
        (HTTPException(status_code=504, detail="Query timed out."), 504, "Query timed out."),
        (OperationalError("SELECT 1", {}, Exception("person_id=42 violates ...")), 500, "Query execution failed."),
        (RuntimeError("row value 42 leaked"), 500, "Query execution failed."),
    ],
)
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_query_failure_is_category_only(
    mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings, failure, status, detail
):
    """get_records' own HTTPExceptions pass through untouched; any other failure is a fixed 500
    whose detail carries no exception text (it reaches every project member on the hub)."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(person_ids=["1", "2", "3"])
    mock_get_records.side_effect = failure

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == status
    assert response.json()["detail"] == detail


@patch("data_access_api.routers.cohort.count_distinct_subjects")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_uncountable_cohort_is_refused_as_below_threshold(
    mock_get_records, mock_get_snapshot, mock_decrypt, mock_get_settings, mock_count
):
    """A subject count that cannot be taken is refused exactly like a small cohort."""
    mock_decrypt.return_value = "my_project"
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_snapshot.return_value = _snapshot(person_ids=["1", "2", "3"])
    mock_get_records.return_value = pd.DataFrame({"person_id": [1, 2, 3]})
    mock_count.side_effect = RuntimeError("image_occurrence unavailable")

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == _BELOW_THRESHOLD_DETAIL


# ---------------------------------------------------------------------------
# POST /cohort/snapshot (creation) and /cohort/snapshot/delete
# ---------------------------------------------------------------------------


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_freezes_ids_and_the_query_of_record(
    mock_snapshot_enabled, mock_decrypt, mock_validate_query, mock_get_records, mock_save_snapshot, mock_get_settings
):
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = PROJECT_UUID
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_records.return_value = pd.DataFrame(
        {"person_id": [3, 1, 2, 1], "accession_id": ["A3", "A1", "A2", "A4"], "label": [0, 1, 0, 1]}
    )
    mock_save_snapshot.return_value = _snapshot(person_ids=["1", "2", "3"], accession_ids=["A1", "A2", "A3", "A4"])

    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_accessions"] is True
    assert payload["query_hash"] == normalised_query_hash(FROZEN_QUERY)
    # Read FRESH (use_cache=False — the freeze must not take the stale frame the statistics run
    # cached).
    mock_get_records.assert_called_once_with(mock_validate_query.return_value, use_cache=False)
    kwargs = mock_save_snapshot.call_args.kwargs
    # The RAW query is stored: serving re-validates it on every run.
    assert kwargs["query"] == FROZEN_QUERY
    assert kwargs["person_ids"] == ["1", "2", "3"]
    assert kwargs["accession_ids"] == ["A1", "A2", "A3", "A4"]
    assert kwargs["row_count"] == 4
    assert kwargs["subject_count"] == 3
    assert kwargs["columns"] == ["person_id", "accession_id", "label"]


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_tolerates_a_duplicated_id_column(
    mock_snapshot_enabled, mock_decrypt, mock_validate_query, mock_get_records, mock_save_snapshot, mock_get_settings
):
    """``SELECT *`` over a join that keeps both sides' person_id freezes from the first copy."""
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = PROJECT_UUID
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_records.return_value = pd.DataFrame([[1, 25, 1], [2, 30, 2]], columns=["person_id", "age", "person_id"])
    mock_save_snapshot.return_value = _snapshot(person_ids=["1", "2"], subject_count=2)

    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 200
    assert mock_save_snapshot.call_args.kwargs["person_ids"] == ["1", "2"]


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
    mock_decrypt.return_value = PROJECT_UUID
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_get_records.return_value = pd.DataFrame({"person_id": [1], "accession_id": ["A1"]})

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
    mock_decrypt.return_value = PROJECT_UUID
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_records.return_value = pd.DataFrame({"person_id": [1, 2, 3], "accession_id": ["A1", "A2", "A3"]})
    mock_save_snapshot.side_effect = SnapshotTooLarge("snapshot is 999 bytes, over the 10-byte limit")

    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 413
    assert "byte" not in response.json()["detail"]  # category-only, no internals


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_store_write_failure_is_a_category_only_500(
    mock_snapshot_enabled, mock_decrypt, mock_validate_query, mock_get_records, mock_save_snapshot, mock_get_settings
):
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = PROJECT_UUID
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_records.return_value = pd.DataFrame({"person_id": [1, 2, 3]})
    mock_save_snapshot.side_effect = PermissionError("[Errno 13] Permission denied: '/snapshots/.tmp-x'")

    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 500
    assert response.json()["detail"] == "Snapshot persistence failed."


@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.load_snapshot")
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_keeps_an_existing_membership(
    mock_snapshot_enabled, mock_decrypt, mock_get_records, mock_load_snapshot, mock_save_snapshot
):
    """A re-queued snapshot (its first result never reached the hub, or the hub re-checks a frozen trust)
    must not re-run the query: patients added to OMOP since the freeze would enter the approved cohort."""
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = PROJECT_UUID
    mock_load_snapshot.return_value = _snapshot(person_ids=["1", "2", "3"])

    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["row_count"] == 3
    mock_get_records.assert_not_called()
    mock_save_snapshot.assert_not_called()


@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.load_snapshot")
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_with_a_different_query_keeps_the_frozen_one_and_warns(
    mock_snapshot_enabled, mock_decrypt, mock_get_records, mock_load_snapshot, mock_save_snapshot, caplog
):
    """A changed query on a re-queued snapshot is logged, not adopted: the frozen membership stands."""
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = PROJECT_UUID
    mock_load_snapshot.return_value = _snapshot(person_ids=["1", "2", "3"])

    body = {**sample_dataframe_query, "query": "SELECT person_id FROM omop.person"}
    with caplog.at_level("WARNING"):
        response = client.post("/cohort/snapshot", json=body, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["query_hash"] == normalised_query_hash(FROZEN_QUERY)
    assert "kept the frozen membership" in caplog.text
    mock_get_records.assert_not_called()
    mock_save_snapshot.assert_not_called()


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.load_snapshot")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_ignores_a_replace_flag(
    mock_snapshot_enabled, mock_decrypt, mock_load_snapshot, mock_get_records
):
    """There is no way to swap a frozen membership: an unknown ``replace`` field is ignored, not honoured."""
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = PROJECT_UUID
    mock_load_snapshot.return_value = _snapshot(person_ids=["1", "2", "3"])

    response = client.post(
        "/cohort/snapshot", json={**sample_dataframe_query, "replace": True}, headers=WRITE_AUTH_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["row_count"] == 3
    mock_get_records.assert_not_called()


@patch("data_access_api.routers.cohort.count_distinct_subjects", return_value=3)
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.load_snapshot", return_value=None)
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_counts_subjects_uncached(
    mock_snapshot_enabled,
    mock_decrypt,
    mock_validate_query,
    mock_get_records,
    mock_load_snapshot,
    mock_save_snapshot,
    mock_get_settings,
    mock_count,
):
    """The freeze's threshold count reads live OMOP too, never the image_occurrence lookup cached earlier."""
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = PROJECT_UUID
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_records.return_value = pd.DataFrame({"accession_id": ["A1", "A2", "A3"]})
    mock_save_snapshot.return_value = _snapshot(accession_ids=["A1", "A2", "A3"])

    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 200
    assert mock_count.call_args.kwargs == {"use_cache": False}


@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.load_snapshot", side_effect=SnapshotUnreadable("EIO"))
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_never_writes_over_an_unreadable_record(
    mock_snapshot_enabled, mock_decrypt, mock_load_snapshot, mock_get_records, mock_save_snapshot
):
    """A record that exists but cannot be read is not "not frozen yet": re-running the query and writing
    today's cohort over it would let the approved cohort grow. A category-only 500, nothing run or written."""
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = PROJECT_UUID

    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 500
    assert response.json() == {"detail": "Cohort snapshot store read failed."}
    mock_get_records.assert_not_called()
    mock_save_snapshot.assert_not_called()


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.save_snapshot", side_effect=SnapshotExists("raced"))
@patch("data_access_api.routers.cohort.load_snapshot")
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.snapshot_enabled")
def test_create_snapshot_that_loses_a_race_returns_the_winners_facts(
    mock_snapshot_enabled,
    mock_decrypt,
    mock_validate_query,
    mock_get_records,
    mock_load_snapshot,
    mock_save_snapshot,
    mock_get_settings,
):
    """Two freezes racing for one project: the first to land stands, the second reports it."""
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = PROJECT_UUID
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_load_snapshot.side_effect = [None, _snapshot(person_ids=["1", "2", "3"])]
    mock_get_records.return_value = pd.DataFrame({"person_id": [1, 2, 3, 4]})

    response = client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["row_count"] == 3


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
    mock_get_records.return_value = pd.DataFrame({"person_id": [1, 2, 3], "accession_id": ["A1", "A2", "A3"]})
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
