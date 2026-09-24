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

"""Trust governance policy: parsing and validation (FLIP#1259).

Strict by construction, following the precedent set by ``flip.nvflare.site_policy``:
an unknown key is a hard error, not an ignored line. That detail is the whole reason
the #851 renderer is safe — a typo'd parameter name would otherwise leave the weaker
default silently in force, and the operator would have no way to tell. The same logic
applies with more force here, where the silently-ignored line is an access rule.

Unset is not an error: no document means today's behaviour exactly. Invalid means the
service refuses to start.
"""

import tomllib
from collections.abc import Iterable
from pathlib import Path

from data_access_api.policy.model import (
    EFFECT_PERMIT,
    KNOWN_ACTIONS,
    KNOWN_EFFECTS,
    KNOWN_RULE_FIELDS,
    KNOWN_SECTIONS,
    AccessPolicyError,
    Policy,
    Rule,
)

# Keys accepted inside [disclosure]. `fl_privacy` is validated by the fl-client, not here.
_KNOWN_DISCLOSURE_FIELDS: frozenset[str] = frozenset({"min_cohort_size"})
_KNOWN_ACCESS_FIELDS: frozenset[str] = frozenset({"rule"})


def _require_mapping(value: object, where: str) -> dict:
    if not isinstance(value, dict):
        raise AccessPolicyError(f"{where} must be a table, got {type(value).__name__}")
    return value


def _reject_unknown(keys: Iterable[str], known: frozenset[str], where: str) -> None:
    """Reject any key not in the allowlist, naming the offenders and the alternatives."""
    unknown = sorted(str(k) for k in keys if k not in known)
    if unknown:
        raise AccessPolicyError(
            f"unrecognised key(s) {', '.join(repr(u) for u in unknown)} in {where} — "
            f"expected only {', '.join(sorted(known))}; refusing to load a policy that ignores them"
        )


def _parse_threshold(value: object, *, floor: int, where: str) -> int:
    """Validate a threshold, enforcing the monotonic floor (#870, plan Decision 4).

    ``bool`` is rejected explicitly: it is an ``int`` subclass in Python, so ``True``
    would otherwise sail through as the threshold 1 and quietly disable the check.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise AccessPolicyError(f"{where} must be an integer, got {value!r}")
    if value < floor:
        raise AccessPolicyError(
            f"{where}={value} is below the configured COHORT_QUERY_THRESHOLD of {floor} — "
            f"policy may raise the disclosure threshold but never lower it (FLIP#870)"
        )
    return value


def _parse_rule(raw: object, *, index: int, floor: int, seen_ids: set[str]) -> Rule:
    where = f"[[access.rule]] #{index + 1}"
    table = _require_mapping(raw, where)
    _reject_unknown(table.keys(), KNOWN_RULE_FIELDS, where)

    rule_id = table.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise AccessPolicyError(f"{where} needs a non-empty string 'id' — it is what makes a denial attributable")
    rule_id = rule_id.strip()
    if rule_id in seen_ids:
        raise AccessPolicyError(f"{where} reuses id {rule_id!r}; rule ids must be unique so a log line names one rule")
    seen_ids.add(rule_id)

    action = table.get("action")
    if action not in KNOWN_ACTIONS:
        raise AccessPolicyError(
            f"{where} (id {rule_id!r}) has action {action!r}, which is not a known action — "
            f"expected one of {', '.join(sorted(KNOWN_ACTIONS))}"
        )

    effect = table.get("effect", EFFECT_PERMIT)
    if effect not in KNOWN_EFFECTS:
        raise AccessPolicyError(
            f"{where} (id {rule_id!r}) has effect {effect!r} — expected one of {', '.join(sorted(KNOWN_EFFECTS))}"
        )

    projects_raw = table.get("projects")
    projects: frozenset[str] | None = None
    if projects_raw is not None:
        if not isinstance(projects_raw, list) or not all(isinstance(p, str) for p in projects_raw):
            raise AccessPolicyError(f"{where} (id {rule_id!r}) 'projects' must be a list of project id strings")
        if not projects_raw:
            # An empty list matches nothing, which reads as "applies to no project" — almost
            # certainly not what was meant, and silently inert. Omitting the key means "all".
            raise AccessPolicyError(
                f"{where} (id {rule_id!r}) has an empty 'projects' list, which would match nothing; "
                f"omit the key to apply the rule to every project"
            )
        projects = frozenset(projects_raw)

    min_cohort_size: int | None = None
    if "min_cohort_size" in table:
        if effect != EFFECT_PERMIT:
            raise AccessPolicyError(
                f"{where} (id {rule_id!r}) sets 'min_cohort_size' on a deny rule, where it has no meaning — "
                f"a denied request never reaches a threshold check"
            )
        min_cohort_size = _parse_threshold(
            table["min_cohort_size"], floor=floor, where=f"{where} (id {rule_id!r}) min_cohort_size"
        )

    return Rule(id=rule_id, action=action, effect=effect, projects=projects, min_cohort_size=min_cohort_size)


def parse_policy(text: str, *, floor: int, source: str) -> Policy:
    """Parse and validate a governance document.

    Args:
        text: The TOML document.
        floor: The configured ``COHORT_QUERY_THRESHOLD``; policy may not go below it.
        source: Where the text came from, recorded on the Policy for logging.

    Returns:
        Policy: The validated document.

    Raises:
        AccessPolicyError: On malformed TOML, an unknown section, an unknown key, an
            unknown action, a duplicate rule id, or a threshold below the floor.
    """
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise AccessPolicyError(f"{source} is not valid TOML: {e}") from None

    _reject_unknown(document.keys(), KNOWN_SECTIONS, f"{source} (top level)")

    disclosure = _require_mapping(document.get("disclosure", {}), f"{source} [disclosure]")
    _reject_unknown(disclosure.keys(), _KNOWN_DISCLOSURE_FIELDS, f"{source} [disclosure]")
    min_cohort_size: int | None = None
    if "min_cohort_size" in disclosure:
        min_cohort_size = _parse_threshold(
            disclosure["min_cohort_size"], floor=floor, where=f"{source} [disclosure] min_cohort_size"
        )

    access = _require_mapping(document.get("access", {}), f"{source} [access]")
    _reject_unknown(access.keys(), _KNOWN_ACCESS_FIELDS, f"{source} [access]")
    raw_rules = access.get("rule", [])
    if not isinstance(raw_rules, list):
        raise AccessPolicyError(f"{source} [[access.rule]] must be an array of tables")

    seen_ids: set[str] = set()
    rules = tuple(
        _parse_rule(raw, index=index, floor=floor, seen_ids=seen_ids) for index, raw in enumerate(raw_rules)
    )

    return Policy(min_cohort_size=min_cohort_size, rules=rules, source=source)


def load_policy(*, inline: str | None, path: str | None, floor: int) -> Policy | None:
    """Load the governance policy from settings.

    Exactly one source may be configured. Both set is an error rather than a silent
    precedence rule: an operator who sets both has a mistaken belief about which one is
    in force, and guessing would leave the wrong policy running.

    Args:
        inline: Contents of ``ACCESS_POLICY``, or ``None``/empty when unset.
        path: Value of ``ACCESS_POLICY_FILE``, or ``None``/empty when unset.
        floor: The configured ``COHORT_QUERY_THRESHOLD``.

    Returns:
        Policy | None: The validated policy, or ``None`` when the trust configured none.

    Raises:
        AccessPolicyError: On an unreadable file or an invalid document.
    """
    # The service Makefile exports kit-file names with `sed 's/=.*//'`, so a commented-out
    # entry arrives as KEY="". Empty must behave exactly like absent.
    inline = (inline or "").strip() or None
    path = (path or "").strip() or None

    if inline is not None and path is not None:
        raise AccessPolicyError(
            "both ACCESS_POLICY and ACCESS_POLICY_FILE are set — set exactly one, "
            "so there is no ambiguity about which policy is in force"
        )

    if inline is not None:
        return parse_policy(inline, floor=floor, source="ACCESS_POLICY")

    if path is None:
        return None

    policy_path = Path(path)
    try:
        text = policy_path.read_text(encoding="utf-8")
    except OSError as e:
        # A configured-but-unreadable policy is the dangerous case: the operator believes
        # rules are in force. Refuse to start rather than fall back to the defaults.
        raise AccessPolicyError(f"ACCESS_POLICY_FILE={path!r} could not be read: {e}") from None

    return parse_policy(text, floor=floor, source=f"ACCESS_POLICY_FILE={path}")
