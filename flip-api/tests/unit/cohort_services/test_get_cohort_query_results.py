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
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

# Assuming the models are imported like this
from flip_api.cohort_services.get_cohort_query_results import get_cohort_query_results

# Constants for testing
TEST_QUERY_ID = "query-123"
TEST_USER_ID = "user-abc"

# Mock Data Structure returned from the database exec call
MOCK_STATS = {
    "record_count": 100,
    "trusts_results": [
        {
            "name": "Result A",  # Correct field name
            "results": [  # Correct field name
                {
                    "trust_name": "Trust A",  # Correct field name
                    "trust_id": "trust-1",
                    "data": [  # Correct field name
                        {"value": "some value", "count": 50},
                        {"value": "another value", "count": 50},
                    ],
                }
            ],
        },
        {
            "name": "Result B",  # Correct field name
            "results": [  # Correct field name
                {
                    "trust_name": "Trust B",  # Correct field name
                    "trust_id": "trust-2",
                    "data": [  # Correct field name
                        {"value": "yet another value", "count": 30},
                        {"value": "more data", "count": 70},
                    ],
                }
            ],
        },
    ],
}


@patch("flip_api.cohort_services.get_cohort_query_results.can_access_cohort_query", return_value=True)
def test_get_cohort_results_success(mock_access):
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = json.dumps(MOCK_STATS)

    result = get_cohort_query_results(query_id=TEST_QUERY_ID, db=mock_db, user_id=TEST_USER_ID)

    # Check the response type
    assert result.record_count == 100
    assert len(result.trusts_results) == 2

    # Check trust 1
    trust_a = result.trusts_results[0]
    assert trust_a.name == "Result A"  # Adjusted for name
    assert len(trust_a.results) == 1
    assert trust_a.results[0].trust_name == "Trust A"
    assert trust_a.results[0].trust_id == "trust-1"
    assert len(trust_a.results[0].data) == 2
    assert trust_a.results[0].data[0].value == "some value"
    assert trust_a.results[0].data[0].count == 50

    # Check trust 2
    trust_b = result.trusts_results[1]
    assert trust_b.name == "Result B"  # Adjusted for name
    assert len(trust_b.results) == 1
    assert trust_b.results[0].trust_name == "Trust B"
    assert trust_b.results[0].trust_id == "trust-2"
    assert len(trust_b.results[0].data) == 2
    assert trust_b.results[0].data[0].value == "yet another value"
    assert trust_b.results[0].data[0].count == 30


@patch("flip_api.cohort_services.get_cohort_query_results.can_access_cohort_query", return_value=True)
def test_get_cohort_results_not_found(mock_access):
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        get_cohort_query_results(query_id=TEST_QUERY_ID, db=mock_db, user_id=TEST_USER_ID)

    assert exc_info.value.status_code == 404
    assert "No results returned from the database" in str(exc_info.value.detail)


@patch("flip_api.cohort_services.get_cohort_query_results.can_access_cohort_query", return_value=False)
def test_get_cohort_results_forbidden(mock_access):
    mock_db = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        get_cohort_query_results(query_id=TEST_QUERY_ID, db=mock_db, user_id=TEST_USER_ID)

    assert exc_info.value.status_code == 403
    assert "denied access" in str(exc_info.value.detail)


@patch("flip_api.cohort_services.get_cohort_query_results.can_access_cohort_query", return_value=True)
def test_get_cohort_results_internal_error(mock_access):
    mock_db = MagicMock()
    mock_db.exec.side_effect = Exception("Unexpected DB error")

    with pytest.raises(HTTPException) as exc_info:
        get_cohort_query_results(query_id=TEST_QUERY_ID, db=mock_db, user_id=TEST_USER_ID)

    assert exc_info.value.status_code == 500
    assert "Internal server error" in str(exc_info.value.detail)
