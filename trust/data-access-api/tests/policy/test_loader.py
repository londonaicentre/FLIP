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

P1 = "3f1c9a70-5e42-4d8b-9c31-7a2e6b4f8d15"
P2 = "9b7e2d48-1a36-4c92-8f05-6d3b1e7a4c28"


def test_valid_document_parses_rules_in_order():
    """A well-formed document yields its rules, in document order (kept for log lines; the
    decision itself does not depend on order)."""
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
        projects = ["3f1c9a70-5e42-4d8b-9c31-7a2e6b4f8d15", "9B7E2D48-1A36-4C92-8F05-6D3B1E7A4C28"]
        min_cohort_size = 50
        """,
        floor=FLOOR,
        source="test",
    )

    assert policy.min_cohort_size == 25
    assert [r.id for r in policy.rules] == ["no-raw-export", "imaging-allowlist"]
    assert policy.rules[0].effect == "deny"
    assert policy.rules[0].projects is None
    assert policy.rules[1].projects == frozenset({P1, P2}), "project ids are normalised to the hub's lowercase form"
    assert policy.rules[1].min_cohort_size == 50


@pytest.mark.parametrize("text", ["", "   \n\t\n", "# every line commented out\n# [disclosure]\n"])
def test_a_document_that_configures_nothing_is_rejected(text):
    """An empty file is far likelier a truncated copy than an intent: accepting it as a valid
    no-op would drop every rule the operator believes is in force. No policy is spelt by
    leaving ACCESS_POLICY_FILE unset."""
    with pytest.raises(AccessPolicyError, match="configures nothing"):
        parse_policy(text, floor=FLOOR, source="test")


def test_fl_privacy_section_is_accepted_but_not_interpreted():
    """[fl_privacy] belongs to the fl-client; this loader must tolerate it, not reject it.

    The two planes ship as separate images and share one document. If this section were
    rejected here, a trust could not configure its FL privacy filter and its disclosure
    rules in the same file — which is the entire point of having one document.
    """
    policy = parse_policy(
        """
        [fl_privacy.nvflare]
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


def test_unknown_key_in_disclosure_is_rejected():
    """A misspelt threshold key would leave the kit floor in force while the operator
    believes the raised one is."""
    with pytest.raises(AccessPolicyError, match="unrecognised key"):
        parse_policy("[disclosure]\nmin_cohort = 50", floor=FLOOR, source="test")


def test_unknown_key_in_access_is_rejected():
    """[[access.rules]] (plural) would otherwise drop every rule in the document."""
    with pytest.raises(AccessPolicyError, match="unrecognised key"):
        parse_policy(
            """
            [[access.rules]]
            id = "r1"
            action = "cohort.dataframe"
            effect = "deny"
            """,
            floor=FLOOR,
            source="test",
        )


def test_effect_is_required():
    """A dropped effect line used to default to permit, turning an intended deny into a
    permit. There is no default."""
    with pytest.raises(AccessPolicyError, match="needs an 'effect'"):
        parse_policy(
            """
            [[access.rule]]
            id = "r1"
            action = "cohort.dataframe"
            """,
            floor=FLOOR,
            source="test",
        )


@pytest.mark.parametrize("entry", ["p1", "", "3f1c9a70-5e42-4d8b-9c31"])
def test_a_project_id_that_is_not_a_uuid_is_rejected(entry):
    """The hub seals str(uuid.UUID). Any other string can never match a request, so a deny
    list naming one would silently deny nothing."""
    with pytest.raises(AccessPolicyError, match="not a project UUID"):
        parse_policy(
            f"""
            [[access.rule]]
            id = "r1"
            action = "cohort.dataframe"
            effect = "deny"
            projects = ["{entry}"]
            """,
            floor=FLOOR,
            source="test",
        )


def test_rule_threshold_below_the_disclosure_section_is_rejected():
    """A rule threshold under [disclosure] is inert (the section's value wins by max()), so
    accepting it would describe a policy that is not the one enforced."""
    with pytest.raises(AccessPolicyError, match="below the document's \\[disclosure\\]"):
        parse_policy(
            """
            [disclosure]
            min_cohort_size = 25

            [[access.rule]]
            id = "r1"
            action = "cohort.dataframe"
            effect = "permit"
            min_cohort_size = 15
            """,
            floor=FLOOR,
            source="test",
        )


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


def test_unset_source_yields_no_policy():
    """Unset means today's behaviour — not an error, and not an empty policy."""
    assert load_policy(path=None, floor=FLOOR) is None


def test_empty_string_source_is_treated_as_unset():
    """The kit-file `sed` export turns a commented-out entry into KEY=""."""
    assert load_policy(path="", floor=FLOOR) is None
    assert load_policy(path="  ", floor=FLOOR) is None


def test_unreadable_policy_file_is_rejected(tmp_path):
    """A configured-but-missing file must fail closed, not fall back to defaults."""
    missing = tmp_path / "does-not-exist.toml"
    with pytest.raises(AccessPolicyError, match="could not be read"):
        load_policy(path=str(missing), floor=FLOOR)


def test_policy_file_is_loaded_with_a_digest_of_its_bytes(tmp_path):
    """The digest is what an operator compares between check-governance and the service's
    startup line, to know the document they validated is the one in force."""
    import hashlib

    path = tmp_path / "governance.toml"
    text = "[disclosure]\nmin_cohort_size = 30\n"
    path.write_text(text, encoding="utf-8")

    policy = load_policy(path=str(path), floor=FLOOR)

    assert policy is not None
    assert policy.min_cohort_size == 30
    assert str(path) in policy.source
    assert policy.digest == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_describe_names_the_source_digest_and_every_rule():
    """The startup line (logged by main.py) is the operator's proof of what is enforced."""
    from data_access_api.policy import describe_policy

    policy = parse_policy(
        f"""
        [disclosure]
        min_cohort_size = 25

        [[access.rule]]
        id = "withdrawn"
        action = "cohort.accession_ids"
        effect = "deny"
        projects = ["{P1}"]

        [[access.rule]]
        id = "everyone-else"
        action = "cohort.accession_ids"
        effect = "permit"
        """,
        floor=FLOOR,
        source="ACCESS_POLICY_FILE=/app/governance.toml",
    )

    line = describe_policy(policy, floor=FLOOR)

    assert line.startswith("[governance] policy ACTIVE from ACCESS_POLICY_FILE=/app/governance.toml")
    assert f"sha256={policy.digest[:12]}" in line
    assert "min_cohort_size=25" in line
    assert "withdrawn=deny cohort.accession_ids (1 project)" in line
    assert "everyone-else=permit cohort.accession_ids (all projects)" in line


def test_describe_says_when_no_policy_is_configured():
    from data_access_api.policy import describe_policy

    line = describe_policy(None, floor=12)

    assert line.startswith("[governance] no policy configured")
    assert "COHORT_QUERY_THRESHOLD=12" in line


def test_shipped_example_document_is_valid():
    """The worked example in trust/governance.example.toml must actually load.

    A broken example is worse than none: it is the first thing an operator copies.
    """
    from pathlib import Path

    example = Path(__file__).resolve().parents[3] / "governance.example.toml"
    assert example.is_file(), f"expected the shipped example at {example}"

    policy = parse_policy(example.read_text(encoding="utf-8"), floor=FLOOR, source=str(example))

    assert policy.min_cohort_size == 25
    assert [r.id for r in policy.rules] == ["withdrawn-project-imaging", "imaging-for-everyone-else"]


def test_the_shipped_example_leaves_fl_training_and_real_projects_alone():
    """The example's first version denied cohort.dataframe — the FL client's own training fetch —
    and allowlisted two placeholder projects for imaging, so a trust that copied it stopped FL
    and every real imaging pull while its comments said training still worked."""
    from pathlib import Path

    from data_access_api.policy import ACTION_COHORT_ACCESSION_IDS, ACTION_COHORT_DATAFRAME, decide

    example = Path(__file__).resolve().parents[3] / "governance.example.toml"
    policy = parse_policy(example.read_text(encoding="utf-8"), floor=FLOOR, source=str(example))
    real_project = {"project_id": P1}

    training = decide({}, real_project, ACTION_COHORT_DATAFRAME, policy=policy, configured_threshold=FLOOR)
    imaging = decide({}, real_project, ACTION_COHORT_ACCESSION_IDS, policy=policy, configured_threshold=FLOOR)

    assert training.permit is True
    assert training.rule_id == "default.unmentioned"
    assert imaging.permit is True
    assert imaging.effective_threshold == 50


def test_the_digest_is_of_the_files_bytes_even_with_crlf_line_endings(tmp_path):
    """reload-governance compares the logged digest with `sha256sum` of the file. Hashing the
    decoded text normalised CRLF to LF and made a correct reload report a different document."""
    import hashlib

    path = tmp_path / "governance.toml"
    path.write_bytes(b"[disclosure]\r\nmin_cohort_size = 30\r\n")

    policy = load_policy(path=str(path), floor=FLOOR)

    assert policy is not None
    assert policy.digest == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("text", ["[disclosure]\n", "[access]\n", "[disclosure]\n[access]\n"])
def test_a_document_of_empty_sections_configures_nothing(text):
    """Section headers with nothing under them are as inert as an empty file, and as likely a
    truncated or half-written copy."""
    with pytest.raises(AccessPolicyError, match="configures nothing"):
        parse_policy(text, floor=FLOOR, source="test")


def test_a_document_carrying_only_the_fl_clients_section_is_accepted():
    """It configures the fl-client's half; data-access-api has nothing to enforce, and says so by
    loading a policy with no rules rather than refusing to start."""
    policy = parse_policy('[fl_privacy.nvflare]\npolicy = "percentile"\n', floor=FLOOR, source="test")

    assert policy.rules == ()
    assert policy.min_cohort_size is None
