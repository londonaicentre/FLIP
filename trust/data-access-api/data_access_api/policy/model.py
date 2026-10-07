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

"""Trust governance policy: the data model (FLIP#1259).

A trust states its runtime access rules in a governance document instead of having
them compiled into this service. This module holds the validated shapes only — see
``loader`` for parsing and ``decide`` for evaluation, both of which are deliberately
separate so the evaluator stays a pure function of validated data.

The document is TOML, parsed with stdlib ``tomllib``. TOML rather than YAML because
the fl-client's policy renderer (``flip.nvflare.site_policy``) must read the same
file and is stdlib-only on purpose — it runs before NVFLARE starts and must never
fail on a framework import. A PyYAML dependency there would break that guarantee.

Two sections exist, one per enforcement plane, because the planes ship as separate
images and neither can import the other's code:

* ``[disclosure]`` / ``[[access.rule]]`` — this service (cohort routes).
* ``[fl_privacy.nvflare]`` — the NVFLARE fl-client, via ``flip.nvflare.site_policy``.

Each plane validates its own section strictly and must tolerate the other's section
being present. ``KNOWN_SECTIONS`` below is the shared contract; the matching list in
``flip/nvflare/site_policy.py`` must be kept in step with it.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Operations a rule may name. An unknown action in a document is a hard error rather
# than an ignored line: a typo'd action would otherwise silently match nothing and
# leave the operator believing a restriction is in force when it is not. Same reason
# `site_policy.py` rejects a misspelt FL_SITE_PRIVACY_* name.
ACTION_COHORT_STATISTICS = "cohort.statistics"
ACTION_COHORT_DATAFRAME = "cohort.dataframe"
ACTION_COHORT_ACCESSION_IDS = "cohort.accession_ids"

KNOWN_ACTIONS: frozenset[str] = frozenset(
    {
        ACTION_COHORT_STATISTICS,
        ACTION_COHORT_DATAFRAME,
        ACTION_COHORT_ACCESSION_IDS,
    }
)

# Top-level tables this document format defines across BOTH planes. `fl_privacy` is
# not enforced here — the fl-client owns it — but it must be accepted, or a trust
# that configures its FL privacy filter could not also configure disclosure.
KNOWN_SECTIONS: frozenset[str] = frozenset({"disclosure", "access", "fl_privacy"})

EFFECT_PERMIT = "permit"
EFFECT_DENY = "deny"
KNOWN_EFFECTS: frozenset[str] = frozenset({EFFECT_PERMIT, EFFECT_DENY})

# Rule fields. Anything else in a [[access.rule]] table is rejected.
KNOWN_RULE_FIELDS: frozenset[str] = frozenset({"id", "action", "effect", "projects", "min_cohort_size"})


def normalise_project_id(value: str) -> str:
    """Return a project id in the form the hub seals it: ``str(uuid.UUID(...))``, lowercase.

    Both sides of a match go through this — the rule's ``projects`` at load and the request's
    id at decision time — so an id written in uppercase, braced or as a URN still names the
    project the hub means. Comparing the raw strings let an uppercase entry in a deny list
    match nothing, silently.

    Raises:
        ValueError: If ``value`` is not a UUID.
    """
    return str(uuid.UUID(value))


class AccessPolicyError(ValueError):
    """Invalid governance policy document.

    Raised by the loader only. Callers must treat it as fatal: a policy that cannot be
    understood must stop the service rather than leave it running under the weaker
    built-in defaults, which is the failure mode #851's renderer was written to avoid.
    """


@dataclass(frozen=True)
class Rule:
    """One access rule, already validated.

    Attributes:
        id: Operator-chosen identifier, unique within the document. Appears in logs and
            audit records so a denial is attributable, and never in an HTTP response.
        action: One of :data:`KNOWN_ACTIONS`.
        effect: ``permit`` or ``deny``.
        projects: Normalised project ids (see :func:`normalise_project_id`) this rule
            applies to, or ``None`` for "any project".
        min_cohort_size: Optional threshold raise for this rule. Permit rules only, and
            the loader has already checked it is not below the configured floor.

    Raises:
        ValueError: On construction with an effect other than ``permit``/``deny``, a threshold
            on a deny rule, or ``projects`` that is not a frozenset. The loader reports these
            with the document's own context first; this is what stops anything else reaching
            :func:`decide.decide`, which reads the effect and must never guess it.
    """

    id: str
    action: str
    effect: str
    projects: frozenset[str] | None
    min_cohort_size: int | None

    def __post_init__(self) -> None:
        if self.effect not in KNOWN_EFFECTS:
            raise ValueError(f"rule {self.id!r} has effect {self.effect!r}; expected one of {sorted(KNOWN_EFFECTS)}")
        if self.effect == EFFECT_DENY and self.min_cohort_size is not None:
            raise ValueError(f"rule {self.id!r} is a deny rule and cannot carry min_cohort_size")
        if self.projects is not None and not isinstance(self.projects, frozenset):
            raise ValueError(f"rule {self.id!r} projects must be a frozenset, got {type(self.projects).__name__}")

    def matches(self, action: str, project_id: str | None) -> bool:
        """Whether this rule applies to an action against a project.

        A rule with no ``projects`` list applies to every project. A rule that names
        projects applies only to those, so a request carrying no project id can never
        match it. ``project_id`` must already be normalised.
        """
        if self.action != action:
            return False
        if self.projects is None:
            return True
        return project_id is not None and project_id in self.projects


@dataclass(frozen=True)
class Policy:
    """A validated governance policy document.

    Attributes:
        min_cohort_size: Section-wide disclosure threshold from ``[disclosure]``, or
            ``None``. Never below the configured ``COHORT_QUERY_THRESHOLD`` — the
            loader rejects a document that tries, so policy can only tighten (FLIP#870).
        rules: Access rules in document order. Order is kept for log lines only: a decision
            never depends on it (see :func:`decide.decide`).
        source: Where the document came from, for log lines only.
        digest: SHA-256 of the document's bytes. ``check-governance`` prints it and the
            service logs it at startup, so an operator can tell the document they validated
            is the one in force.
    """

    min_cohort_size: int | None
    rules: tuple[Rule, ...]
    source: str
    digest: str

    def mentions(self, action: str) -> bool:
        """Whether any rule names this action.

        Drives the deliberate fall-through asymmetry in :func:`decide.decide`: an action
        the document never mentions keeps its existing behaviour, rather than being
        denied because the trust has not enumerated it yet.
        """
        return any(rule.action == action for rule in self.rules)

    def scopes_projects(self, action: str) -> bool:
        """Whether any rule for this action names specific projects.

        Lets a route skip opening the hub-sealed project envelope when no rule could match
        on it — on ``/cohort``, where the project id is otherwise unused, decrypting
        unconditionally would add a 400 the route has never returned.
        """
        return any(rule.action == action and rule.projects is not None for rule in self.rules)


@dataclass(frozen=True)
class Decision:
    """The outcome of one policy evaluation.

    Attributes:
        permit: Whether the operation may proceed.
        rule_id: Which rule decided, e.g. ``policy:withdrawn-project-imaging``, ``default.unset``,
            ``default.deny``, ``default.invalid_project``. Log and audit only — putting it in an HTTP response would
            turn the refusal into a probe for the trust's configuration.
        reason: Operator-facing explanation. Safe to log; NOT safe to return verbatim.
        effective_threshold: Minimum distinct subjects this operation must clear. Always
            at least the configured ``COHORT_QUERY_THRESHOLD``.
    """

    permit: bool
    rule_id: str
    reason: str
    effective_threshold: int


def describe_policy(policy: Policy | None, *, floor: int) -> str:
    """One log line saying which policy this service enforces, or that it enforces none.

    Logged once at startup by ``main.py``. It is the operator's evidence after
    ``reload-governance``, which waits for the ``[governance]`` prefix: an image that predates
    governance support never prints it, so a stale ``DOCKER_TAG`` is caught rather than
    reported as applied. Rule ids are fine here — this is the trust's own log, never an HTTP
    body.

    Args:
        policy: The loaded document, or ``None``.
        floor: ``COHORT_QUERY_THRESHOLD``.

    Returns:
        str: The line, starting ``[governance]``.
    """
    if policy is None:
        return (
            f"[governance] no policy configured (ACCESS_POLICY_FILE unset) — platform defaults apply: "
            f"COHORT_QUERY_THRESHOLD={floor}"
        )
    threshold = policy.min_cohort_size if policy.min_cohort_size is not None else floor
    rules = "; ".join(_describe_rule(rule) for rule in policy.rules)
    return (
        f"[governance] policy ACTIVE from {policy.source} sha256={policy.digest[:12]}: "
        f"min_cohort_size={threshold}, {len(policy.rules)} access rule(s){': ' + rules if rules else ''}"
    )


def _describe_rule(rule: Rule) -> str:
    if rule.projects is None:
        scope = "all projects"
    else:
        scope = f"{len(rule.projects)} project{'' if len(rule.projects) == 1 else 's'}"
    if rule.min_cohort_size is not None:
        scope += f", min_cohort_size={rule.min_cohort_size}"
    return f"{rule.id}={rule.effect} {rule.action} ({scope})"


def subject_attributes() -> Mapping[str, Any]:
    """Build the subject attributes available on the site plane.

    Empty, and that is the point. The site plane authenticates its callers with a shared
    per-trust secret (``utils/internal_auth``); no user identity reaches this service, so
    no rule may be written over one. The argument is kept in the :func:`decide.decide`
    signature so the contract is attribute-shaped from day one and the evaluator can be
    replaced without touching call sites — but a rule language over users needs a new
    identity flow first, and that is a separate piece of work.
    """
    return {}
