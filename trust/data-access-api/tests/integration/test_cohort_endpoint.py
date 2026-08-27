# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Direct ``/cohort`` endpoint tests against data-access-api + a real MI-CDM Postgres.

Complements the trust-api integration suite — that one goes through trust-api's
handler; this one drives data-access-api directly to keep coverage focused on the
SQL template + MI-CDM schema layer. Same compose stack, same seed (``image_occurrence``
joined to ``concept`` for modality lookups), asserts on counts that match the seed
comments (omop_seed.sql).
"""

import httpx
import pytest

from tests.integration.conftest import AUTH_HEADERS


@pytest.fixture
def http_client(data_access_api_url: str):
    """Plain httpx client with auth pre-baked. Per-test scope so timeouts stay tight."""
    with httpx.Client(base_url=data_access_api_url, headers=AUTH_HEADERS, timeout=30.0) as client:
        yield client


def _cohort_payload(query: str) -> dict:
    return {
        "encrypted_project_id": "enc-1",
        "query_id": "qid-direct",
        "query_name": "B3 direct test",
        "query": query,
        "trust_id": "trust_test",
    }


def _dataframe_payload(query: str) -> dict:
    """Payload for the two row-level routes, which take ``DataframeQuery`` (two fields only).

    ``encrypted_project_id`` is encrypted with the same AES key the container is configured
    with, so the decrypt path stays real rather than mocked. The import is function-local for
    the same reason it is in ``test_dataframe_endpoint_returns_seeded_columns``: the conftest
    pins ``AES_KEY_BASE64`` before any ``data_access_api`` module builds its Settings singleton.
    """
    from data_access_api.utils.encryption import encrypt

    return {"encrypted_project_id": encrypt("integration-project-1"), "query": query}


def test_cohort_endpoint_returns_aggregates_for_image_occurrences(http_client):
    """All 24 image_occurrence rows clear the threshold and come back with the full aggregate set."""
    response = http_client.post(
        "/cohort",
        json=_cohort_payload(
            "SELECT person_id, modality_concept_id, accession_id FROM omop.image_occurrence"
        ),
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["record_count"] == 24
    aggregate_names = {g["name"] for g in body["data"]}
    assert {"Counts", "Nulls", "Sex Distribution", "Age Distribution"} <= aggregate_names


def test_cohort_endpoint_suppresses_below_threshold(http_client):
    """A non-zero below-threshold cohort (4 XR rows in the seed) is privacy-suppressed:
    HTTP 200 with record_count=0, empty data and suppressed=True (#519)."""
    response = http_client.post(
        "/cohort",
        json=_cohort_payload(
            # modality_concept_id 4013632 = 'XR' (Plain Film); the seed has 4 such rows,
            # below the threshold of 10 — a genuine below-threshold count, not a zero.
            "SELECT person_id, modality_concept_id, accession_id "
            "FROM omop.image_occurrence WHERE modality_concept_id = 4013632"
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["record_count"] == 0
    assert body["data"] == []
    # The 0 is privacy suppression of a real (1-9) count; a genuine zero is suppressed the
    # same way, so the flag can't reveal which 0s were genuine (#519).
    assert body["suppressed"] is True


def test_cohort_endpoint_genuine_zero_is_suppressed(http_client):
    """Privacy regression: a query matching no rows is suppressed identically to a
    below-threshold count (record_count=0, suppressed=True), so the wire never reveals a
    genuine zero apart from a small count (#519, security review)."""
    response = http_client.post(
        "/cohort",
        json=_cohort_payload(
            "SELECT * FROM omop.image_occurrence WHERE accession_id = 'NONEXISTENT'"
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["record_count"] == 0
    assert body["data"] == []
    assert body["suppressed"] is True


def test_cohort_endpoint_rejects_unsafe_sql(http_client):
    """validate_query rejects SQL injection attempts. The AST-based validator drops the
    multi-statement payload before the second statement (``DROP``) can reach Postgres."""
    response = http_client.post(
        "/cohort",
        json=_cohort_payload(
            "SELECT * FROM omop.image_occurrence; DROP TABLE omop.person"
        ),
    )
    assert response.status_code == 400
    # validate_query enforces "exactly one statement per request" as its first rule, so
    # query-stacking attempts are caught before the DDL/DML test.
    assert "one sql statement" in response.json()["detail"].lower()


def test_cohort_endpoint_requires_auth_header(data_access_api_url):
    """The ``/cohort`` router is gated by the trust-internal service key."""
    with httpx.Client(base_url=data_access_api_url, timeout=30.0) as client:
        response = client.post(
            "/cohort", json=_cohort_payload("SELECT * FROM omop.image_occurrence")
        )
    assert response.status_code == 401


def test_dataframe_endpoint_returns_seeded_columns(http_client):
    """``/cohort/dataframe`` returns column-oriented data straight from Postgres.

    The endpoint decrypts ``encrypted_project_id`` with the shared AES key, so the test
    encrypts a real project id with the same key the container is configured with. This
    keeps the encryption path real instead of mocking ``decrypt``.
    """
    from data_access_api.utils.encryption import encrypt

    payload = {
        "encrypted_project_id": encrypt("integration-project-1"),
        "query": (
            "SELECT c.concept_code AS modality, io.accession_id "
            "FROM omop.image_occurrence io "
            "LEFT JOIN omop.concept c ON c.concept_id = io.modality_concept_id"
        ),
    }
    response = http_client.post("/cohort/dataframe", json=payload)
    assert response.status_code == 200, response.text

    body = response.json()
    assert set(body.keys()) == {"modality", "accession_id"}
    assert len(body["modality"]) == 24
    # Sanity: counts in the dataframe should match the seed totals.
    assert body["modality"].count("CT") == 12
    assert body["modality"].count("MR") == 8
    assert body["modality"].count("XR") == 4
    # accession_id is unique per row in the seed.
    assert len(set(body["accession_id"])) == 24


def test_accession_ids_returns_seeded_ids(http_client):
    """``/cohort/accession-ids`` projects the id column out of the caller's cohort.

    The route wraps the caller's (re-emitted) SQL as ``SELECT accession_id FROM (...) AS
    cohort_subquery``, so only that one column ever crosses the trust boundary regardless of
    what the inner query selected.
    """
    response = http_client.post(
        "/cohort/accession-ids",
        json=_dataframe_payload("SELECT person_id, accession_id FROM omop.image_occurrence"),
    )
    assert response.status_code == 200, response.text

    accession_ids = response.json()["accession_ids"]
    assert len(accession_ids) == 24
    # accession_id is unique per row in the seed (ACC-1001 … ACC-1024).
    assert len(set(accession_ids)) == 24
    assert "ACC-1001" in accession_ids


def test_accession_ids_missing_column_surfaces_get_records_400(http_client):
    """A cohort that does not project ``accession_id`` fails inside ``get_records``, not after it.

    Pins which 400 actually reaches the caller. Because the route wraps the inner query in
    ``SELECT accession_id FROM (...)``, Postgres raises ``UndefinedColumn`` while the query is
    executing — so ``get_records`` converts it to a category 400 and the router's
    ``except HTTPException: raise`` branch re-raises it before any DataFrame is returned. A
    router-level "did the DataFrame come back with the column?" guard could never fire, which
    is why there isn't one; this test is the standing proof of that. The cohort here is 16
    rows — comfortably over the stack's threshold — so the refusal is about the column, not
    the cohort size.
    """
    response = http_client.post(
        "/cohort/accession-ids",
        json=_dataframe_payload("SELECT person_id FROM omop.person"),
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "The column 'accession_id' does not exist."


def test_accession_ids_below_threshold_is_refused(http_client):
    """A below-threshold cohort is refused outright — no partial list, no count.

    modality_concept_id 4013632 = 'XR'; the seed has 4 such rows, under the stack's
    COHORT_QUERY_THRESHOLD of 5.
    """
    response = http_client.post(
        "/cohort/accession-ids",
        json=_dataframe_payload(
            "SELECT accession_id FROM omop.image_occurrence WHERE modality_concept_id = 4013632"
        ),
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Cohort is too small for row-level data to be released."


def test_accession_ids_zero_rows_indistinguishable_from_below_threshold(http_client):
    """Privacy regression, against real Postgres rather than a mocked DataFrame.

    A cohort matching nothing and a cohort of 1-4 rows must produce byte-identical refusals,
    or the response itself becomes a row-count oracle: a caller could binary-search a
    predicate and learn whether *any* patient matches it, one bit at a time.
    """
    below_threshold = http_client.post(
        "/cohort/accession-ids",
        json=_dataframe_payload(
            "SELECT accession_id FROM omop.image_occurrence WHERE modality_concept_id = 4013632"
        ),
    )
    genuine_zero = http_client.post(
        "/cohort/accession-ids",
        json=_dataframe_payload(
            "SELECT accession_id FROM omop.image_occurrence WHERE accession_id = 'NONEXISTENT'"
        ),
    )

    assert below_threshold.status_code == genuine_zero.status_code == 403
    assert below_threshold.text == genuine_zero.text
