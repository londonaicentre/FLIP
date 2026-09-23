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

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from data_access_api.main import app
from data_access_api.routers.schema import StatisticsResponse
from tests.conftest import AUTH_HEADERS, WRITE_AUTH_HEADERS

client = TestClient(app)


@pytest.fixture(autouse=True)
def one_subject_per_accession():
    """Resolve every accession number to a subject of its own, unless a test says otherwise.

    That is the one-study-per-patient world ``COHORT_QUERY_THRESHOLD`` was written for, so the
    gate tests below keep testing exactly what they always tested. The subject lookup lives in the
    service module, so patching the router's ``get_records`` does not reach it. Tests that
    exercise the per-subject rule itself patch this with their own count.
    """

    def resolve(query=None, params=None, **kwargs):
        accession_ids = (params or {}).get("accession_ids", [])
        return pd.DataFrame({"subject_count": [len(set(accession_ids))]})

    with patch("data_access_api.services.cohort.get_records", side_effect=resolve) as stub:
        yield stub


# Sample input request and output
sample_query_input = {
    "encrypted_project_id": "my_project",
    "query_id": "1",
    "query_name": "query_1",
    "query": "SELECT * FROM omop.image_occurrence",
    "trust_id": "mock_trust",
}

sample_statistics_response = StatisticsResponse(
    query_id="1",
    trust_id="mock_trust",
    record_count=21,
    created="2023-10-01T12:00:00Z",
    data=[
        {
            "name": "modality",
            "results": [{"value": "CT", "count": 21}],
        },
        {
            "name": "manufacturer",
            "results": [
                {"value": "GE", "count": 11},
                {"value": "Siemens", "count": 10},
            ],
        },
    ],
).model_dump()


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.get_statistics")
def test_receive_cohort_query_success(mock_get_statistics, mock_validate_query, mock_get_settings, mock_get_records):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_statistics.return_value = sample_statistics_response

    # Mock DataFrame
    mock_df = pd.DataFrame({"col1": range(10)})
    mock_get_records.return_value = mock_df

    response = client.post("/cohort", json=sample_query_input, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == sample_statistics_response
    mock_validate_query.assert_called_once_with(sample_query_input["query"])
    # The engine receives what validate_query emitted from the checked AST,
    # never the caller's raw string.
    mock_get_records.assert_called_once_with(mock_validate_query.return_value)
    mock_get_statistics.assert_called_once()


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
def test_receive_cohort_query_invalid_validation(mock_validate_query, mock_get_settings):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_validate_query.side_effect = HTTPException(status_code=400, detail="Invalid field in query")

    response = client.post("/cohort", json=sample_query_input, headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid field in query"


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.get_statistics")
def test_receive_cohort_query_statistics_error(
    mock_get_statistics, mock_validate_query, mock_get_settings, mock_get_records
):
    """Aggregation failures return a category only — the hub relays this detail to every
    project member, and raw exception text can carry row values (FLIP-PT-016)."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_statistics.side_effect = RuntimeError("failed on patient 12345 birth_datetime")

    # Mock DataFrame
    mock_df = pd.DataFrame({"col1": range(10)})
    mock_get_records.return_value = mock_df

    response = client.post("/cohort", json=sample_query_input, headers=AUTH_HEADERS)

    assert response.status_code == 500
    assert response.json()["detail"] == "Statistics aggregation failed."
    assert "12345" not in response.text


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
def test_receive_cohort_query_too_few_records(mock_validate_query, mock_get_settings, mock_get_records):
    """Below-threshold count is a privacy-suppressed normal response, not an error.

    Returning 200 with an empty ``data`` list lets trust-api forward a 0-count
    result back to the hub so the per-trust UI status leaves "running" and shows
    0 instead of getting stuck.
    """
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5

    # Three subjects below a floor of five. person_id is projected so this exercises the
    # below-threshold branch; a frame with no subject column would suppress through the
    # *uncountable* branch instead and pass for the wrong reason.
    mock_df = pd.DataFrame({"person_id": range(3), "col1": range(3)})
    mock_get_records.return_value = mock_df

    response = client.post("/cohort", json=sample_query_input, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["record_count"] == 0
    assert body["data"] == []
    # Below-threshold count is privacy-suppressed on the wire (a genuine zero is suppressed
    # the same way, so it can't reveal whether >=1 patient matched) (#519).
    assert body["suppressed"] is True
    assert body["query_id"] == sample_query_input["query_id"]
    assert body["trust_id"] == sample_query_input["trust_id"]


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
def test_receive_cohort_query_zero_records(mock_validate_query, mock_get_settings, mock_get_records):
    """A query that genuinely returns 0 rows is privacy-suppressed identically to a
    below-threshold count (suppressed=True), so the wire can't reveal a true zero from a
    small count (#519, security review)."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5

    # Zero rows with the subject column projected: zero subjects, below-threshold branch.
    mock_df = pd.DataFrame({"person_id": [], "col1": []})
    mock_get_records.return_value = mock_df

    response = client.post("/cohort", json=sample_query_input, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["record_count"] == 0
    assert body["data"] == []
    # A genuine zero is suppressed the same as a 1..N-1 count — no membership leak.
    assert body["suppressed"] is True


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
def test_receive_cohort_query_uncountable_cohort_is_suppressed(
    mock_validate_query, mock_get_settings, mock_get_records, caplog
):
    """A cohort exposing neither person_id nor accession_id is suppressed, not rejected.

    Kept as a separate case from the below-threshold tests so the two branches are pinned
    independently: this one must log the shape problem, the others must not.
    """
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_records.return_value = pd.DataFrame({"col1": range(30)})

    with caplog.at_level("WARNING"):
        response = client.post("/cohort", json=sample_query_input, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["suppressed"] is True
    assert response.json()["record_count"] == 0
    assert "neither person_id nor accession_id" in caplog.text


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
def test_receive_cohort_query_zero_rows_with_person_id_is_not_misdiagnosed(
    mock_validate_query, mock_get_settings, mock_get_records, caplog
):
    """A zero-row cohort that projected person_id is a zero, not an uncountable cohort.

    ``dropna(axis=1, how="all")`` on an empty frame drops every column (each is vacuously
    all-null), which used to turn a correctly shaped zero-row result into "exposes neither
    column" in the log. The wire response is identical either way; the diagnosis is what this
    pins, on exactly the frame a vocabulary-less trust produces (FLIP#967).
    """
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_records.return_value = pd.DataFrame({"person_id": pd.Series([], dtype="int64"), "age": []})

    with caplog.at_level("WARNING"):
        response = client.post("/cohort", json=sample_query_input, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["suppressed"] is True
    assert "neither person_id nor accession_id" not in caplog.text


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
def test_receive_cohort_query_execution_error(mock_validate_query, mock_get_settings, mock_get_records):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_records.side_effect = Exception("Database connection failed")

    # We expect the exception to propagate and be handled by FastAPI's default exception handler or bubble up
    # Based on the code, it re-raises the exception, so we expect a 500 or the exception itself depending on middleware
    # Since we are using TestClient, unhandled exceptions in the app might raise directly or return 500
    # The code catches Exception and logs it, then re-raises it.
    # FastAPI TestClient will catch the re-raised exception if not handled by an exception handler.
    # However, let's see how the app is configured. Assuming standard FastAPI behavior for unhandled exceptions.

    with pytest.raises(Exception, match="Database connection failed"):
        client.post("/cohort", json=sample_query_input, headers=AUTH_HEADERS)


# Sample request input
sample_dataframe_query = {
    "encrypted_project_id": "encrypted-id",
    "query": "SELECT age, gender FROM dummy_table",
}

# The /cohort/dataframe and /cohort/accession-ids behavioural tests live in
# tests/routers/test_cohort_snapshot.py: both routes serve ONLY the frozen
# approved-cohort snapshot (FLIP#857) — the live-SQL serving path they used to
# have (and the tests that pinned it) is gone. Live SQL now runs only on
# /cohort (statistics) above and /cohort/snapshot (creation).


# The encrypted project id is opened (AES-GCM, ``project_id`` context) before the snapshot is
# looked up, so a payload that fails to open is answered on the row-level routes regardless of
# whether a snapshot exists.
@patch("data_access_api.routers.cohort.decrypt")
def test_get_dataframe_rejects_a_project_id_that_fails_authentication(mock_decrypt):
    """A tampered or foreign-key project id is the caller's problem: 400 with a reason, not a bare 500."""
    from cryptography.exceptions import InvalidTag

    mock_decrypt.side_effect = InvalidTag()

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert "failed authentication" in response.json()["detail"]


@patch("data_access_api.routers.cohort.decrypt")
def test_get_dataframe_rejects_a_malformed_envelope(mock_decrypt):
    mock_decrypt.side_effect = ValueError("Payload is not a FLIP encryption envelope")

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert "not a FLIP encryption envelope" in response.json()["detail"]


@patch("data_access_api.routers.cohort.decrypt")
def test_get_dataframe_reports_an_unexpected_decrypt_fault_as_500(mock_decrypt):
    """Not the caller's payload (key load, cipher fault): a logged 500 naming the type, as imaging-api answers."""
    mock_decrypt.side_effect = Exception("bad key")

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to decrypt encrypted_project_id (Exception)"


@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.decrypt")
def test_get_accession_ids_rejects_a_project_id_that_fails_authentication(mock_decrypt, mock_get_snapshot):
    from cryptography.exceptions import InvalidTag

    mock_decrypt.side_effect = InvalidTag()

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert "failed authentication" in response.json()["detail"]
    mock_get_snapshot.assert_not_called()


# ---------------------------------------------------------------------------
# Trust-internal service auth — every /cohort route requires the header.
# Parametrised so the three endpoints stay covered together; if a future
# endpoint is added under /cohort it should be added to this list.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/cohort", sample_query_input),
        ("/cohort/dataframe", sample_dataframe_query),
        ("/cohort/accession-ids", sample_dataframe_query),
        ("/cohort/snapshot", sample_dataframe_query),
        ("/cohort/snapshot/delete", {"encrypted_project_id": "encrypted-id"}),
    ],
)
def test_cohort_route_rejects_missing_key(path, payload):
    response = client.post(path, json=payload)
    assert response.status_code == 401
    assert "missing" in response.json()["detail"].lower()


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/cohort", sample_query_input),
        ("/cohort/dataframe", sample_dataframe_query),
        ("/cohort/accession-ids", sample_dataframe_query),
        ("/cohort/snapshot", sample_dataframe_query),
        ("/cohort/snapshot/delete", {"encrypted_project_id": "encrypted-id"}),
    ],
)
def test_cohort_route_rejects_wrong_key(path, payload):
    response = client.post(path, json=payload, headers={"X-Trust-Internal-Service-Key": "wrong-key"})
    assert response.status_code == 401
    assert "invalid" in response.json()["detail"].lower()


def test_health_does_not_require_auth():
    """Regression: /health must stay reachable without the trust-internal key
    so liveness probes and operator checks keep working when /cohort is
    locked down."""
    response = client.get("/health/")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Cohort-admin auth — the snapshot WRITE routes require proof of possessing
# AES_KEY_BASE64 on top of the trust-internal key (FLIP#857), so a caller that
# holds only the shared trust-internal key (fl-client) cannot DEFINE or destroy a
# project's frozen cohort. The read routes must stay reachable with the
# trust-internal key alone.
# ---------------------------------------------------------------------------

_WRITE_ROUTES = [
    ("/cohort/snapshot", sample_dataframe_query),
    ("/cohort/snapshot/delete", {"encrypted_project_id": "encrypted-id"}),
]


@pytest.mark.parametrize(("path", "payload"), _WRITE_ROUTES)
def test_write_routes_reject_trust_internal_key_without_cohort_admin_proof(path, payload):
    """A valid trust-internal key alone (what fl-client holds) is refused with 403 —
    authenticated but not authorised to define the cohort."""
    response = client.post(path, json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 403
    assert "authorised" in response.json()["detail"].lower()


@pytest.mark.parametrize(("path", "payload"), _WRITE_ROUTES)
def test_write_routes_reject_wrong_cohort_admin_proof(path, payload):
    """A wrong AES-possession proof is refused with the same fixed 403 as a missing one,
    so the refusal never reveals whether the proof was absent or merely invalid."""
    headers = {**AUTH_HEADERS, "X-Cohort-Admin-Key": "not-the-real-proof"}
    response = client.post(path, json=payload, headers=headers)
    assert response.status_code == 403
    assert "authorised" in response.json()["detail"].lower()


@pytest.mark.parametrize("path", ["/cohort/dataframe", "/cohort/accession-ids"])
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_snapshot")
def test_read_routes_do_not_require_cohort_admin_proof(mock_get_snapshot, mock_decrypt, path):
    """The read routes must NOT gain the cohort-admin gate: the trust-internal key alone must
    get past auth into the handler (a cohort-admin 403 here would mean fl-client's get_dataframe
    broke). With no snapshot the handler reaches its own fail-closed 403 — distinct text — which
    proves auth let the caller through rather than blocking on cohort-admin."""
    mock_decrypt.return_value = "my_project"
    mock_get_snapshot.return_value = None
    response = client.post(path, json=sample_dataframe_query, headers=AUTH_HEADERS)
    assert "authorised" not in response.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# Parse-then-emit tests
#
# validate_query is the single parse-validate-emit step: it returns the caller's
# SQL re-emitted from the AST it just checked. These cases used to target a
# separate _parse_and_emit helper in this router, which re-parsed the query and
# kept its own copy of the single-statement and SELECT-shape rules. That second
# copy is gone; the behaviour it guarded is asserted here against the one
# remaining implementation.
# ---------------------------------------------------------------------------

from data_access_api.services.cohort import validate_query  # noqa: E402


def test_validate_query_rejects_empty_string():
    with pytest.raises(HTTPException) as exc_info:
        validate_query("")
    assert exc_info.value.status_code == 400


def test_validate_query_rejects_whitespace_only():
    with pytest.raises(HTTPException) as exc_info:
        validate_query("   \n\t  ")
    assert exc_info.value.status_code == 400


def test_validate_query_rejects_multi_statement():
    with pytest.raises(HTTPException) as exc_info:
        validate_query("SELECT 1; SELECT 2")
    assert exc_info.value.status_code == 400
    assert "one SQL statement" in exc_info.value.detail


def test_validate_query_rejects_multi_statement_with_drop():
    """A semicolon-separated DML statement is rejected before any execution."""
    with pytest.raises(HTTPException) as exc_info:
        validate_query("SELECT id FROM omop.person; DROP TABLE omop.person")
    assert exc_info.value.status_code == 400
    assert "one SQL statement" in exc_info.value.detail


def test_validate_query_strips_trailing_semicolon():
    result = validate_query("SELECT 1;")
    assert ";" not in result
    assert "1" in result


def test_validate_query_emits_valid_select():
    result = validate_query("SELECT * FROM omop.image_occurrence")
    assert "omop.image_occurrence" in result


def test_validate_query_cte_roundtrip():
    query = "WITH cte AS (SELECT id FROM omop.person) SELECT * FROM cte"
    result = validate_query(query)
    assert "cte" in result.lower()
    assert result.upper().startswith("WITH")


def test_validate_query_complex_pg_syntax_roundtrip():
    """PG-specific constructs (aggregate FILTER clauses) survive the round-trip."""
    query = (
        "SELECT person_id, COUNT(*) FILTER (WHERE age > 18) AS adult_count "
        "FROM omop.person GROUP BY person_id"
    )
    result = validate_query(query)
    assert "person_id" in result
    assert "adult_count" in result.lower()


@pytest.mark.parametrize(
    "query",
    [
        "INSERT INTO omop.person (person_id) VALUES (1)",
        "DROP TABLE omop.person",
        "UPDATE omop.person SET gender_concept_id = 0 WHERE person_id = 1",
        "DELETE FROM omop.person WHERE person_id = 1",
    ],
)
def test_validate_query_rejects_dml_and_ddl(query: str):
    with pytest.raises(HTTPException) as exc_info:
        validate_query(query)
    assert exc_info.value.status_code == 400
    assert "SELECT" in exc_info.value.detail


# ---------------------------------------------------------------------------
# The disclosure threshold counts subjects, not rows
#
# The count is taken once, when /cohort/snapshot freezes the cohort (FLIP#857), and stored
# with the artefact; the row-level routes gate on that frozen count (see
# test_cohort_snapshot.py). These cases pin how the count itself is taken at creation.
# ---------------------------------------------------------------------------

_SNAPSHOT_PROJECT_ID = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"


def _post_snapshot(mock_decrypt, mock_snapshot_enabled):
    mock_snapshot_enabled.return_value = True
    mock_decrypt.return_value = _SNAPSHOT_PROJECT_ID
    return client.post("/cohort/snapshot", json=sample_dataframe_query, headers=WRITE_AUTH_HEADERS)


@patch("data_access_api.routers.cohort.snapshot_enabled")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_snapshot_rejects_many_rows_from_too_few_subjects(
    mock_get_records, mock_decrypt, mock_get_settings, mock_save_snapshot, mock_snapshot_enabled
):
    """Forty rows covering three people is below a floor of ten.

    This is the case the row count used to wave through: the floor exists to stop a response
    revealing that ">=1 patient matched", and forty rows from three patients protects nobody.
    """
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_get_records.return_value = pd.DataFrame({"person_id": [1, 2, 3] * 40, "age": range(120)})

    response = _post_snapshot(mock_decrypt, mock_snapshot_enabled)

    assert response.status_code == 403
    mock_save_snapshot.assert_not_called()


@patch("data_access_api.routers.cohort.snapshot_enabled")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_snapshot_refuses_a_cohort_whose_subjects_cannot_be_counted(
    mock_get_records, mock_decrypt, mock_get_settings, mock_save_snapshot, mock_snapshot_enabled
):
    """No person_id and no accession_id means the floor cannot be applied, so nothing is frozen."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_get_records.return_value = pd.DataFrame({"age": range(500)})

    response = _post_snapshot(mock_decrypt, mock_snapshot_enabled)

    # 400, not the 403: this reports the shape of the query, never anything about the data, so it
    # is safe to name the missing column and useless as a membership oracle.
    assert response.status_code == 400
    assert "person_id" in response.json()["detail"]
    assert "accession_id" in response.json()["detail"]
    assert "500" not in response.json()["detail"]
    mock_save_snapshot.assert_not_called()


@patch("data_access_api.routers.cohort.snapshot_enabled")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_snapshot_counts_subjects_not_rows_at_the_boundary(
    mock_get_records, mock_decrypt, mock_get_settings, mock_save_snapshot, mock_snapshot_enabled
):
    """Exactly ten distinct people is frozen however many rows they contribute, and the frozen
    count is the subject count, not the row count."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_get_records.return_value = pd.DataFrame({"person_id": list(range(10)) * 3, "age": range(30)})
    mock_save_snapshot.side_effect = OSError("store unavailable")  # stop after the gate

    response = _post_snapshot(mock_decrypt, mock_snapshot_enabled)

    assert response.status_code == 500
    assert mock_save_snapshot.call_args.kwargs["subject_count"] == 10


@patch("data_access_api.routers.cohort.snapshot_enabled")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_snapshot_rejects_many_studies_from_too_few_subjects(
    mock_get_records,
    mock_decrypt,
    mock_get_settings,
    mock_save_snapshot,
    mock_snapshot_enabled,
    one_subject_per_accession,
):
    """Thirty studies that resolve to three patients do not clear a floor of ten."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_get_records.return_value = pd.DataFrame({"accession_id": [f"ACC{i}" for i in range(30)]})
    one_subject_per_accession.side_effect = None
    one_subject_per_accession.return_value = pd.DataFrame({"subject_count": [3]})

    response = _post_snapshot(mock_decrypt, mock_snapshot_enabled)

    assert response.status_code == 403
    mock_save_snapshot.assert_not_called()


@patch("data_access_api.routers.cohort.snapshot_enabled")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_snapshot_zero_and_few_subjects_are_indistinguishable(
    mock_get_records,
    mock_decrypt,
    mock_get_settings,
    mock_save_snapshot,
    mock_snapshot_enabled,
    one_subject_per_accession,
):
    """A cohort resolving to zero subjects and one resolving to three refuse byte-identically."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_get_records.return_value = pd.DataFrame({"accession_id": [f"ACC{i}" for i in range(30)]})
    one_subject_per_accession.side_effect = None

    responses = []
    for subject_count in (0, 3):
        one_subject_per_accession.return_value = pd.DataFrame({"subject_count": [subject_count]})
        responses.append(_post_snapshot(mock_decrypt, mock_snapshot_enabled))

    assert responses[0].status_code == responses[1].status_code == 403
    assert responses[0].json()["detail"] == responses[1].json()["detail"]
    assert "3" not in responses[1].json()["detail"]
    mock_save_snapshot.assert_not_called()


@patch("data_access_api.routers.cohort.snapshot_enabled")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_snapshot_counts_a_duplicated_person_id_column_instead_of_failing(
    mock_get_records, mock_decrypt, mock_get_settings, mock_save_snapshot, mock_snapshot_enabled
):
    """A projection carrying person_id twice is counted, never a 500.

    ``SELECT *`` over a join that keeps both sides' person_id arrives with a duplicated column;
    ``df["person_id"]`` is then a DataFrame, and counting it naively raised a TypeError.
    """
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_get_records.return_value = pd.DataFrame(
        [[1, 25, 1], [2, 30, 2], [3, 40, 3]], columns=["person_id", "age", "person_id"]
    )
    mock_save_snapshot.side_effect = OSError("store unavailable")  # stop after the gate

    _post_snapshot(mock_decrypt, mock_snapshot_enabled)

    assert mock_save_snapshot.call_args.kwargs["subject_count"] == 3


@patch("data_access_api.routers.cohort.snapshot_enabled")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.count_distinct_subjects")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_snapshot_refuses_when_the_subject_count_cannot_be_taken(
    mock_get_records, mock_decrypt, mock_get_settings, mock_count, mock_save_snapshot, mock_snapshot_enabled
):
    """A failure of the count itself is refused exactly like a small cohort.

    The count is the one call outside the route's get_records handling; unguarded, a lookup
    error there escaped as a 500 (or a category 400 naming a table), a third response shape
    that says why the cohort could not be gated.
    """
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_get_records.return_value = pd.DataFrame({"accession_id": [f"ACC{i}" for i in range(30)]})

    mock_count.side_effect = HTTPException(status_code=400, detail="The table 'image_occurrence' does not exist.")
    failed = _post_snapshot(mock_decrypt, mock_snapshot_enabled)

    mock_count.side_effect = None
    mock_count.return_value = 3
    small = _post_snapshot(mock_decrypt, mock_snapshot_enabled)

    assert failed.status_code == small.status_code == 403
    assert failed.json()["detail"] == small.json()["detail"]
    assert "image_occurrence" not in failed.text
    mock_save_snapshot.assert_not_called()


@patch("data_access_api.routers.cohort.snapshot_enabled")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_snapshot_never_counts_more_subjects_than_rows(
    mock_get_records,
    mock_decrypt,
    mock_get_settings,
    mock_save_snapshot,
    mock_snapshot_enabled,
    one_subject_per_accession,
):
    """Three accession numbers cannot clear a floor of ten however many people they resolve to.

    Nothing in the OMOP schema makes accession_id functional on person_id, so the lookup can
    return more subjects than the cohort has rows; the row count stays the upper bound the old
    row check provided.
    """
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_get_records.return_value = pd.DataFrame({"accession_id": ["ACC1", "ACC2", "ACC3"]})
    one_subject_per_accession.side_effect = None
    one_subject_per_accession.return_value = pd.DataFrame({"subject_count": [12]})

    response = _post_snapshot(mock_decrypt, mock_snapshot_enabled)

    assert response.status_code == 403
    mock_save_snapshot.assert_not_called()
