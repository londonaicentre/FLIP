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

"""Validate a trust's governance document without starting the services (FLIP#1259).

Backs ``make -C trust check-governance KIT=<CODE>``. The services fail closed on an
invalid policy, so an operator editing rules wants the error here rather than from a
container that then refuses to come back up.

Deliberately calls the same ``load_policy`` the service calls at startup: a separate
validator would be free to drift from what is actually enforced, and an operator would
then be told a document is fine when the service would reject it.

The ``[fl_privacy]`` half is the fl-client's, so the file's own copy is validated by the
fl-client at container start (and by ``make -C trust check-governance``, which runs
``flip.nvflare.site_policy --check`` alongside this). One case is this script's to refuse,
because only it can see it: a ``[fl_privacy]`` section written *inline* in ``ACCESS_POLICY``.
The fl-client reads the document from ``ACCESS_POLICY_FILE`` alone, so that section would be
parsed here and then silently ignored at the container that was supposed to enforce it.

Exits 0 when valid (or when no document is configured), 1 with the loader's own message
when not.
"""

import os
import sys
import tomllib

from data_access_api.policy import AccessPolicyError, load_policy


def _inline_declares_fl_privacy(inline: str) -> bool:
    """Whether an inline document declares ``[fl_privacy]``, which only the fl-client enforces.

    Returns False for text ``load_policy`` will reject as malformed anyway, so a TOML error is
    reported once, by the loader, with its own message.
    """
    try:
        document = tomllib.loads(inline)
    except tomllib.TOMLDecodeError:
        return False
    return "fl_privacy" in document


def main() -> int:
    inline = os.environ.get("ACCESS_POLICY")
    path = os.environ.get("ACCESS_POLICY_FILE")
    floor = int(os.environ.get("COHORT_QUERY_THRESHOLD") or 10)

    if inline and _inline_declares_fl_privacy(inline):
        print(
            "❌ [fl_privacy] appears in ACCESS_POLICY, which the fl-client never reads — it reads "
            "the document from ACCESS_POLICY_FILE only, so that section would be ignored there "
            "while looking configured here. Put the document in a file "
            "(trust/governance.<CODE>.toml) and point ACCESS_POLICY_FILE at it, or state the "
            "fl-client half in FL_SITE_PRIVACY_*."
        )
        return 1

    try:
        policy = load_policy(inline=inline, path=path, floor=floor)
    except AccessPolicyError as e:
        print(f"❌ {e}")
        return 1

    if policy is None:
        print("ℹ️  No governance document configured — platform defaults apply.")
        return 0

    threshold = policy.min_cohort_size if policy.min_cohort_size is not None else f"{floor} (kit floor)"
    print("✅ Governance document is valid.")
    print(f"   source: {policy.source}")
    print(f"   effective min cohort size: {threshold}")
    print(f"   access rules: {len(policy.rules)}")
    for rule in policy.rules:
        scope = f"{len(rule.projects)} project(s)" if rule.projects else "all projects"
        extra = f", min_cohort_size={rule.min_cohort_size}" if rule.min_cohort_size is not None else ""
        print(f"     - {rule.id}: {rule.effect} {rule.action} for {scope}{extra}")
    print("ℹ️  [fl_privacy] is validated by the fl-client, and by check-governance's site_policy check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
