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

import json
import uuid
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from flip_api.auth.access_manager import authenticate_trust
from flip_api.db.models.main_models import Trust
from flip_api.domain.schemas.private import (
    AggregatedCohortStats,
    AggregatedFieldResult,
    AggregatedTrustFieldResult,
    FetchedAggregationData,
    OmopCohortResults,
    OmopData,
    Results,
    TrustSpecificData,
)
from flip_api.main import app
from flip_api.private_services.receive_cohort_results import (
    _aggregate_and_save_results,
    _save_individual_result,
)


@pytest.fixture
def sample_omop_data_dict():
    return {
        "name": "age_group",
        "results": [
            {"value": "<50", "count": 5},
            {"value": ">=50", "count": 5},
        ],
    }


@pytest.fixture
def sample_cohort_dict(sample_omop_data_dict):
    return {
        "query_id": str(uuid.uuid4()),
        "trust_id": str(uuid.uuid4()),
        "created": "2023-10-01T12:00:00Z",
        "record_count": 10,
        "data": [sample_omop_data_dict],
    }


@pytest.fixture
def sample_cohort_payload(sample_cohort_dict):
    return OmopCohortResults(**sample_cohort_dict)


class TestPydanticModels:
    def test_omop_data_creation(self, sample_omop_data_dict):
        data = OmopData(**sample_omop_data_dict)
        assert data.name == sample_omop_data_dict["name"]
        assert [r.model_dump() for r in data.results] == sample_omop_data_dict["results"]

    def test_cohort_results_payload_creation(self, sample_cohort_dict):
        payload = OmopCohortResults(**sample_cohort_dict)
        assert str(payload.query_id) == sample_cohort_dict["query_id"]
        assert str(payload.trust_id) == sample_cohort_dict["trust_id"]
        assert payload.record_count == sample_cohort_dict["record_count"]
        assert len(payload.data) == 1
        assert payload.data[0].name == sample_cohort_dict["data"][0]["name"]

    def test_cohort_results_payload_data_validator_none(self, sample_cohort_dict):
        sample_cohort_dict["record_count"] = 0
        sample_cohort_dict["data"] = None
        payload = OmopCohortResults(**sample_cohort_dict)
        assert payload.data == []

    def test_cohort_results_payload_data_validator_empty_list(self, sample_cohort_dict):
        sample_cohort_dict["record_count"] = 0
        sample_cohort_dict["data"] = []
        payload = OmopCohortResults(**sample_cohort_dict)
        assert payload.data == []

    def test_trust_specific_data_creation(self, sample_omop_data_dict):
        data = TrustSpecificData(record_count=5, data=[OmopData(**sample_omop_data_dict)])
        assert data.record_count == 5
        assert len(data.data) == 1

    def test_aggregated_trust_field_result_creation(self):
        res = AggregatedTrustFieldResult(data={"<50": 2}, trust_name="Trust X", trust_id="trustX_id")
        assert res.trust_name == "Trust X"

    def test_aggregated_field_result_creation(self):
        agg_res = AggregatedFieldResult(name="age_group", results=[])
        assert agg_res.name == "age_group"

    def test_aggregated_cohort_stats_creation(self):
        stats = AggregatedCohortStats(record_count=100, trusts_results=[], trust_record_counts={})
        assert stats.record_count == 100
        assert stats.trust_record_counts == {}

    def test_aggregated_cohort_stats_carries_per_trust_counts(self):
        """trust_record_counts lets the UI distinguish "responded with 0"
        from "never responded" — without it a privacy-suppressed 0-record
        trust appears identical to a still-running one."""
        stats = AggregatedCohortStats(
            record_count=5,
            trusts_results=[],
            trust_record_counts={"trustA": 5, "trustB": 0},
        )
        assert stats.trust_record_counts == {"trustA": 5, "trustB": 0}

    def test_aggregated_cohort_stats_carries_trust_errors(self):
        stats = AggregatedCohortStats(
            record_count=5,
            trusts_results=[],
            trust_record_counts={"trustA": 5},
            trust_errors={"trustB": "Invalid SQL"},
        )
        assert stats.trust_errors == {"trustB": "Invalid SQL"}

    def test_omop_cohort_results_accepts_error_field(self, sample_cohort_dict):
        """Trusts must be able to report a failed cohort query so the hub can
        track per-trust error state and the UI can show a red chip."""
        sample_cohort_dict["record_count"] = 0
        sample_cohort_dict["data"] = []
        sample_cohort_dict["error"] = "Database connection failed"
        payload = OmopCohortResults(**sample_cohort_dict)
        assert payload.error == "Database connection failed"

    def test_omop_cohort_results_error_defaults_to_none(self, sample_cohort_dict):
        payload = OmopCohortResults(**sample_cohort_dict)
        assert payload.error is None

    def test_fetched_aggregation_data_creation(self):
        fetched = FetchedAggregationData(trust_name=["Trust X"], trust_id=["idX"], data=['{"key": "value"}'])
        assert fetched.trust_name == ["Trust X"]


class TestSaveIndividualResult:
    def test_save_general_db_exception(self, mock_db_session: MagicMock, sample_cohort_payload: OmopCohortResults):
        mock_db_session.exec.side_effect = Exception("Some generic DB error")

        with pytest.raises(HTTPException) as exc_info:
            _save_individual_result(mock_db_session, sample_cohort_payload)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Error saving cohort results" in exc_info.value.detail
        mock_db_session.commit.assert_not_called()
        mock_db_session.rollback.assert_called_once()


class TestAggregateAndSaveResults:
    @pytest.fixture
    def query_id_for_agg(self):
        return str(uuid.uuid4())

    @pytest.fixture
    def mock_aggregation_select_success(self, mock_db_session: MagicMock):
        trust_a_data_str = TrustSpecificData(
            record_count=2, data=[OmopData(name="age", results=[Results(value="<50", count=2)])]
        ).model_dump_json()
        trust_b_data_str = TrustSpecificData(
            record_count=3, data=[OmopData(name="age", results=[Results(value="<50", count=3)])]
        ).model_dump_json()

        mock_select_result = [
            ("Trust A Name", uuid.uuid4(), trust_a_data_str),
            ("Trust B Name", uuid.uuid4(), trust_b_data_str),
        ]
        return mock_select_result

    def test_aggregate_success(
        self, mock_db_session: MagicMock, query_id_for_agg: UUID, mock_aggregation_select_success: MagicMock
    ):
        mock_insert_stats_result = MagicMock()
        mock_insert_stats_result.all.return_value.count = 1

        mock_db_session.exec.return_value.all.return_value = mock_aggregation_select_success

        _aggregate_and_save_results(mock_db_session, query_id_for_agg)

        assert mock_db_session.exec.call_count == 2

        select_call_args = mock_db_session.exec.call_args_list[0]
        assert "SELECT trust.name, query_result.trust_id, query_result.data" in str(select_call_args[0][0])

        mock_db_session.commit.assert_called_once()
        mock_db_session.rollback.assert_not_called()

    def test_aggregate_no_data_found_for_query(self, mock_db_session: MagicMock, query_id_for_agg: UUID):
        mock_db_session.exec.return_value.all.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            _aggregate_and_save_results(mock_db_session, query_id_for_agg)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert f"No results found in database for query_id {query_id_for_agg} to aggregate" in exc_info.value.detail
        mock_db_session.commit.assert_not_called()
        mock_db_session.rollback.assert_called_once()

    def test_aggregate_fail_to_save_stats(
        self, mock_db_session: MagicMock, query_id_for_agg: UUID, mock_aggregation_select_success: MagicMock
    ):
        mock_db_session.exec.return_value.all.return_value = mock_aggregation_select_success
        mock_db_session.commit.side_effect = Exception("Failed to save aggregated stats")

        with pytest.raises(HTTPException) as exc_info:
            _aggregate_and_save_results(mock_db_session, query_id_for_agg)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Failed to save aggregated stats" in exc_info.value.detail
        mock_db_session.commit.assert_called_once()
        mock_db_session.rollback.assert_called_once()

    def test_aggregate_db_exception_on_select(self, mock_db_session: MagicMock, query_id_for_agg: UUID):
        mock_db_session.exec.side_effect = Exception("DB error on SELECT")

        with pytest.raises(HTTPException) as exc_info:
            _aggregate_and_save_results(mock_db_session, query_id_for_agg)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "DB error on SELECT" in exc_info.value.detail
        mock_db_session.rollback.assert_called_once()

    def test_aggregate_includes_per_trust_record_counts(
        self, mock_db_session: MagicMock, query_id_for_agg: UUID
    ):
        """The saved QueryStats JSON must include ``trust_record_counts`` so the
        UI can show "0" for trusts whose count was privacy-suppressed instead of
        leaving them stuck on "running"."""
        trust_a_id = uuid.uuid4()
        trust_b_id = uuid.uuid4()
        trust_c_id = uuid.uuid4()

        trust_a_data = TrustSpecificData(
            record_count=7, data=[OmopData(name="age", results=[Results(value="<50", count=7)])]
        ).model_dump_json()
        trust_b_data = TrustSpecificData(
            record_count=4, data=[OmopData(name="age", results=[Results(value="<50", count=4)])]
        ).model_dump_json()
        trust_c_data = TrustSpecificData(record_count=0, data=[]).model_dump_json()

        rows = [
            ("Trust A", trust_a_id, trust_a_data),
            ("Trust B", trust_b_id, trust_b_data),
            ("Trust C", trust_c_id, trust_c_data),
        ]

        existing_stats_mock = MagicMock()
        mock_db_session.exec.return_value.all.return_value = rows
        mock_db_session.exec.return_value.first.return_value = existing_stats_mock

        _aggregate_and_save_results(mock_db_session, query_id_for_agg)

        saved = json.loads(existing_stats_mock.stats)
        assert saved["record_count"] == 11
        assert saved["trust_record_counts"] == {
            str(trust_a_id): 7,
            str(trust_b_id): 4,
            str(trust_c_id): 0,
        }

    def test_aggregate_surfaces_trust_errors(
        self, mock_db_session: MagicMock, query_id_for_agg: UUID
    ):
        """Errored trusts go into ``trust_errors`` (not ``trust_record_counts``)
        so the UI can show a red "error" chip instead of "running"."""
        trust_a_id = uuid.uuid4()
        trust_b_id = uuid.uuid4()

        trust_a_data = TrustSpecificData(
            record_count=5,
            data=[OmopData(name="age", results=[Results(value="<50", count=5)])],
        ).model_dump_json()
        trust_b_data = TrustSpecificData(
            record_count=0, data=[], error="Database connection failed"
        ).model_dump_json()

        rows = [
            ("Trust A", trust_a_id, trust_a_data),
            ("Trust B", trust_b_id, trust_b_data),
        ]

        existing_stats_mock = MagicMock()
        mock_db_session.exec.return_value.all.return_value = rows
        mock_db_session.exec.return_value.first.return_value = existing_stats_mock

        _aggregate_and_save_results(mock_db_session, query_id_for_agg)

        saved = json.loads(existing_stats_mock.stats)
        # Errored trust must not contribute to the aggregate record count and
        # must not appear in trust_record_counts (otherwise UI would render "0").
        assert saved["record_count"] == 5
        assert saved["trust_record_counts"] == {str(trust_a_id): 5}
        assert saved["trust_errors"] == {str(trust_b_id): "Database connection failed"}


class TestReceiveCohortResultsEndpointAuth:
    """Endpoint-level test for the trust ownership check in receive_cohort_results_endpoint.

    ``authenticate_trust`` returns the resolved ``Trust`` row, so the endpoint
    compares ``authenticated_trust.id`` against the payload's ``trust_id``
    directly — no DB lookup, no "trust name not found" path.
    """

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_returns_403_when_trust_id_does_not_match_authenticated_trust(self, client):
        """POST with a trust_id that belongs to a different trust should be rejected."""
        authenticated = Trust(id=uuid.uuid4(), name="Authed Trust")
        other_trust_id = uuid.uuid4()
        app.dependency_overrides[authenticate_trust] = lambda: authenticated

        payload = {
            "query_id": str(uuid.uuid4()),
            "trust_id": str(other_trust_id),
            "created": "2023-10-01T12:00:00Z",
            "record_count": 5,
            "data": [],
        }
        try:
            response = client.post("/api/cohort/results", json=payload)
            assert response.status_code == 403
            assert "not authorised" in response.json()["detail"]
        finally:
            app.dependency_overrides.pop(authenticate_trust, None)
