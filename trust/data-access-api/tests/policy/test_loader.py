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

"""Unit tests for the governance policy loader (FLIP#1259).

The loader is the security boundary of this feature: it is what stops a typo'd action
name from silently matching nothing, and what stops a document from lowering the
disclosure threshold. Every rejection below is a case where being permissive would leave
an operator believing a rule is in force when it is not.
"""

import pytest

from data_access_api.policy import AccessPolicyError, load_policy, parse_policy

FLOOR = 10


def test_valid_document_parses_rules_in_order():
    """A well-formed document yields its rules, in document order (first match wins)."""
    policy = parse_policy(
        """
        [disclosure]
        min_cohort_size = 25

        [[access.rule]]
        id = "no-raw-export"
        action = "cohort.dataframe"
        effect = "deny"

        [[access.rule]]
        id = "imaging-allowlist"
        action = "cohort.accession_ids"
        effect = "permit"
        projects = ["p1", "p2"]
        min_cohort_size = 50
        """,
        floor=FLOOR,
        source="test",
    )

    assert policy.min_cohort_size == 25
    assert [r.id for r in policy.rules] == ["no-raw-export", "imaging-allowlist"]
    assert policy.rules[0].effect == "deny"
    assert policy.rules[0].projects is None
    assert policy.rules[1].projects == frozenset({"p1", "p2"})
    assert policy.rules[1].min_cohort_size == 50


def test_empty_document_is_valid_and_mentions_nothing():
    """An empty document is legal: it configures no rules and constrains nothing."""
    policy = parse_policy("", floor=FLOOR, source="test")

    assert policy.min_cohort_size is None
    assert policy.rules == ()
    assert policy.mentions("cohort.dataframe") is False


def test_fl_privacy_section_is_accepted_but_not_interpreted():
    """[fl_privacy] belongs to the fl-client; this loader must tolerate it, not reject it.

    The two planes ship as separate images and share one document. If this section were
    rejected here, a trust could not configure its FL privacy filter and its disclosure
    rules in the same file — which is the entire point of having one document.
    """
    policy = parse_policy(
        """
        [fl_privacy]
        policy = "percentile"
        percentile = 10
        gamma = 0.01
        """,
        floor=FLOOR,
        source="test",
    )

    assert policy.rules == ()


def test_unknown_top_level_section_is_rejected():
    """A misspelt section is an error, not an ignored line (the #851 precedent)."""
    with pytest.raises(AccessPolicyError, match="unrecognised key"):
        parse_policy("[disclosre]\nmin_cohort_size = 20", floor=FLOOR, source="test")


def test_unknown_rule_field_is_rejected():
    """A misspelt rule field would silently not apply; reject the document instead."""
    with pytest.raises(AccessPolicyError, match="unrecognised key"):
        parse_policy(
            """
            [[access.rule]]
            id = "r1"
            action = "cohort.dataframe"
            effect = "deny"
            project = ["p1"]
            """,
            floor=FLOOR,
            source="test",
        )


def test_unknown_action_is_rejected():
    """A typo'd action would match no request and quietly enforce nothing."""
    with pytest.raises(AccessPolicyError, match="not a known action"):
        parse_policy(
            """
            [[access.rule]]
            id = "r1"
            action = "cohort.dataframes"
            effect = "deny"
            """,
            floor=FLOOR,
            source="test",
        )


def test_unknown_effect_is_rejected():
    with pytest.raises(AccessPolicyError, match="effect"):
        parse_policy(
            """
            [[access.rule]]
            id = "r1"
            action = "cohort.dataframe"
            effect = "allow"
            """,
            floor=FLOOR,
            source="test",
        )


def test_threshold_below_floor_is_rejected():
    """Policy may raise the disclosure threshold but never lower it (FLIP#870)."""
    with pytest.raises(AccessPolicyError, match="below the configured COHORT_QUERY_THRESHOLD"):
        parse_policy("[disclosure]\nmin_cohort_size = 5", floor=FLOOR, source="test")


def test_rule_threshold_below_floor_is_rejected():
    """The same floor applies to a per-rule override, not just the section-wide value."""
    with pytest.raises(AccessPolicyError, match="below the configured COHORT_QUERY_THRESHOLD"):
        parse_policy(
            """
            [[access.rule]]
            id = "r1"
            action = "cohort.dataframe"
            effect = "permit"
            min_cohort_size = 2
            """,
            floor=FLOOR,
            source="test",
        )


def test_boolean_threshold_is_rejected():
    """``True`` is an int in Python and would sail through as the threshold 1."""
    with pytest.raises(AccessPolicyError, match="must be an integer"):
        parse_policy("[disclosure]\nmin_cohort_size = true", floor=FLOOR, source="test")


def test_duplicate_rule_id_is_rejected():
    """Ids are what make a denial attributable; two rules sharing one defeat that."""
    with pytest.raises(AccessPolicyError, match="reuses id"):
        parse_policy(
            """
            [[access.rule]]
            id = "dupe"
            action = "cohort.dataframe"
            effect = "deny"

            [[access.rule]]
            id = "dupe"
            action = "cohort.statistics"
            effect = "deny"
            """,
            floor=FLOOR,
            source="test",
        )


def test_missing_rule_id_is_rejected():
    with pytest.raises(AccessPolicyError, match="non-empty string 'id'"):
        parse_policy(
            """
            [[access.rule]]
            action = "cohort.dataframe"
            effect = "deny"
            """,
            floor=FLOOR,
            source="test",
        )


def test_empty_projects_list_is_rejected():
    """An empty list matches nothing and is almost certainly a mistake."""
    with pytest.raises(AccessPolicyError, match="empty 'projects' list"):
        parse_policy(
            """
            [[access.rule]]
            id = "r1"
            action = "cohort.dataframe"
            effect = "permit"
            projects = []
            """,
            floor=FLOOR,
            source="test",
        )


def test_min_cohort_size_on_deny_rule_is_rejected():
    """A denied request never reaches a threshold check, so the key is meaningless there."""
    with pytest.raises(AccessPolicyError, match="deny rule"):
        parse_policy(
            """
            [[access.rule]]
            id = "r1"
            action = "cohort.dataframe"
            effect = "deny"
            min_cohort_size = 50
            """,
            floor=FLOOR,
            source="test",
        )


def test_malformed_toml_is_rejected():
    with pytest.raises(AccessPolicyError, match="not valid TOML"):
        parse_policy("[disclosure\nmin_cohort_size = 20", floor=FLOOR, source="test")


def test_unset_sources_yield_no_policy():
    """Unset means today's behaviour — not an error, and not an empty policy."""
    assert load_policy(inline=None, path=None, floor=FLOOR) is None


def test_empty_string_sources_are_treated_as_unset():
    """The kit-file `sed` export turns a commented-out entry into KEY=""."""
    assert load_policy(inline="", path="", floor=FLOOR) is None
    assert load_policy(inline="   ", path="  ", floor=FLOOR) is None


def test_both_sources_set_is_rejected():
    """Guessing a precedence would leave the wrong policy silently in force."""
    with pytest.raises(AccessPolicyError, match="set exactly one"):
        load_policy(inline="[disclosure]\nmin_cohort_size = 20", path="/tmp/x.toml", floor=FLOOR)


def test_unreadable_policy_file_is_rejected(tmp_path):
    """A configured-but-missing file must fail closed, not fall back to defaults."""
    missing = tmp_path / "does-not-exist.toml"
    with pytest.raises(AccessPolicyError, match="could not be read"):
        load_policy(inline=None, path=str(missing), floor=FLOOR)


def test_policy_file_is_loaded(tmp_path):
    path = tmp_path / "governance.toml"
    path.write_text("[disclosure]\nmin_cohort_size = 30\n", encoding="utf-8")

    policy = load_policy(inline=None, path=str(path), floor=FLOOR)

    assert policy is not None
    assert policy.min_cohort_size == 30
    assert str(path) in policy.source


def test_shipped_example_document_is_valid():
    """The worked example in trust/governance.example.toml must actually load.

    A broken example is worse than none: it is the first thing an operator copies.
    """
    from pathlib import Path

    example = Path(__file__).resolve().parents[3] / "governance.example.toml"
    assert example.is_file(), f"expected the shipped example at {example}"

    policy = parse_policy(example.read_text(encoding="utf-8"), floor=FLOOR, source=str(example))

    assert policy.min_cohort_size == 25
    assert [r.id for r in policy.rules] == ["no-raw-export", "imaging-approved-projects"]
