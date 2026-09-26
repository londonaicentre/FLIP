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
#

"""Policy enforcement at the cohort routes (FLIP#1259).

The loader and evaluator are unit-tested separately; these pin the wiring, and above all
the two properties that must not regress:

* the refusal text never names the rule (it would become a configuration probe, and on
  the row-level routes a row-count oracle);
* the denial IS attributable in the log.
"""

from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from data_access_api.main import app
from data_access_api.policy import parse_policy
from data_access_api.routers.cohort import _BELOW_THRESHOLD_DETAIL
from tests.conftest import AUTH_HEADERS

client = TestClient(app)

# Matches the fixed text the routes answer for a below-threshold cohort. Asserted against
# a policy denial too: the two must be indistinguishable to the caller.
_FIXED_REFUSAL = {"detail": _BELOW_THRESHOLD_DETAIL}


def _policy(text: str, floor: int = 5):
    return parse_policy(text, floor=floor, source="test")


def _large_cohort() -> pd.DataFrame:
    """A frame comfortably above any threshold used here, so only policy can refuse it."""
    return pd.DataFrame({"person_id": list(range(100)), "value": list(range(100))})


@patch("data_access_api.routers.cohort.decrypt", return_value="p-denied")
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_policy_denial_uses_the_fixed_refusal_text(
    mock_get_records, mock_get_settings, mock_get_policy, mock_decrypt, caplog
):
    """A policy denial is byte-identical to a below-threshold refusal, and is logged with its rule."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy(
        """
        [[access.rule]]
        id = "no-raw-export"
        action = "cohort.dataframe"
        effect = "deny"
        """
    )
    mock_get_records.return_value = _large_cohort()

    with caplog.at_level("WARNING"):
        response = client.post(
        "/cohort/dataframe", json={"encrypted_project_id": "sealed", "query": "SELECT 1"}, headers=AUTH_HEADERS
    )

    assert response.status_code == 403
    assert response.json() == _FIXED_REFUSAL
    # The rule name is in the log...
    assert "no-raw-export" in caplog.text
    # ...and never on the wire.
    assert "no-raw-export" not in response.text
    # Denied before the query ran: a denied project's SQL must not touch OMOP.
    mock_get_records.assert_not_called()


@patch("data_access_api.routers.cohort.decrypt", return_value="p-denied")
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.get_records")
def test_accession_ids_policy_denial_uses_the_fixed_refusal_text(
    mock_get_records, mock_get_settings, mock_get_policy, mock_decrypt
):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy(
        """
        [[access.rule]]
        id = "imaging-off"
        action = "cohort.accession_ids"
        effect = "deny"
        """
    )

    response = client.post(
        "/cohort/accession-ids", json={"encrypted_project_id": "sealed", "query": "SELECT 1"}, headers=AUTH_HEADERS
    )

    assert response.status_code == 403
    assert response.json() == _FIXED_REFUSAL
    assert "imaging-off" not in response.text
    mock_get_records.assert_not_called()


@patch("data_access_api.routers.cohort.decrypt", return_value="p-allowed")
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query", return_value="SELECT 1")
@patch("data_access_api.routers.cohort.count_distinct_subjects", return_value=30)
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_rule_threshold_raise_refuses_a_cohort_that_clears_the_kit_floor(
    mock_get_records, mock_count, mock_validate, mock_get_settings, mock_get_policy, mock_decrypt
):
    """A permit rule may raise the floor: 30 subjects clears the kit's 5 but not the rule's 50."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy(
        """
        [[access.rule]]
        id = "strict"
        action = "cohort.dataframe"
        effect = "permit"
        min_cohort_size = 50
        """
    )
    mock_get_records.return_value = _large_cohort()

    response = client.post(
        "/cohort/dataframe", json={"encrypted_project_id": "sealed", "query": "SELECT 1"}, headers=AUTH_HEADERS
    )

    assert response.status_code == 403
    assert response.json() == _FIXED_REFUSAL


@patch("data_access_api.routers.cohort.decrypt", return_value="p-allowed")
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query", return_value="SELECT 1")
@patch("data_access_api.routers.cohort.count_distinct_subjects", return_value=30)
@patch("data_access_api.routers.cohort.get_records")
def test_dataframe_permitted_by_policy_returns_data(
    mock_get_records, mock_count, mock_validate, mock_get_settings, mock_get_policy, mock_decrypt
):
    """The permit path still returns rows — the gate adds a condition, it does not block."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy(
        """
        [[access.rule]]
        id = "allow-this-project"
        action = "cohort.dataframe"
        effect = "permit"
        projects = ["p-allowed"]
        """
    )
    mock_get_records.return_value = pd.DataFrame({"person_id": [1, 2, 3]})

    response = client.post(
        "/cohort/dataframe", json={"encrypted_project_id": "sealed", "query": "SELECT 1"}, headers=AUTH_HEADERS
    )

    assert response.status_code == 200
    assert response.json() == {"person_id": [1, 2, 3]}


@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.get_records")
def test_statistics_policy_denial_is_suppressed_not_errored(
    mock_get_records, mock_get_settings, mock_get_policy
):
    """/cohort answers a denial as a suppressed zero-count response, never an HTTP error.

    An error would tell the caller a policy exists; the route's whole contract (issue #519)
    is that a shortfall is indistinguishable from a genuine zero.
    """
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy(
        """
        [[access.rule]]
        id = "stats-off"
        action = "cohort.statistics"
        effect = "deny"
        """
    )

    response = client.post(
        "/cohort",
        json={
            "encrypted_project_id": "sealed",
            "query_id": "q1",
            "query_name": "q",
            "query": "SELECT 1",
            "trust_id": "t1",
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["record_count"] == 0
    assert body["suppressed"] is True
    assert body["data"] == []
    assert "stats-off" not in response.text
    mock_get_records.assert_not_called()


@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt")
def test_statistics_does_not_open_the_envelope_when_no_rule_scopes_projects(
    mock_decrypt, mock_get_settings, mock_get_policy
):
    """/cohort must not gain a decrypt failure mode it never had (AC 4).

    The project id is unused on this route, so a trust-wide rule must decide without
    touching the envelope — otherwise every unconfigured trust inherits a new 400.
    """
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy(
        """
        [[access.rule]]
        id = "stats-off"
        action = "cohort.statistics"
        effect = "deny"
        """
    )

    response = client.post(
        "/cohort",
        json={
            "encrypted_project_id": "not-an-envelope",
            "query_id": "q1",
            "query_name": "q",
            "query": "SELECT 1",
            "trust_id": "t1",
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["suppressed"] is True
    mock_decrypt.assert_not_called()


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt", return_value="p-allowed")
def test_statistics_opens_the_envelope_when_a_rule_scopes_projects(
    mock_decrypt, mock_get_settings, mock_get_policy, mock_get_records
):
    """A project-scoped statistics rule must be able to match, which needs the real id."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy(
        """
        [[access.rule]]
        id = "stats-allowlist"
        action = "cohort.statistics"
        effect = "permit"
        projects = ["p-other"]
        """
    )

    response = client.post(
        "/cohort",
        json={
            "encrypted_project_id": "sealed",
            "query_id": "q1",
            "query_name": "q",
            "query": "SELECT 1",
            "trust_id": "t1",
        },
        headers=AUTH_HEADERS,
    )

    # p-allowed is not in the allowlist, so the request falls to the default deny.
    assert response.status_code == 200
    assert response.json()["suppressed"] is True
    mock_decrypt.assert_called_once()
    mock_get_records.assert_not_called()


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt", return_value="p-denied")
def test_statistics_denial_logs_the_project_it_blocked(
    mock_decrypt, mock_get_settings, mock_get_policy, mock_get_records, caplog
):
    """A statistics denial names the project in the log, as the row-level routes already do.

    This route suppresses rather than refuses, so the log is the only place the denial is
    visible at all — and an unattributable ``<none>`` line cannot be tied back to the
    project that was blocked, which is the first question an operator asks afterwards.
    """
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy(
        """
        [[access.rule]]
        id = "stats-allowlist"
        action = "cohort.statistics"
        effect = "permit"
        projects = ["p-other"]
        """
    )

    with caplog.at_level("WARNING"):
        response = client.post(
            "/cohort",
            json={
                "encrypted_project_id": "sealed",
                "query_id": "q1",
                "query_name": "q",
                "query": "SELECT 1",
                "trust_id": "t1",
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["suppressed"] is True
    assert "Policy denied cohort statistics for project p-denied" in caplog.text
    assert "<none>" not in caplog.text
