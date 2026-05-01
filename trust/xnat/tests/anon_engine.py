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
Minimal interpreter for the subset of DicomEdit (.das) syntax used by the FLIP
site-wide anonymization script. This is a TEST DOUBLE, not a faithful DicomEdit
re-implementation - it exists so we can prove the rules in
`trust/xnat/xnat/config/anon_script.das` actually scrub PHI when applied to
representative synthetic DICOM datasets.

Supported grammar (line-oriented; comments via `//`, blank lines ignored):

    version "<string>"                       # ignored; documents the file
    (gggg,eeee) := "<literal>"               # assign string literal
    (gggg,eeee) := <variable>                # assign value of project|subject|session
    (gggg,eeee) := hashUID[(gggg,eeee)]      # replace with deterministic hash UID
    - (gggg,eeee)                            # remove tag

Anything else raises ``UnsupportedRuleError`` so unexpected new syntax in the
.das file fails loudly in CI rather than being silently skipped.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Union

from pydicom.dataset import Dataset

# (gggg,eeee) - case insensitive, exactly 4 hex digits each
_TAG_RE = re.compile(r"\(\s*([0-9A-Fa-f]{4})\s*,\s*([0-9A-Fa-f]{4})\s*\)")
_VARIABLE_NAMES = frozenset({"project", "subject", "session"})
_HASH_UID_RE = re.compile(r"hashUID\[\s*" + _TAG_RE.pattern + r"\s*\]")
_LITERAL_RE = re.compile(r'"([^"]*)"')


class UnsupportedRuleError(ValueError):
    """Raised when the parser encounters a .das line it does not handle."""


def _parse_tag(group_hex: str, element_hex: str) -> int:
    return (int(group_hex, 16) << 16) | int(element_hex, 16)


@dataclass(frozen=True)
class RemoveRule:
    tag: int
    line_no: int


@dataclass(frozen=True)
class AssignLiteralRule:
    tag: int
    value: str
    line_no: int


@dataclass(frozen=True)
class AssignVariableRule:
    tag: int
    variable: str
    line_no: int


@dataclass(frozen=True)
class HashUidRule:
    tag: int
    source_tag: int
    line_no: int


Rule = Union[RemoveRule, AssignLiteralRule, AssignVariableRule, HashUidRule]


def _strip_comment(line: str) -> str:
    idx = line.find("//")
    return line if idx == -1 else line[:idx]


def parse_script(path: Path) -> list[Rule]:
    """Parse a .das file into a list of typed rules.

    Lines that are blank, pure comment, or the ``version`` directive are
    skipped. Any other unrecognised syntax raises ``UnsupportedRuleError``.
    """
    rules: list[Rule] = []
    for line_no, raw in enumerate(path.read_text().splitlines(), start=1):
        body = _strip_comment(raw).strip()
        if not body:
            continue
        if body.startswith("version "):
            continue
        rules.append(_parse_line(body, line_no))
    return rules


def _parse_line(body: str, line_no: int) -> Rule:
    if body.startswith("-"):
        rest = body[1:].strip()
        match = _TAG_RE.fullmatch(rest)
        if not match:
            raise UnsupportedRuleError(f"line {line_no}: cannot parse remove rule: {body!r}")
        return RemoveRule(tag=_parse_tag(*match.groups()), line_no=line_no)

    lhs_match = _TAG_RE.match(body)
    if not lhs_match:
        raise UnsupportedRuleError(f"line {line_no}: expected '(gggg,eeee) := ...' or '- (gggg,eeee)': {body!r}")

    rhs = body[lhs_match.end() :].strip()
    if not rhs.startswith(":="):
        raise UnsupportedRuleError(f"line {line_no}: expected ':=' after tag: {body!r}")
    rhs = rhs[2:].strip()
    target_tag = _parse_tag(*lhs_match.groups())

    literal_match = _LITERAL_RE.fullmatch(rhs)
    if literal_match:
        return AssignLiteralRule(tag=target_tag, value=literal_match.group(1), line_no=line_no)

    hash_match = _HASH_UID_RE.fullmatch(rhs)
    if hash_match:
        source_tag = _parse_tag(hash_match.group(1), hash_match.group(2))
        return HashUidRule(tag=target_tag, source_tag=source_tag, line_no=line_no)

    if rhs in _VARIABLE_NAMES:
        return AssignVariableRule(tag=target_tag, variable=rhs, line_no=line_no)

    raise UnsupportedRuleError(f"line {line_no}: unsupported RHS expression {rhs!r}")


def hash_uid(value: str) -> str:
    """Deterministic UID-formatted hash, matching DicomEdit's hashUID intent.

    Returns ``2.25.<int>`` where ``<int>`` is derived from a SHA-256 of the
    input; "2.25" is the OID arc reserved for UUID-derived UIDs and is the
    convention DicomEdit uses for replacement UIDs.
    """
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    # 16 bytes -> integer fits comfortably within 64-char UID limit.
    integer = int.from_bytes(digest[:16], "big")
    return f"2.25.{integer}"


@dataclass(frozen=True)
class XnatLabels:
    project: str
    subject: str
    session: str

    def lookup(self, name: str) -> str:
        match name:
            case "project":
                return self.project
            case "subject":
                return self.subject
            case "session":
                return self.session
            case _:
                raise UnsupportedRuleError(f"unknown variable {name!r}")


def apply_rules(dataset: Dataset, rules: Iterable[Rule], labels: XnatLabels) -> Dataset:
    """Apply parsed rules to ``dataset`` in order, mutating and returning it.

    Rules are applied sequentially so later rules can override or remove
    earlier writes - this matches DicomEdit's behaviour and is the reason the
    real script writes ``(0010,4000) := project`` then later removes
    ``(0010,4000)``.
    """
    for rule in rules:
        if isinstance(rule, RemoveRule):
            if rule.tag in dataset:
                del dataset[rule.tag]
        elif isinstance(rule, AssignLiteralRule):
            _assign(dataset, rule.tag, rule.value)
        elif isinstance(rule, AssignVariableRule):
            _assign(dataset, rule.tag, labels.lookup(rule.variable))
        elif isinstance(rule, HashUidRule):
            current = dataset.get(rule.source_tag, None)
            source_value = current.value if current is not None else ""
            _assign(dataset, rule.tag, hash_uid(str(source_value)))
        else:  # pragma: no cover - exhaustive over Rule union
            raise UnsupportedRuleError(f"unhandled rule type: {type(rule).__name__}")
    return dataset


def _assign(dataset: Dataset, tag: int, value: str) -> None:
    if tag in dataset:
        dataset[tag].value = value
        return
    # Create with a sensible VR. UID tags need VR=UI, everything else falls
    # back to LO (Long String) which fits the FLIP script's needs.
    vr: Literal["UI", "LO"] = "UI" if _is_uid_tag(tag) else "LO"
    dataset.add_new(tag, vr, value)


_UID_TAGS = frozenset({
    0x00080018,  # SOP Instance UID
    0x0020000D,  # Study Instance UID
    0x0020000E,  # Series Instance UID
})


def _is_uid_tag(tag: int) -> bool:
    return tag in _UID_TAGS
