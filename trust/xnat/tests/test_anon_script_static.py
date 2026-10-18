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

import csv
from pathlib import Path

import pytest
from pydicom.datadict import dictionary_VM

from anon_engine import (
    AssignLiteralRule,
    AssignVariableRule,
    HashUidRule,
    RemoveAllPrivateRule,
    RemoveRule,
    Rule,
)

# Tags FLIP must explicitly handle on EVERY incoming study before it is stored.
# Each entry: tag -> ("description", expected disposition).
# Disposition values:
#   "removed"      -> a `- */(gggg,eeee)` rule must exist
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
    0x00081030: ("Study Description", "removed"),  # imaging-api's session label is the accession number
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
        if isinstance(rule, RemoveRule) and rule.matches(tag) and rule.scope != "nested":
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
def test_phi_tag_has_required_disposition(anon_rules: list[Rule], tag: int, description: str, expected: str) -> None:
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
    assert rules_for_tag[-1].value == "YES", f"Patient Identity Removed must be 'YES', got {rules_for_tag[-1].value!r}"


def test_deidentification_method_documented(anon_rules: list[Rule]) -> None:
    rules_for_tag = [r for r in anon_rules if isinstance(r, AssignLiteralRule) and r.tag == DEID_METHOD_TAG]
    assert rules_for_tag, "De-identification Method (0012,0063) must be assigned"
    assert "FLIP" in rules_for_tag[-1].value, (
        f"De-identification Method should reference FLIP, got {rules_for_tag[-1].value!r}"
    )


def test_every_removal_reaches_nested_sequences(anon_rules: list[Rule]) -> None:
    """A bare ``- (gggg,eeee)`` matches the top level only, leaving every nested copy (FLIP#1513)."""
    top_only = [r.line_no for r in anon_rules if isinstance(r, RemoveRule) and r.scope == "top"]
    assert not top_only, f"removal rules on lines {top_only} miss nested copies; write `- */(gggg,eeee)`"


def test_nested_copies_of_replaced_identifiers_are_removed(anon_rules: list[Rule]) -> None:
    """Patient Name and ID are replaced at the top level, so their nested copies need a rule of their own."""
    nested = {r.tag for r in anon_rules if isinstance(r, RemoveRule) and r.scope == "nested"}
    assert {0x00100010, 0x00100020} <= nested


PS315_TABLE = Path(__file__).parent / "ps315_table_e1_1.csv"
# Table E.1-1 rows the script cannot or need not act on
TABLE_EXEMPT = {
    "(0000,1001)": "Requested SOP Instance UID is a DIMSE command field, never part of a stored object",
    "(0002,0003)": "Media Storage SOP Instance UID: DicomEdit rewrites it to follow the hashed SOP Instance UID",
}
# Labels written at the top level, so only their nested copies are removed
TABLE_REPLACED = {"(0010,0010)", "(0010,0020)"}


def _table_rows() -> list[dict[str, str]]:
    with PS315_TABLE.open() as table:
        return list(csv.DictReader(line for line in table if not line.startswith("#")))


def _sample_tags(tag: str) -> list[int]:
    """Concrete tags for a table row; a repeating group like ``(60xx,4000)`` gives two of them."""
    digits = tag.strip("()").replace(",", "")
    return [int(digits.replace("x", fill), 16) for fill in ("0", "2")] if "x" in digits else [int(digits, 16)]


def _removed_at(rules: list[Rule], tag: int, scope: str) -> bool:
    return any(isinstance(r, RemoveRule) and r.scope == scope and r.matches(tag) for r in rules)


def _single_valued(tag: int) -> bool:
    try:
        return dictionary_VM(tag) == "1"
    except KeyError:  # newer than this pydicom; every such UID row is VM 1 in the 2026d table
        return True


def test_every_table_e1_1_attribute_is_handled(anon_rules: list[Rule]) -> None:
    """Each attribute the PS3.15 Basic Profile acts on is removed at any depth, hashed, or replaced.

    UIDs (action U) are hashed at the top level and removed where nested; multi-valued ones, which
    ``hashUID`` does not take, are removed. Every other action (X, Z, D and their choices) is applied as
    removal, the strictest of them. Refresh the table with ``extract_ps315_table.py``.
    """
    unhandled: list[str] = []
    for row in _table_rows():
        tag_text, action = row["tag"], row["action"]
        if tag_text.startswith("(gggg") or tag_text in TABLE_EXEMPT:
            continue  # private attributes: test_private_tags_are_removed
        for tag in _sample_tags(tag_text):
            final = _final_disposition(anon_rules, tag)
            if tag_text in TABLE_REPLACED:
                ok = final == "replaced" and _removed_at(anon_rules, tag, "nested")
            elif action == "U" and _single_valued(tag):
                ok = final == "hashed" and _removed_at(anon_rules, tag, "nested")
            else:
                ok = final == "removed" and _removed_at(anon_rules, tag, "any")
            if not ok:
                unhandled.append(f"{tag_text} {row['name']} ({action}): {final}")
    assert not unhandled, f"{len(unhandled)} Table E.1-1 attributes not handled:\n" + "\n".join(unhandled)


def test_ps315_table_is_complete() -> None:
    """Guards the vendored table itself against truncation."""
    assert len(_table_rows()) >= 657


def test_private_tags_are_removed(anon_rules: list[Rule]) -> None:
    assert any(isinstance(r, RemoveAllPrivateRule) for r in anon_rules), "removeAllPrivateTags is missing"


def test_no_orphan_rules(anon_rules: list[Rule]) -> None:
    """Every rule we parsed should be a supported kind.

    parse_script raises on unsupported syntax, so this is mostly a smoke test
    that the file actually contains rules and we did not silently parse zero.
    """
    assert len(anon_rules) > 10, f"expected many rules in anon_script.das, got {len(anon_rules)}"
