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

"""Trust governance policy: the decision point (FLIP#1259).

One function, one return type. Every gated call site builds attributes, calls
:func:`decide`, and on a denial logs ``rule_id`` and ``reason`` before raising with
its *existing* refusal text.

The refusal text must not change. ``_BELOW_THRESHOLD_DETAIL`` on the row-level cohort
routes is deliberately identical for a zero-row cohort and a below-threshold one; adding
a rule name to it would turn the refusal into a row-count oracle. Attribution belongs in
the log line, never in the HTTP body.
"""

from collections.abc import Mapping
from typing import Any

from data_access_api.policy.model import (
    EFFECT_DENY,
    EFFECT_PERMIT,
    KNOWN_ACTIONS,
    Decision,
    Policy,
    normalise_project_id,
)


def decide(
    subject: Mapping[str, Any],
    resource: Mapping[str, Any],
    action: str,
    *,
    policy: Policy | None,
    configured_threshold: int,
) -> Decision:
    """Evaluate one operation against the trust's governance policy.

    Pure: no I/O, no settings lookup, no logging. Everything it needs arrives as an
    argument, so a test can pin a decision without standing up the service.

    Evaluation order:

    1. **No policy document** → permit at the configured threshold. This is the
       unconfigured path and must reproduce today's behaviour byte for byte.
    2. **Document does not mention the action** → permit at the effective threshold.
       This is the one place the design is deliberately *not* fail-closed; see below.
    3. **Document mentions the action** → every rule that matches is considered, and
       document order never matters:

       - any matching ``deny`` denies;
       - otherwise any matching ``permit`` permits, at the strictest threshold among the
         matching permits (never below ``[disclosure]`` or the configured floor);
       - otherwise deny. Once a trust has written rules for an action, an unmatched request
         is one the trust did not authorise.

    Deny-overrides replaced first-match-wins because order made an intended deny fragile: a
    deny placed below a broad permit never fired, and nothing said so. These are the same
    semantics as Cedar's forbid-overrides-permit with default deny, so a later change of
    evaluator is not a change of meaning.

    The asymmetry between 2 and 3 is intentional and is what makes incremental adoption
    possible: a trust that writes a rule for ``cohort.dataframe`` does not thereby lock
    itself out of ``cohort.statistics``, which it never configured. The cost is that a
    policy silent about an action grants nothing new but blocks nothing either — the
    threshold still applies, because the floor is an invariant rather than a rule.

    A request whose project id is not a UUID is denied whenever a rule for the action names
    projects: it could match none of them, so a deny list would wave it through to a broad
    permit. The hub always seals a UUID; anything else is not a request to be generous with.

    Args:
        subject: Attributes of the caller. Empty on the site plane — there is no user
            identity on the wire. Present so the contract is attribute-shaped.
        resource: Attributes of the thing acted upon; ``project_id`` when known.
        action: One of :data:`~data_access_api.policy.model.KNOWN_ACTIONS`.
        policy: The loaded document, or ``None`` when the trust has configured none.
        configured_threshold: ``COHORT_QUERY_THRESHOLD``. The floor policy may raise
            and must never lower.

    Returns:
        Decision: The outcome, always carrying an ``effective_threshold`` at or above
        ``configured_threshold``.

    Raises:
        ValueError: If ``action`` is not a known action. A caller passing an unknown
            action is a programming error, not a configuration one — failing loudly here
            stops a typo'd call site from silently evaluating to "permit".
    """
    if action not in KNOWN_ACTIONS:
        raise ValueError(f"unknown action {action!r} — expected one of {', '.join(sorted(KNOWN_ACTIONS))}")

    if policy is None:
        return Decision(
            permit=True,
            rule_id="default.unset",
            reason="no governance policy configured; platform defaults apply",
            effective_threshold=configured_threshold,
        )

    # The floor can only go up. max() is applied here rather than trusted from the
    # document because it is the invariant that makes an optional policy safe: even a
    # document that somehow carried a lower number could not weaken the threshold.
    section_threshold = max(configured_threshold, policy.min_cohort_size or 0)

    if not policy.mentions(action):
        return Decision(
            permit=True,
            rule_id="default.unmentioned",
            reason=f"policy does not mention {action}; existing behaviour applies",
            effective_threshold=section_threshold,
        )

    raw_project_id = resource.get("project_id")
    project_id: str | None = None
    if raw_project_id is not None:
        try:
            project_id = normalise_project_id(raw_project_id)
        except (ValueError, TypeError, AttributeError):
            if policy.scopes_projects(action):
                return Decision(
                    permit=False,
                    rule_id="default.invalid_project",
                    reason=f"project id {raw_project_id!r} is not a UUID and rules for {action} name projects",
                    effective_threshold=section_threshold,
                )

    matching = [rule for rule in policy.rules if rule.matches(action, project_id)]

    denies = [rule for rule in matching if rule.effect == EFFECT_DENY]
    if denies:
        return Decision(
            permit=False,
            rule_id=f"policy:{denies[0].id}",
            reason=f"denied by rule {denies[0].id!r} for action {action}",
            effective_threshold=section_threshold,
        )

    permits = [rule for rule in matching if rule.effect == EFFECT_PERMIT]
    if permits:
        # The strictest matching permit decides, so adding a permit can never loosen another.
        # max() keeps the first of equals, so the named rule is stable in document order.
        strictest = max(permits, key=lambda rule: rule.min_cohort_size or 0)
        return Decision(
            permit=True,
            rule_id=f"policy:{strictest.id}",
            reason=f"permitted by rule {strictest.id!r} for action {action}",
            effective_threshold=max(section_threshold, strictest.min_cohort_size or 0),
        )

    return Decision(
        permit=False,
        rule_id="default.deny",
        reason=(
            f"policy defines rules for {action} but none match project {project_id or '<none>'}; denying by default"
        ),
        effective_threshold=section_threshold,
    )
