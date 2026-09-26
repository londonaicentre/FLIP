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
    KNOWN_ACTIONS,
    Decision,
    Policy,
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
    3. **Document mentions the action** → first matching rule wins. A ``deny`` rule
       denies; a ``permit`` rule permits, possibly raising the threshold further.
    4. **Mentioned but nothing matched** → deny. Once a trust has written rules for an
       action, an unmatched request is one the trust did not authorise.

    The asymmetry between 2 and 4 is intentional and is what makes incremental adoption
    possible: a trust that writes a rule for ``cohort.dataframe`` does not thereby lock
    itself out of ``cohort.statistics``, which it never configured. The cost is that a
    policy silent about an action grants nothing new but blocks nothing either — the
    threshold still applies, because the floor is an invariant rather than a rule.

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

    project_id = resource.get("project_id")

    if not policy.mentions(action):
        return Decision(
            permit=True,
            rule_id="default.unmentioned",
            reason=f"policy does not mention {action}; existing behaviour applies",
            effective_threshold=section_threshold,
        )

    for rule in policy.rules:
        if not rule.matches(action, project_id):
            continue
        if rule.effect == EFFECT_DENY:
            return Decision(
                permit=False,
                rule_id=f"policy:{rule.id}",
                reason=f"denied by rule {rule.id!r} for action {action}",
                effective_threshold=section_threshold,
            )
        return Decision(
            permit=True,
            rule_id=f"policy:{rule.id}",
            reason=f"permitted by rule {rule.id!r} for action {action}",
            effective_threshold=max(section_threshold, rule.min_cohort_size or 0),
        )

    return Decision(
        permit=False,
        rule_id="default.deny",
        reason=(
            f"policy defines rules for {action} but none match "
            f"project {project_id or '<none>'}; denying by default"
        ),
        effective_threshold=section_threshold,
    )
