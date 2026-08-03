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
from sqlalchemy.exc import SQLAlchemyError

from data_access_api.main import app
from data_access_api.routers.schema import StatisticsResponse
from tests.conftest import AUTH_HEADERS

client = TestClient(app)

# Sample input request and output
sample_query_input = {
    "encrypted_project_id": "my_project",
    "query_id": "1",
    "query_name": "query_1",
    "query": "SELECT * FROM omop.radiology_occurrence",
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

    # Mock DataFrame with fewer records than threshold
    mock_df = pd.DataFrame({"col1": range(3)})
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

    mock_df = pd.DataFrame({"col1": []})
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

# Expected output
sample_df_dict = {
    "age": [25, 30, 40],
    "gender": ["M", "F", "M"],
}


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.validate_query")
def test_get_dataframe_success(mock_validate_query, mock_get_records, mock_decrypt, mock_get_settings):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.return_value = pd.DataFrame(sample_df_dict)

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == sample_df_dict
    mock_decrypt.assert_called_once_with("encrypted-id")
    mock_validate_query.assert_called_once_with(sample_dataframe_query["query"])
    # The engine receives what validate_query emitted from the checked AST,
    # never the caller's raw string.
    mock_get_records.assert_called_once_with(mock_validate_query.return_value)


@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.validate_query")
def test_get_dataframe_invalid_query(mock_validate_query, mock_decrypt):
    mock_decrypt.return_value = "decrypted-id"
    mock_validate_query.side_effect = HTTPException(status_code=400, detail="Invalid query syntax")

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid query syntax"


@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_dataframe_sqlalchemy_error(mock_get_records, mock_decrypt):
    """Driver text must not reach the caller — the hub relays this detail to the UI."""
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.side_effect = SQLAlchemyError("relation omop.person has 42 rows for patient Bob")

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 500
    assert "Bob" not in response.json()["detail"]


@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_dataframe_generic_error(mock_get_records, mock_decrypt):
    """Unexpected exception text must not reach the caller either."""
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.side_effect = RuntimeError("connection string postgres://user:hunter2@omop-db")

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 500
    assert "hunter2" not in response.json()["detail"]


@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_dataframe_preserves_http_exception_from_get_records(mock_get_records, mock_decrypt):
    """A categorised 400 from get_records must not be re-wrapped as an opaque 500.

    ``get_records`` deliberately converts driver errors into category-only
    HTTPExceptions. ``except Exception`` also catches HTTPException, so the
    route used to swallow that work and re-raise every one of them as a 500
    whose detail was the *repr of the HTTPException*.
    """
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.side_effect = HTTPException(status_code=400, detail="Column 'nope' does not exist")

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"] == "Column 'nope' does not exist"


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_dataframe_rejects_cohort_below_threshold(mock_get_records, mock_decrypt, mock_get_settings):
    """Row-level data is withheld for cohorts smaller than COHORT_QUERY_THRESHOLD."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.return_value = pd.DataFrame({"age": range(9)})

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 403


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_dataframe_below_threshold_does_not_disclose_row_count(
    mock_get_records, mock_decrypt, mock_get_settings
):
    """The refusal must not reveal how many rows matched — 0 and 9 look identical."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_decrypt.return_value = "decrypted-id"

    details = []
    for row_count in (0, 9):
        mock_get_records.return_value = pd.DataFrame({"age": range(row_count)})
        response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)
        details.append(response.json()["detail"])

    assert details[0] == details[1]
    assert "9" not in details[1]


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_dataframe_allows_cohort_at_threshold(mock_get_records, mock_decrypt, mock_get_settings):
    """A cohort exactly at the threshold is released — the gate is not off-by-one."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.return_value = pd.DataFrame({"age": range(10)})

    response = client.post("/cohort/dataframe", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert len(response.json()["age"]) == 10


# ---------------------------------------------------------------------------
# /cohort/accession-ids
# ---------------------------------------------------------------------------


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_accession_ids_success(mock_get_records, mock_decrypt, mock_get_settings):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.return_value = pd.DataFrame({"accession_id": ["ACC1", "ACC2", "ACC3"]})

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"accession_ids": ["ACC1", "ACC2", "ACC3"]}
    mock_decrypt.assert_called_once_with("encrypted-id")
    # The caller's query must be wrapped server-side so only accession_id is projected.
    called_query = mock_get_records.call_args[0][0]
    assert called_query.startswith("SELECT accession_id FROM (")
    assert sample_dataframe_query["query"] in called_query


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_accession_ids_strips_trailing_semicolon(mock_get_records, mock_decrypt, mock_get_settings):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 1
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.return_value = pd.DataFrame({"accession_id": ["ACC1"]})

    response = client.post(
        "/cohort/accession-ids",
        json={"encrypted_project_id": "encrypted-id", "query": "SELECT * FROM cohort;  "},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    called_query = mock_get_records.call_args[0][0]
    # No bare semicolon should leak into the wrapped subquery.
    assert "SELECT * FROM cohort)" in called_query
    assert ";" not in called_query


@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.validate_query")
def test_get_accession_ids_invalid_query(mock_validate_query, mock_decrypt):
    mock_decrypt.return_value = "decrypted-id"
    mock_validate_query.side_effect = HTTPException(status_code=400, detail="Invalid query syntax")

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid query syntax"


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_accession_ids_missing_column(mock_get_records, mock_decrypt, mock_get_settings):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 1
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.return_value = pd.DataFrame({"some_other_column": [1, 2]})

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert "accession_id" in response.json()["detail"]


@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_accession_ids_propagates_http_exception(mock_get_records, mock_decrypt):
    """``get_records`` raises HTTPException for things like undefined tables/columns;
    the wrapping ``except`` clauses must not swallow that into a 500."""
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.side_effect = HTTPException(
        status_code=400, detail="The table 'omop.bogus' does not exist."
    )

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"] == "The table 'omop.bogus' does not exist."


@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_accession_ids_sqlalchemy_error_does_not_leak(mock_get_records, mock_decrypt):
    """Driver text must not reach the caller: the trust forwards this detail to the hub,
    which shows it to every project member (FLIP-PT-016)."""
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.side_effect = SQLAlchemyError("relation omop.secret_table row 42")

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 500
    assert response.json()["detail"] == "Query execution failed."
    assert "secret_table" not in response.text


@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_accession_ids_generic_error_does_not_leak(mock_get_records, mock_decrypt):
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.side_effect = RuntimeError("connection to 10.0.0.5 failed for user svc_omop")

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 500
    assert response.json()["detail"] == "Query execution failed."
    assert "svc_omop" not in response.text


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_accession_ids_rejects_cohort_below_threshold(mock_get_records, mock_decrypt, mock_get_settings):
    """Accession IDs are row-level identifiers and decide whose imaging is pulled into XNAT,
    so a below-threshold cohort is refused just as it is on /cohort/dataframe."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.return_value = pd.DataFrame({"accession_id": [f"ACC{i}" for i in range(9)]})

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == "Cohort is too small for row-level data to be released."
    # No identifier may appear in the refusal.
    assert "ACC" not in response.text


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_accession_ids_below_threshold_is_indistinguishable_from_zero(
    mock_get_records, mock_decrypt, mock_get_settings
):
    """A zero-row cohort and a below-threshold one must return byte-identical responses,
    or the refusal itself becomes a row-count oracle."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_decrypt.return_value = "decrypted-id"

    mock_get_records.return_value = pd.DataFrame({"accession_id": []})
    zero_response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    mock_get_records.return_value = pd.DataFrame({"accession_id": [f"ACC{i}" for i in range(9)]})
    below_response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert zero_response.status_code == below_response.status_code == 403
    assert zero_response.text == below_response.text


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_accession_ids_allows_cohort_at_threshold(mock_get_records, mock_decrypt, mock_get_settings):
    """Exactly at the threshold is allowed — the gate is `<`, not `<=`."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 10
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.return_value = pd.DataFrame({"accession_id": [f"ACC{i}" for i in range(10)]})

    response = client.post("/cohort/accession-ids", json=sample_dataframe_query, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert len(response.json()["accession_ids"]) == 10


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
    result = validate_query("SELECT * FROM omop.radiology_occurrence")
    assert "omop.radiology_occurrence" in result


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
