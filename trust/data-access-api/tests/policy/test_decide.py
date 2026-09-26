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

"""Unit tests for the governance policy evaluator (FLIP#1259).

``decide`` is a pure function, so these pin behaviour without standing up the service.
The two that matter most are the unset path (must reproduce today's behaviour exactly)
and the monotonic threshold (policy may tighten, never weaken).
"""

import pytest

from data_access_api.policy import (
    ACTION_COHORT_ACCESSION_IDS,
    ACTION_COHORT_DATAFRAME,
    ACTION_COHORT_STATISTICS,
    decide,
    parse_policy,
)

FLOOR = 10


def _policy(text: str, floor: int = FLOOR):
    return parse_policy(text, floor=floor, source="test")


def test_no_policy_permits_at_configured_threshold():
    """The unconfigured path: permit, at exactly the configured floor (AC 4)."""
    decision = decide({}, {"project_id": "p1"}, ACTION_COHORT_DATAFRAME, policy=None, configured_threshold=FLOOR)

    assert decision.permit is True
    assert decision.effective_threshold == FLOOR
    assert decision.rule_id == "default.unset"


def test_unmentioned_action_falls_through_to_existing_behaviour():
    """A policy silent about an action must not lock the trust out of it.

    This is the one deliberate non-fail-closed choice in the design; it is what makes
    partial adoption possible. Pinned so nobody 'fixes' it into a default deny without
    also rewriting the operator story.
    """
    policy = _policy(
        """
        [[access.rule]]
        id = "r1"
        action = "cohort.dataframe"
        effect = "deny"
        """
    )

    decision = decide({}, {"project_id": "p1"}, ACTION_COHORT_STATISTICS, policy=policy, configured_threshold=FLOOR)

    assert decision.permit is True
    assert decision.rule_id == "default.unmentioned"


def test_mentioned_action_with_no_match_is_denied():
    """Once a trust writes rules for an action, an unmatched request is unauthorised."""
    policy = _policy(
        """
        [[access.rule]]
        id = "allowlist"
        action = "cohort.dataframe"
        effect = "permit"
        projects = ["p1"]
        """
    )

    decision = decide({}, {"project_id": "p2"}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)

    assert decision.permit is False
    assert decision.rule_id == "default.deny"


def test_deny_rule_denies_and_is_attributable():
    """A denial carries the rule id that caused it (AC 6)."""
    policy = _policy(
        """
        [[access.rule]]
        id = "no-raw-export"
        action = "cohort.dataframe"
        effect = "deny"
        """
    )

    decision = decide({}, {"project_id": "p1"}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)

    assert decision.permit is False
    assert decision.rule_id == "policy:no-raw-export"
    assert "no-raw-export" in decision.reason


def test_first_matching_rule_wins():
    """Document order decides, so an operator can put a narrow exception above a broad deny."""
    policy = _policy(
        """
        [[access.rule]]
        id = "exception"
        action = "cohort.dataframe"
        effect = "permit"
        projects = ["vip"]

        [[access.rule]]
        id = "broad-deny"
        action = "cohort.dataframe"
        effect = "deny"
        """
    )

    permitted = decide({}, {"project_id": "vip"}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)
    denied = decide({}, {"project_id": "other"}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)

    assert permitted.permit is True
    assert permitted.rule_id == "policy:exception"
    assert denied.permit is False
    assert denied.rule_id == "policy:broad-deny"


def test_project_scoped_rule_does_not_match_a_request_without_a_project():
    """/cohort carries no project id, so a project-scoped rule can never match there."""
    policy = _policy(
        """
        [[access.rule]]
        id = "scoped"
        action = "cohort.statistics"
        effect = "permit"
        projects = ["p1"]
        """
    )

    decision = decide({}, {"project_id": None}, ACTION_COHORT_STATISTICS, policy=policy, configured_threshold=FLOOR)

    assert decision.permit is False
    assert decision.rule_id == "default.deny"


def test_section_threshold_raises_the_floor():
    policy = _policy("[disclosure]\nmin_cohort_size = 25")

    decision = decide({}, {"project_id": "p1"}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)

    assert decision.effective_threshold == 25


def test_rule_threshold_raises_above_the_section_threshold():
    policy = _policy(
        """
        [disclosure]
        min_cohort_size = 25

        [[access.rule]]
        id = "strict-imaging"
        action = "cohort.accession_ids"
        effect = "permit"
        min_cohort_size = 50
        """
    )

    decision = decide(
        {}, {"project_id": "p1"}, ACTION_COHORT_ACCESSION_IDS, policy=policy, configured_threshold=FLOOR
    )

    assert decision.permit is True
    assert decision.effective_threshold == 50


def test_effective_threshold_never_drops_below_the_configured_value():
    """The monotonic invariant (#870), asserted against a floor raised after load.

    The loader already rejects a document below the floor, but the evaluator applies
    max() independently — two guards, because this is the property that makes an
    optional policy safe to ship.
    """
    policy = _policy("[disclosure]\nmin_cohort_size = 20", floor=10)

    decision = decide({}, {"project_id": "p1"}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=50)

    assert decision.effective_threshold == 50


def test_two_documents_produce_different_decisions_from_the_same_binary():
    """AC 2: rules are data. Changing a rule needs no code change."""
    permissive = _policy(
        """
        [[access.rule]]
        id = "r"
        action = "cohort.dataframe"
        effect = "permit"
        """
    )
    restrictive = _policy(
        """
        [[access.rule]]
        id = "r"
        action = "cohort.dataframe"
        effect = "deny"
        """
    )

    args = ({}, {"project_id": "p1"}, ACTION_COHORT_DATAFRAME)
    assert decide(*args, policy=permissive, configured_threshold=FLOOR).permit is True
    assert decide(*args, policy=restrictive, configured_threshold=FLOOR).permit is False


def test_unknown_action_raises():
    """A typo'd call site must fail loudly rather than evaluate to permit."""
    with pytest.raises(ValueError, match="unknown action"):
        decide({}, {}, "cohort.everything", policy=None, configured_threshold=FLOOR)
