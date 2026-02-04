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

import pytest
from fastapi import status

from flip_api.config import get_settings
from flip.domain.schemas.private import OmopCohortResults


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


class TestReceiveCohortResultsEndpoint:
    def test_receive_results_bad_payload_validation_error(self, real_client):
        response = real_client.post(
            f"{get_settings().FLIP_API_URL}/cohort/results/",
            json={"trust_id": "abc", "record_count": "ten", "data": []},
            headers={get_settings().PRIVATE_API_KEY_HEADER: get_settings().PRIVATE_API_KEY},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_endpoint_fail_save_individual(self, real_client, sample_cohort_dict: dict):
        response = real_client.post(
            f"{get_settings().FLIP_API_URL}/cohort/results/",
            json=sample_cohort_dict,
            headers={get_settings().PRIVATE_API_KEY_HEADER: get_settings().PRIVATE_API_KEY},
        )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.json()["detail"] == f"Error saving cohort results: {sample_cohort_dict['query_id']}"
