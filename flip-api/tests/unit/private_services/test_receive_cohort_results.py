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

import uuid
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException, status

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
from flip.private_services.receive_cohort_results import (
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
        stats = AggregatedCohortStats(record_count=100, trusts_results=[])
        assert stats.record_count == 100

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
        mock_db_session.execute.return_value.all = lambda: [mock_insert_stats_result]
        mock_insert_stats_result.all.return_value.count = 1

        mock_db_session.execute.side_effect = [
            mock_aggregation_select_success,
            mock_insert_stats_result,
        ]

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
