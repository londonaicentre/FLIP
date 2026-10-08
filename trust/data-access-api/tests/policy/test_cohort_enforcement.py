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
import pytest
from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from fastapi.testclient import TestClient

from data_access_api.main import app
from data_access_api.policy import parse_policy
from data_access_api.routers.cohort import _BELOW_THRESHOLD_DETAIL
from data_access_api.routers.schema import StatisticsResponse
from data_access_api.services.cohort_snapshot import Snapshot
from tests.conftest import AUTH_HEADERS, WRITE_AUTH_HEADERS

client = TestClient(app)

# Matches the fixed text the routes answer for a below-threshold cohort. Asserted against
# a policy denial too: the two must be indistinguishable to the caller.
_FIXED_REFUSAL = {"detail": _BELOW_THRESHOLD_DETAIL}


# Project ids are UUIDs: the hub seals str(uuid.UUID), and the loader rejects anything else.
P_DENIED = "d0000000-0000-4000-8000-000000000001"
P_ALLOWED = "a0000000-0000-4000-8000-000000000002"
P_OTHER = "0e000000-0000-4000-8000-000000000003"


def _policy(text: str, floor: int = 5):
    return parse_policy(text, floor=floor, source="test")


def _membership(person_ids: list[str] | None = None, accession_ids: list[str] | None = None) -> Snapshot:
    """An approved cohort's frozen membership (FLIP#857): the row-level routes serve only this."""
    return Snapshot(
        query="SELECT 1",
        query_hash="0" * 64,
        person_ids=person_ids,
        accession_ids=accession_ids,
        row_count=100,
        subject_count=100,
        columns=[],
        created_at="2026-09-29T00:00:00+00:00",
    )


def _large_cohort() -> pd.DataFrame:
    """A frame comfortably above any threshold used here, so only policy can refuse it."""
    return pd.DataFrame({"person_id": list(range(100)), "value": list(range(100))})


@patch("data_access_api.routers.cohort.decrypt", return_value=P_DENIED)
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


@patch("data_access_api.routers.cohort.decrypt", return_value=P_DENIED)
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


@patch("data_access_api.routers.cohort.decrypt", return_value=P_ALLOWED)
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query", return_value="SELECT 1")
@patch("data_access_api.routers.cohort.count_distinct_subjects", return_value=30)
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_snapshot", return_value=_membership(person_ids=[str(i) for i in range(100)]))
def test_dataframe_rule_threshold_raise_refuses_a_cohort_that_clears_the_kit_floor(
    mock_get_snapshot, mock_get_records, mock_count, mock_validate, mock_get_settings, mock_get_policy, mock_decrypt
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


@patch("data_access_api.routers.cohort.decrypt", return_value=P_ALLOWED)
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query", return_value="SELECT 1")
@patch("data_access_api.routers.cohort.count_distinct_subjects", return_value=30)
@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_snapshot", return_value=_membership(person_ids=[str(i) for i in range(100)]))
def test_dataframe_permitted_by_policy_returns_data(
    mock_get_snapshot, mock_get_records, mock_count, mock_validate, mock_get_settings, mock_get_policy, mock_decrypt
):
    """The permit path still returns rows — the gate adds a condition, it does not block."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy(
        """
        [[access.rule]]
        id = "allow-this-project"
        action = "cohort.dataframe"
        effect = "permit"
        projects = ["a0000000-0000-4000-8000-000000000002"]
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
def test_statistics_policy_denial_is_suppressed_not_errored(mock_get_records, mock_get_settings, mock_get_policy):
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
@patch("data_access_api.routers.cohort.decrypt", return_value=P_ALLOWED)
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
        projects = ["0e000000-0000-4000-8000-000000000003"]
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

    # P_ALLOWED is not in the allowlist, so the request falls to the default deny.
    assert response.status_code == 200
    assert response.json()["suppressed"] is True
    mock_decrypt.assert_called_once()
    mock_get_records.assert_not_called()


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt", return_value=P_DENIED)
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
        projects = ["0e000000-0000-4000-8000-000000000003"]
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
    assert f"Policy denied cohort statistics for project {P_DENIED}" in caplog.text
    assert "<none>" not in caplog.text


# ── Validation before the decision (review item 9) ───────────────────────────────────────
# decide() used to run before validate_query, so an invalid query got a 400 when permitted
# but a 403 (or a suppressed 200 on /cohort) when a rule denied it: a deterministic probe for
# "a rule covers this project". The query's shape is now judged first, whatever the policy.
# Only /cohort takes its SQL from the caller: the row-level routes run the query of record
# frozen at approval (FLIP#857) and never judge the caller's, so the probe does not exist there.

_DENY_ALL = """
[[access.rule]]
id = "deny-{action}"
action = "{action}"
effect = "deny"
"""


@pytest.mark.parametrize(
    ("path", "action", "payload"),
    [
        (
            "/cohort",
            "cohort.statistics",
            {"encrypted_project_id": "sealed", "query_id": "q1", "query_name": "q", "query": "x", "trust_id": "t1"},
        ),
    ],
)
@patch("data_access_api.routers.cohort.decrypt", return_value=P_DENIED)
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.get_records")
def test_an_invalid_query_is_refused_as_invalid_whether_or_not_a_rule_denies(
    mock_get_records, mock_get_settings, mock_get_policy, mock_decrypt, path, action, payload
):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    invalid = HTTPException(status_code=400, detail="Only SELECT statements are allowed.")

    responses = []
    for policy in (None, _policy(_DENY_ALL.format(action=action))):
        mock_get_policy.return_value = policy
        with patch("data_access_api.routers.cohort.validate_query", side_effect=invalid):
            responses.append(client.post(path, json=payload, headers=AUTH_HEADERS))

    assert [r.status_code for r in responses] == [400, 400]
    assert responses[0].json() == responses[1].json()
    mock_get_records.assert_not_called()


# ── The policy threshold on every route (review item 14) ─────────────────────────────────
# Only /cohort/dataframe asserted the policy threshold, so reverting either of the other two
# routes to COHORT_QUERY_THRESHOLD passed the suite.


@patch("data_access_api.routers.cohort.get_statistics")
@patch("data_access_api.routers.cohort.validate_query", return_value="SELECT 1")
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.get_records")
def test_statistics_applies_the_policy_threshold(
    mock_get_records, mock_get_settings, mock_get_policy, mock_validate, mock_get_statistics
):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy("[disclosure]\nmin_cohort_size = 30")
    mock_get_records.return_value = _large_cohort()
    mock_get_statistics.return_value = StatisticsResponse(
        query_id="q1", trust_id="t1", record_count=0, created="2026-09-29", data=[], suppressed=True
    )

    response = client.post(
        "/cohort",
        json={"encrypted_project_id": "sealed", "query_id": "q1", "query_name": "q", "query": "x", "trust_id": "t1"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert mock_get_statistics.call_args.kwargs["threshold"] == 30


@patch("data_access_api.routers.cohort.decrypt", return_value=P_ALLOWED)
@patch("data_access_api.routers.cohort.get_snapshot")
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
def test_accession_ids_applies_the_policy_threshold(
    mock_get_settings, mock_get_policy, mock_get_snapshot, mock_decrypt
):
    """20 subjects clear the kit's 5 but not the document's 30."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_snapshot.return_value = _membership(accession_ids=[f"ACC{i}" for i in range(20)])

    def lookup(query=None, params=None, **kwargs):
        ids = (params or {}).get("accession_ids", [])
        if "COUNT(DISTINCT" in str(query):
            return pd.DataFrame({"subject_count": [len(set(ids))]})
        return pd.DataFrame({"accession_id": list(dict.fromkeys(ids))})

    with patch("data_access_api.services.cohort.get_records", side_effect=lookup):
        mock_get_policy.return_value = None
        permitted = client.post(
            "/cohort/accession-ids", json={"encrypted_project_id": "sealed", "query": "x"}, headers=AUTH_HEADERS
        )
        mock_get_policy.return_value = _policy("[disclosure]\nmin_cohort_size = 30")
        raised = client.post(
            "/cohort/accession-ids", json={"encrypted_project_id": "sealed", "query": "x"}, headers=AUTH_HEADERS
        )

    assert permitted.status_code == 200
    assert raised.status_code == 403
    assert raised.json() == _FIXED_REFUSAL


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.decrypt", side_effect=InvalidTag())
def test_statistics_refuses_an_envelope_it_cannot_open_when_a_rule_needs_the_project(
    mock_decrypt, mock_get_settings, mock_get_policy, mock_get_records
):
    """Reading an unopenable envelope as "no project" would let a request slip past a
    project-scoped deny to a trust-wide permit. It is the caller's 400 instead."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy(
        f"""
        [[access.rule]]
        id = "withdrawn"
        action = "cohort.statistics"
        effect = "deny"
        projects = ["{P_DENIED}"]

        [[access.rule]]
        id = "everyone-else"
        action = "cohort.statistics"
        effect = "permit"
        """
    )

    response = client.post(
        "/cohort",
        json={
            "encrypted_project_id": "tampered",
            "query_id": "q1",
            "query_name": "q",
            "query": "SELECT 1",
            "trust_id": "t1",
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert "failed authentication" in response.json()["detail"]
    mock_get_records.assert_not_called()


@patch("data_access_api.routers.cohort.snapshot_enabled", return_value=True)
@patch("data_access_api.routers.cohort.decrypt", return_value=P_DENIED)
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.load_snapshot")
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_snapshot_policy_denial_freezes_nothing_and_uses_the_fixed_refusal_text(
    mock_get_records,
    mock_save_snapshot,
    mock_load_snapshot,
    mock_get_settings,
    mock_get_policy,
    mock_decrypt,
    mock_enabled,
    caplog,
):
    """The freeze reports a project's counts to the hub, so a project denied statistics is not frozen."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy(
        """
        [[access.rule]]
        id = "no-counts"
        action = "cohort.statistics"
        effect = "deny"
        """
    )

    with caplog.at_level("WARNING"):
        response = client.post(
            "/cohort/snapshot", json={"encrypted_project_id": "sealed", "query": "SELECT 1"}, headers=WRITE_AUTH_HEADERS
        )

    assert response.status_code == 403
    assert response.json() == _FIXED_REFUSAL
    assert "no-counts" in caplog.text
    assert "no-counts" not in response.text
    # Denied before anything is read or run: an existing record's facts are not disclosed either.
    mock_load_snapshot.assert_not_called()
    mock_get_records.assert_not_called()
    mock_save_snapshot.assert_not_called()


@patch("data_access_api.routers.cohort.snapshot_enabled", return_value=True)
@patch("data_access_api.routers.cohort.decrypt", return_value=P_ALLOWED)
@patch("data_access_api.routers.cohort.get_policy")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query", return_value="SELECT 1")
@patch("data_access_api.routers.cohort.count_distinct_subjects", return_value=20)
@patch("data_access_api.routers.cohort.load_snapshot", return_value=None)
@patch("data_access_api.routers.cohort.save_snapshot")
@patch("data_access_api.routers.cohort.get_records")
def test_snapshot_applies_the_policy_threshold(
    mock_get_records,
    mock_save_snapshot,
    mock_load_snapshot,
    mock_count,
    mock_validate,
    mock_get_settings,
    mock_get_policy,
    mock_decrypt,
    mock_enabled,
):
    """20 subjects clear the kit's 5 but not the document's 30: nothing is frozen, so no count reaches the hub."""
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_policy.return_value = _policy("[disclosure]\nmin_cohort_size = 30")
    mock_get_records.return_value = _large_cohort()

    response = client.post(
        "/cohort/snapshot", json={"encrypted_project_id": "sealed", "query": "SELECT 1"}, headers=WRITE_AUTH_HEADERS
    )

    assert response.status_code == 403
    assert response.json() == _FIXED_REFUSAL
    mock_save_snapshot.assert_not_called()
