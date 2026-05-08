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


def test_cohort_endpoint_rejects_below_threshold(http_client):
    """A query with 0 matching rows trips the cohort threshold and returns 400."""
    response = http_client.post(
        "/cohort",
        json=_cohort_payload(
            "SELECT * FROM omop.image_occurrence WHERE accession_id = 'NONEXISTENT'"
        ),
    )
    assert response.status_code == 400
    assert "too few records" in response.json()["detail"].lower()


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
