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

"""HTTP-level integration tests for the receive-cohort-results endpoint.

Pre-B1 these used ``real_client`` against an externally-running flip-api,
which never ran in CI and effectively skipped the test. They now drive the
endpoint through ``TestClient`` so the FastAPI dep graph is real (auth,
session, validation) but no out-of-process service is needed.

Pure-DB cohort save flows are in ``test_cohort_save_db_flow.py``.
"""

import uuid

import pytest
from fastapi import status
from sqlmodel import select

from flip_api.auth.access_manager import authenticate_trust
from flip_api.db.models.main_models import Queries, Trust
from flip_api.main import app


@pytest.fixture
def sample_cohort_dict():
    return {
        "query_id": str(uuid.uuid4()),
        "trust_id": str(uuid.uuid4()),
        "created": "2026-05-01T12:00:00Z",
        "record_count": 10,
        "data": [
            {
                "name": "age_group",
                "results": [
                    {"value": "<50", "count": 5},
                    {"value": ">=50", "count": 5},
                ],
            }
        ],
    }


class TestReceiveCohortResultsEndpoint:
    @pytest.fixture(autouse=True)
    def _override_auth(self):
        """Bypass trust signature verification — the route's auth path is unit-tested elsewhere."""
        app.dependency_overrides[authenticate_trust] = lambda: "Trust_1"
        yield
        app.dependency_overrides.pop(authenticate_trust, None)

    def test_bad_payload_returns_422(self, client):
        response = client.post(
            "/api/cohort/results",
            json={"trust_id": "abc", "record_count": "ten", "data": []},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_unauthorised_trust_id_returns_403(self, client, session, sample_cohort_dict: dict):
        """Auth says Trust_1, but the payload claims a different trust_id ⇒ 403."""
        # Trust_1 is seeded by integration_engine; its id won't match the
        # random uuid in sample_cohort_dict so the endpoint should reject.
        seeded = session.exec(select(Trust).where(Trust.name == "Trust_1")).first()
        assert seeded is not None
        assert sample_cohort_dict["trust_id"] != str(seeded.id)

        response = client.post("/api/cohort/results", json=sample_cohort_dict)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "not authorised" in response.json()["detail"].lower()

    def test_save_then_aggregate_round_trip(self, client, session, sample_cohort_dict: dict):
        """Full happy path: insert a Queries row + point payload at the seeded Trust_1."""
        seeded = session.exec(select(Trust).where(Trust.name == "Trust_1")).first()
        assert seeded is not None, "Trust_1 must be present (seeded once per session)"

        query_row = Queries(name="cohort-endpoint-roundtrip", query="SELECT 1")
        session.add(query_row)
        session.commit()
        session.refresh(query_row)

        sample_cohort_dict["trust_id"] = str(seeded.id)
        sample_cohort_dict["query_id"] = str(query_row.id)

        response = client.post("/api/cohort/results", json=sample_cohort_dict)
        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json() == {"message": "Cohort results processed successfully"}
