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

from tests.integration.conftest import AUTH_HEADERS, COHORT_ADMIN_HEADERS


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


# Row-level routes key everything on the hub project id, which must be a UUID (it becomes a
# directory name in the snapshot store).
_PROJECT_A = "0b91a3f2-30c3-4bd5-9a1e-2f24c7f5a111"
_PROJECT_B = "4d5e6f70-8192-4a3b-bc4d-5e6f70819222"


def _dataframe_payload(query: str, project_id: str = _PROJECT_A) -> dict:
    """Payload for the row-level + snapshot routes, which take ``DataframeQuery``.

    ``encrypted_project_id`` is encrypted with the same AES key the container is configured
    with, so the decrypt path stays real rather than mocked. The import is function-local:
    the conftest pins ``AES_KEY_BASE64`` before any ``data_access_api`` module builds its
    Settings singleton.
    """
    from data_access_api.utils.encryption import encrypt

    return {"encrypted_project_id": encrypt(project_id), "query": query}


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


# ---------------------------------------------------------------------------
# Snapshot lifecycle: the row-level routes serve ONLY the frozen approved cohort
# (FLIP#857). Each test uses its own project UUID so the per-session store never
# couples tests; snapshots are cleaned up where a later test would collide.
# ---------------------------------------------------------------------------

_SEED_COHORT_QUERY = (
    "SELECT c.concept_code AS modality, io.accession_id "
    "FROM omop.image_occurrence io "
    "LEFT JOIN omop.concept c ON c.concept_id = io.modality_concept_id"
)


def _create_snapshot(http_client, query: str, project_id: str) -> httpx.Response:
    # The write route needs the cohort-admin proof on top of the client's trust-internal key.
    return http_client.post(
        "/cohort/snapshot", json=_dataframe_payload(query, project_id), headers=COHORT_ADMIN_HEADERS
    )


def test_row_level_routes_refuse_without_a_snapshot(http_client):
    """Fail-closed: no approved snapshot ⇒ no row-level data, on both routes."""
    payload = _dataframe_payload("SELECT * FROM omop.image_occurrence", "97fca5ab-0000-4000-8000-000000000001")
    for path in ("/cohort/dataframe", "/cohort/accession-ids"):
        response = http_client.post(path, json=payload)
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "No approved cohort snapshot exists for this project."


def test_snapshot_write_routes_reject_trust_internal_key_without_cohort_admin_proof(http_client):
    """End-to-end: the container refuses a cohort-defining write from a caller that holds only the
    shared trust-internal key (what fl-client has) — the AES-possession gate closes that path
    (FLIP#857). ``http_client`` carries AUTH_HEADERS but not COHORT_ADMIN_HEADERS."""
    from data_access_api.utils.encryption import encrypt

    create = http_client.post("/cohort/snapshot", json=_dataframe_payload(_SEED_COHORT_QUERY, _PROJECT_B))
    assert create.status_code == 403, create.text
    delete = http_client.post("/cohort/snapshot/delete", json={"encrypted_project_id": encrypt(_PROJECT_B)})
    assert delete.status_code == 403, delete.text


def test_snapshot_then_dataframe_serves_the_frozen_cohort(http_client):
    """The full approval-time flow: freeze once, then serve — ignoring the client's SQL."""
    created = _create_snapshot(http_client, _SEED_COHORT_QUERY, _PROJECT_A)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["row_count"] == 24
    assert body["has_accessions"] is True
    assert set(body["columns"]) == {"modality", "accession_id"}

    # The served query is hostile-looking and never executed: the frozen frame comes back.
    response = http_client.post(
        "/cohort/dataframe",
        json=_dataframe_payload("SELECT * FROM omop.person", _PROJECT_A),
    )
    assert response.status_code == 200, response.text
    frame = response.json()
    assert set(frame.keys()) == {"modality", "accession_id"}
    assert len(frame["modality"]) == 24
    assert frame["modality"].count("CT") == 12
    assert frame["modality"].count("MR") == 8
    assert frame["modality"].count("XR") == 4
    assert len(set(frame["accession_id"])) == 24

    # And the frozen accession pointer set serves from the same artefact.
    ids_response = http_client.post(
        "/cohort/accession-ids",
        json=_dataframe_payload("SELECT 1 AS nothing FROM omop.person", _PROJECT_A),
    )
    assert ids_response.status_code == 200, ids_response.text
    accession_ids = ids_response.json()["accession_ids"]
    assert len(accession_ids) == 24
    assert "ACC-1001" in accession_ids


def test_tabular_snapshot_serves_empty_accession_list(http_client):
    """A frozen cohort without accession_id is a tabular project: imaging no-ops, no error.

    (Pre-snapshot, a cohort not projecting accession_id produced an UndefinedColumn 400 out
    of the live wrapped query; with frozen serving the column's absence is a legitimate
    project shape, recorded in the snapshot's metadata.)
    """
    created = _create_snapshot(http_client, "SELECT person_id FROM omop.person", _PROJECT_B)
    assert created.status_code == 200, created.text
    assert created.json()["has_accessions"] is False

    response = http_client.post(
        "/cohort/accession-ids",
        json=_dataframe_payload("SELECT person_id FROM omop.person", _PROJECT_B),
    )
    assert response.status_code == 200, response.text
    assert response.json()["accession_ids"] == []


def test_below_threshold_snapshot_is_refused_and_persists_nothing(http_client):
    """The disclosure floor is enforced at freeze time; a refused freeze leaves no artefact.

    modality_concept_id 4013632 = 'XR'; the seed has 4 such rows, under the stack's
    COHORT_QUERY_THRESHOLD of 5. The refusal text matches the row-level routes' fixed
    below-threshold string, and a zero-row cohort refuses byte-identically so the snapshot
    route cannot act as a row-count oracle either.
    """
    project_id = "97fca5ab-0000-4000-8000-000000000002"
    below = _create_snapshot(
        http_client,
        "SELECT accession_id FROM omop.image_occurrence WHERE modality_concept_id = 4013632",
        project_id,
    )
    zero = _create_snapshot(
        http_client,
        "SELECT accession_id FROM omop.image_occurrence WHERE accession_id = 'NONEXISTENT'",
        project_id,
    )
    assert below.status_code == zero.status_code == 403
    assert below.text == zero.text
    assert below.json()["detail"] == "Cohort is too small for row-level data to be released."

    # Nothing was persisted: the project still refuses row-level serving outright.
    response = http_client.post(
        "/cohort/dataframe", json=_dataframe_payload("SELECT 1 AS one FROM omop.person", project_id)
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "No approved cohort snapshot exists for this project."


def test_snapshot_route_rejects_unsafe_sql(http_client):
    """validate_query remains the authority on the one query the snapshot route executes."""
    response = _create_snapshot(
        http_client,
        "INSERT INTO omop.person (person_id) VALUES (999)",
        "97fca5ab-0000-4000-8000-000000000003",
    )
    assert response.status_code == 400, response.text


def test_reapproval_replaces_the_snapshot_and_delete_removes_it(http_client):
    """Overwrite-on-reapproval and the FLIP#997 teardown hook, end to end."""
    from data_access_api.utils.encryption import encrypt

    project_id = "97fca5ab-0000-4000-8000-000000000004"
    first = _create_snapshot(http_client, _SEED_COHORT_QUERY, project_id)
    assert first.status_code == 200, first.text

    second = _create_snapshot(http_client, "SELECT person_id, accession_id FROM omop.image_occurrence", project_id)
    assert second.status_code == 200, second.text

    served = http_client.post(
        "/cohort/dataframe", json=_dataframe_payload("SELECT 1 AS one FROM omop.person", project_id)
    )
    assert set(served.json().keys()) == {"person_id", "accession_id"}

    deleted = http_client.post(
        "/cohort/snapshot/delete", json={"encrypted_project_id": encrypt(project_id)}, headers=COHORT_ADMIN_HEADERS
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    again = http_client.post(
        "/cohort/snapshot/delete", json={"encrypted_project_id": encrypt(project_id)}, headers=COHORT_ADMIN_HEADERS
    )
    assert again.json() == {"deleted": False}

    refused = http_client.post(
        "/cohort/dataframe", json=_dataframe_payload("SELECT 1 AS one FROM omop.person", project_id)
    )
    assert refused.status_code == 403
