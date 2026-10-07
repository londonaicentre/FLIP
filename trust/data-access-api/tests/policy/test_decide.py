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

# Project ids are UUIDs: the hub seals str(uuid.UUID), and the loader rejects anything else.
P1 = "3f1c9a70-5e42-4d8b-9c31-7a2e6b4f8d15"
P2 = "9b7e2d48-1a36-4c92-8f05-6d3b1e7a4c28"
VIP = "c0ffee00-0000-4000-8000-000000000001"


def _policy(text: str, floor: int = FLOOR):
    return parse_policy(text, floor=floor, source="test")


def test_no_policy_permits_at_configured_threshold():
    """The unconfigured path: permit, at exactly the configured floor (AC 4)."""
    decision = decide({}, {"project_id": P1}, ACTION_COHORT_DATAFRAME, policy=None, configured_threshold=FLOOR)

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

    decision = decide({}, {"project_id": P1}, ACTION_COHORT_STATISTICS, policy=policy, configured_threshold=FLOOR)

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
        projects = ["3f1c9a70-5e42-4d8b-9c31-7a2e6b4f8d15"]
        """
    )

    decision = decide({}, {"project_id": P2}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)

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

    decision = decide({}, {"project_id": P1}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)

    assert decision.permit is False
    assert decision.rule_id == "policy:no-raw-export"
    assert "no-raw-export" in decision.reason


def test_a_matching_deny_wins_wherever_it_sits():
    """Deny overrides permit, independent of document order.

    Under first-match-wins a deny placed below a broad permit never fired, and nothing
    detected the shadowing — the operator believed the deny was in force. Order must not
    change a decision.
    """
    for rules in (
        ("permit", "deny"),
        ("deny", "permit"),
    ):
        blocks = []
        for index, effect in enumerate(rules):
            blocks.append(
                f"""
                [[access.rule]]
                id = "r{index}"
                action = "cohort.dataframe"
                effect = "{effect}"
                """
            )
        policy = _policy("".join(blocks))

        decision = decide({}, {"project_id": P1}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)

        assert decision.permit is False, rules
        assert decision.rule_id == f"policy:r{rules.index('deny')}"


def test_an_allowlist_is_a_project_scoped_permit():
    """The allowlist shape under deny-overrides: permit the named projects, and every other
    project falls to the mentioned-action default deny — no broad deny rule needed."""
    policy = _policy(
        f"""
        [[access.rule]]
        id = "exception"
        action = "cohort.dataframe"
        effect = "permit"
        projects = ["{VIP}"]
        """
    )

    permitted = decide({}, {"project_id": VIP}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)
    denied = decide({}, {"project_id": P1}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)

    assert permitted.permit is True
    assert permitted.rule_id == "policy:exception"
    assert denied.permit is False
    assert denied.rule_id == "default.deny"


def test_matching_permits_apply_the_highest_threshold():
    """Two matching permits never loosen each other: the strictest threshold applies,
    whichever comes first in the document."""
    policy = _policy(
        f"""
        [[access.rule]]
        id = "everyone"
        action = "cohort.accession_ids"
        effect = "permit"
        min_cohort_size = 20

        [[access.rule]]
        id = "sensitive"
        action = "cohort.accession_ids"
        effect = "permit"
        projects = ["{P1}"]
        min_cohort_size = 60
        """
    )

    sensitive = decide({}, {"project_id": P1}, ACTION_COHORT_ACCESSION_IDS, policy=policy, configured_threshold=FLOOR)
    other = decide({}, {"project_id": P2}, ACTION_COHORT_ACCESSION_IDS, policy=policy, configured_threshold=FLOOR)

    assert sensitive.permit is True
    assert sensitive.effective_threshold == 60
    assert sensitive.rule_id == "policy:sensitive"
    assert other.effective_threshold == 20


def test_project_ids_match_whatever_their_case():
    """The hub seals str(UUID), which is lowercase. A rule written in uppercase must still
    match it, or an intended deny silently never fires."""
    policy = _policy(
        f"""
        [[access.rule]]
        id = "withdrawn"
        action = "cohort.dataframe"
        effect = "deny"
        projects = ["{P1.upper()}"]

        [[access.rule]]
        id = "everyone-else"
        action = "cohort.dataframe"
        effect = "permit"
        """
    )

    for request_id in (P1, P1.upper(), "{" + P1 + "}"):
        decision = decide(
            {}, {"project_id": request_id}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR
        )
        assert decision.permit is False, request_id
        assert decision.rule_id == "policy:withdrawn"


def test_an_unparseable_project_id_is_denied_when_a_rule_names_projects():
    """A request id that is not a UUID can match no project-scoped rule, so a deny list would
    wave it through to a broad permit. Fail closed instead."""
    policy = _policy(
        f"""
        [[access.rule]]
        id = "withdrawn"
        action = "cohort.dataframe"
        effect = "deny"
        projects = ["{P1}"]

        [[access.rule]]
        id = "everyone-else"
        action = "cohort.dataframe"
        effect = "permit"
        """
    )

    decision = decide(
        {}, {"project_id": "not-a-uuid"}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR
    )

    assert decision.permit is False
    assert decision.rule_id == "default.invalid_project"


def test_a_permit_without_its_own_threshold_uses_the_disclosure_section():
    """A permit rule with no min_cohort_size applies [disclosure], not the kit floor."""
    policy = _policy(
        """
        [disclosure]
        min_cohort_size = 25

        [[access.rule]]
        id = "plain"
        action = "cohort.dataframe"
        effect = "permit"
        """
    )

    decision = decide({}, {"project_id": P1}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)

    assert decision.permit is True
    assert decision.effective_threshold == 25


def test_a_rule_with_an_unknown_effect_cannot_be_built():
    """decide() must never read an unexpected effect as a permit. The type refuses one, so
    nothing but the loader's two effects can reach the evaluator."""
    from data_access_api.policy import Rule

    with pytest.raises(ValueError, match="effect"):
        Rule(id="r", action=ACTION_COHORT_DATAFRAME, effect="Deny", projects=None, min_cohort_size=None)


def test_project_scoped_rule_does_not_match_a_request_without_a_project():
    """/cohort carries no project id, so a project-scoped rule can never match there."""
    policy = _policy(
        """
        [[access.rule]]
        id = "scoped"
        action = "cohort.statistics"
        effect = "permit"
        projects = ["3f1c9a70-5e42-4d8b-9c31-7a2e6b4f8d15"]
        """
    )

    decision = decide({}, {"project_id": None}, ACTION_COHORT_STATISTICS, policy=policy, configured_threshold=FLOOR)

    assert decision.permit is False
    assert decision.rule_id == "default.deny"


def test_section_threshold_raises_the_floor():
    policy = _policy("[disclosure]\nmin_cohort_size = 25")

    decision = decide({}, {"project_id": P1}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)

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

    decision = decide({}, {"project_id": P1}, ACTION_COHORT_ACCESSION_IDS, policy=policy, configured_threshold=FLOOR)

    assert decision.permit is True
    assert decision.effective_threshold == 50


def test_effective_threshold_never_drops_below_the_configured_value():
    """The monotonic invariant (#870), asserted against a floor raised after load.

    The loader already rejects a document below the floor, but the evaluator applies
    max() independently — two guards, because this is the property that makes an
    optional policy safe to ship.
    """
    policy = _policy("[disclosure]\nmin_cohort_size = 20", floor=10)

    decision = decide({}, {"project_id": P1}, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=50)

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

    args = ({}, {"project_id": P1}, ACTION_COHORT_DATAFRAME)
    assert decide(*args, policy=permissive, configured_threshold=FLOOR).permit is True
    assert decide(*args, policy=restrictive, configured_threshold=FLOOR).permit is False


def test_unknown_action_raises():
    """A typo'd call site must fail loudly rather than evaluate to permit."""
    with pytest.raises(ValueError, match="unknown action"):
        decide({}, {}, "cohort.everything", policy=None, configured_threshold=FLOOR)
