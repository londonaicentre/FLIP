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
"""
Tier 1 - static guarantees about anon_script.das.

These tests parse the .das file and assert structural invariants without
running the rules against any DICOM dataset. They guard against regressions
where a rule is accidentally deleted or commented out.
"""

from __future__ import annotations

import pytest

from anon_engine import (
    AssignLiteralRule,
    AssignVariableRule,
    HashUidRule,
    RemoveRule,
    Rule,
)

# Tags FLIP must explicitly handle on EVERY incoming study before it is stored.
# Each entry: tag -> ("description", expected disposition).
# Disposition values:
#   "removed"      -> a `- (gggg,eeee)` rule must exist
#   "replaced"     -> the tag must be assigned (literal or XNAT variable)
#   "hashed"       -> a hashUID rule with this tag as both lhs and source
PHI_TAGS_REQUIRED: dict[int, tuple[str, str]] = {
    # Direct patient identifiers (P2-07: Patient ID is the primary one)
    0x00100020: ("Patient ID", "replaced"),
    0x00100010: ("Patient Name", "replaced"),
    0x00100030: ("Patient Birth Date", "removed"),
    0x00100032: ("Patient Birth Time", "removed"),
    0x00101000: ("Other Patient IDs", "removed"),
    0x00101001: ("Other Patient Names", "removed"),
    0x00101002: ("Other Patient IDs Sequence", "removed"),
    0x00101005: ("Patient Birth Name", "removed"),
    0x00101040: ("Patient Address", "removed"),
    0x00102154: ("Patient Telephone Numbers", "removed"),
    # Institutional identifiers
    0x00080080: ("Institution Name", "removed"),
    0x00080081: ("Institution Address", "removed"),
    0x00081040: ("Institutional Department Name", "removed"),
    # Physician / operator identifiers
    0x00080090: ("Referring Physician Name", "removed"),
    0x00080092: ("Referring Physician Address", "removed"),
    0x00080094: ("Referring Physician Telephone Numbers", "removed"),
    0x00081048: ("Physicians of Record", "removed"),
    0x00081050: ("Performing Physician Name", "removed"),
    0x00081070: ("Operators Name", "removed"),
    # Other identifying tags
    0x00080050: ("Accession Number", "removed"),
    0x00101090: ("Medical Record Locator", "removed"),
    0x00102160: ("Ethnic Group", "removed"),
    0x00102180: ("Occupation", "removed"),
    0x001021B0: ("Additional Patient History", "removed"),
    0x00104000: ("Patient Comments", "removed"),
    0x00321032: ("Requesting Physician", "removed"),
    0x00400006: ("Scheduled Performing Physician Name", "removed"),
    0x00402016: ("Placer Order Number", "removed"),
    0x00402017: ("Filler Order Number", "removed"),
    0x0040A123: ("Person Name (SR)", "removed"),
    # UIDs (must be hashed, not removed - downstream tooling needs them)
    0x00080018: ("SOP Instance UID", "hashed"),
    0x0020000D: ("Study Instance UID", "hashed"),
    0x0020000E: ("Series Instance UID", "hashed"),
}

DEID_METHOD_TAG = 0x00120063
PATIENT_IDENTITY_REMOVED_TAG = 0x00120062


def _final_disposition(rules: list[Rule], tag: int) -> str | None:
    """Return what happens to ``tag`` after all rules execute, in order.

    Later rules can overwrite earlier ones (e.g. the script writes
    ``(0010,4000) := project`` then later removes it), so we walk the rule
    list and report the LAST rule that touched the tag.
    """
    last: Rule | None = None
    for rule in rules:
        if isinstance(rule, RemoveRule) and rule.tag == tag:
            last = rule
        elif isinstance(rule, (AssignLiteralRule, AssignVariableRule, HashUidRule)) and rule.tag == tag:
            last = rule
    if last is None:
        return None
    if isinstance(last, RemoveRule):
        return "removed"
    if isinstance(last, (AssignLiteralRule, AssignVariableRule)):
        return "replaced"
    if isinstance(last, HashUidRule):
        return "hashed" if last.source_tag == tag else "replaced"
    return None  # pragma: no cover


@pytest.mark.parametrize(
    ("tag", "description", "expected"),
    [(t, d[0], d[1]) for t, d in PHI_TAGS_REQUIRED.items()],
    ids=[f"{d[0]} ({t:08X})" for t, d in PHI_TAGS_REQUIRED.items()],
)
def test_phi_tag_has_required_disposition(
    anon_rules: list[Rule], tag: int, description: str, expected: str
) -> None:
    """Each PHI tag in the allowlist must end up removed/replaced/hashed."""
    actual = _final_disposition(anon_rules, tag)
    assert actual == expected, (
        f"{description} ({tag:08X}): expected disposition {expected!r}, got {actual!r}. "
        f"This guards a security invariant - update PHI_TAGS_REQUIRED only after "
        f"deliberate review."
    )


def test_patient_identity_removed_flag_set(anon_rules: list[Rule]) -> None:
    """DICOM PS3.15 says (0012,0062) must be 'YES' for de-identified studies."""
    rules_for_tag = [
        r for r in anon_rules if isinstance(r, AssignLiteralRule) and r.tag == PATIENT_IDENTITY_REMOVED_TAG
    ]
    assert rules_for_tag, "Patient Identity Removed (0012,0062) must be assigned"
    assert rules_for_tag[-1].value == "YES", (
        f"Patient Identity Removed must be 'YES', got {rules_for_tag[-1].value!r}"
    )


def test_deidentification_method_documented(anon_rules: list[Rule]) -> None:
    rules_for_tag = [r for r in anon_rules if isinstance(r, AssignLiteralRule) and r.tag == DEID_METHOD_TAG]
    assert rules_for_tag, "De-identification Method (0012,0063) must be assigned"
    assert "FLIP" in rules_for_tag[-1].value, (
        f"De-identification Method should reference FLIP, got {rules_for_tag[-1].value!r}"
    )


def test_no_orphan_rules(anon_rules: list[Rule]) -> None:
    """Every rule we parsed should be one of the four supported kinds.

    parse_script raises on unsupported syntax, so this is mostly a smoke test
    that the file actually contains rules and we did not silently parse zero.
    """
    assert len(anon_rules) > 10, f"expected many rules in anon_script.das, got {len(anon_rules)}"
