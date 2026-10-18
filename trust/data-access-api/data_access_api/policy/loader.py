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
service refuses to start — and so does a document that configures nothing, which is far
likelier a truncated copy than an intent.
"""

import hashlib
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
    normalise_project_id,
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
    """Validate a threshold, enforcing the monotonic floor (FLIP#870).

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


def _parse_rule(raw: object, *, index: int, floor: int, section: int | None, seen_ids: set[str]) -> Rule:
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

    # No default. Defaulting to permit turned a dropped `effect` line into a permit — the
    # opposite of what a rule written to deny meant.
    if "effect" not in table:
        raise AccessPolicyError(
            f"{where} (id {rule_id!r}) needs an 'effect' — one of {', '.join(sorted(KNOWN_EFFECTS))}; "
            f"there is no default"
        )
    effect = table["effect"]
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
        normalised = set()
        for entry in projects_raw:
            try:
                normalised.add(normalise_project_id(entry))
            except ValueError:
                # The hub seals str(uuid.UUID). Any other string can never match a request, so a
                # deny list naming one would deny nothing while reading as if it did.
                raise AccessPolicyError(
                    f"{where} (id {rule_id!r}) lists {entry!r}, which is not a project UUID — "
                    f"use the project id the hub shows (e.g. 3f1c9a70-5e42-4d8b-9c31-7a2e6b4f8d15)"
                ) from None
        projects = frozenset(normalised)

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
        if section is not None and min_cohort_size < section:
            # decide() takes the larger of the two, so this value could never apply. Accepting it
            # would describe a policy that is not the one enforced.
            raise AccessPolicyError(
                f"{where} (id {rule_id!r}) min_cohort_size={min_cohort_size} is below the document's "
                f"[disclosure] min_cohort_size={section}, so it would never apply — raise it or remove it"
            )

    return Rule(id=rule_id, action=action, effect=effect, projects=projects, min_cohort_size=min_cohort_size)


def parse_policy(text: str, *, floor: int, source: str, digest: str | None = None) -> Policy:
    """Parse and validate a governance document.

    Args:
        text: The TOML document.
        floor: The configured ``COHORT_QUERY_THRESHOLD``; policy may not go below it.
        source: Where the text came from, recorded on the Policy for logging.
        digest: SHA-256 of the document's bytes as stored. Defaults to the digest of ``text``
            encoded as UTF-8; ``load_policy`` passes the file's own, so a CRLF file reports the
            digest ``sha256sum`` prints for it.

    Returns:
        Policy: The validated document.

    Raises:
        AccessPolicyError: On malformed TOML, a document that configures nothing, an unknown
            section, an unknown key, an unknown action or effect, a missing effect, a project id
            that is not a UUID, a duplicate rule id, or a threshold below the floor.
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
        _parse_rule(raw, index=index, floor=floor, section=min_cohort_size, seen_ids=seen_ids)
        for index, raw in enumerate(raw_rules)
    )

    if min_cohort_size is None and not rules and not document.get("fl_privacy"):
        # An empty file, one with every line commented out, and one of bare section headers are
        # all far likelier a truncated or half-written copy than an intent.
        raise AccessPolicyError(
            f"{source} configures nothing (empty, only comments, or only empty sections) — refusing "
            f"it, since a truncated file would otherwise drop every rule; to run with no policy, unset "
            f"ACCESS_POLICY_FILE"
        )

    digest = digest or hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Policy(min_cohort_size=min_cohort_size, rules=rules, source=source, digest=digest)


def load_policy(*, path: str | None, floor: int) -> Policy | None:
    """Load the governance policy named by ``ACCESS_POLICY_FILE``.

    Args:
        path: Value of ``ACCESS_POLICY_FILE``, or ``None``/empty when unset.
        floor: The configured ``COHORT_QUERY_THRESHOLD``.

    Returns:
        Policy | None: The validated policy, or ``None`` when the trust configured none.

    Raises:
        AccessPolicyError: On an unreadable file or an invalid document.
    """
    # The service Makefile exports kit-file names with `sed 's/=.*//'`, so a commented-out
    # entry arrives as KEY="". Empty must behave exactly like absent.
    path = (path or "").strip() or None
    if path is None:
        return None

    policy_path = Path(path)
    try:
        raw = policy_path.read_bytes()
        text = raw.decode("utf-8")
    except OSError as e:
        # A configured-but-unreadable policy is the dangerous case: the operator believes
        # rules are in force. Refuse to start rather than fall back to the defaults.
        raise AccessPolicyError(f"ACCESS_POLICY_FILE={path!r} could not be read: {e}") from None
    except UnicodeDecodeError as e:
        raise AccessPolicyError(f"ACCESS_POLICY_FILE={path!r} is not UTF-8: {e}") from None

    # The digest is of the bytes as stored, which is what `sha256sum` prints and what
    # reload-governance compares; hashing the decoded text would not match a CRLF file.
    digest = hashlib.sha256(raw).hexdigest()
    return parse_policy(text, floor=floor, source=f"ACCESS_POLICY_FILE={path}", digest=digest)
