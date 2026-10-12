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

Backs ``make -C trust check-governance KIT=<CODE>`` and the on-prem onboarding checklist.
The services fail closed on an invalid policy, so an operator editing rules wants the error
here rather than from a container that then refuses to come back up.

Deliberately calls the same ``load_policy`` the service calls at startup: a separate
validator would be free to drift from what is actually enforced, and an operator would
then be told a document is fine when the service would reject it. It prints the same
digest the service logs at startup, so the operator can match the two.

Stdlib-only, like the ``data_access_api.policy`` package it imports: it runs on the trust
host with a bare interpreter (``PYTHONPATH`` at the service root), never syncing the
service's own dependencies there. The ``[fl_privacy]`` half is the fl-client's, and
``check-governance`` validates it with ``flip.nvflare.site_policy --check`` alongside this.

Exits 0 when valid (or when no document is configured), 1 with the loader's own message
when not.
"""

import os
import sys

from data_access_api.policy import AccessPolicyError, load_policy


def main() -> int:
    path = os.environ.get("ACCESS_POLICY_FILE")
    floor = int(os.environ.get("COHORT_QUERY_THRESHOLD") or 10)

    try:
        policy = load_policy(path=path, floor=floor)
    except AccessPolicyError as e:
        print(f"❌ {e}")
        return 1

    if policy is None:
        print("ℹ️  No governance document configured — platform defaults apply.")
        return 0

    threshold = policy.min_cohort_size if policy.min_cohort_size is not None else f"{floor} (kit floor)"
    print("✅ Governance document is valid.")
    print(f"   source: {policy.source}")
    print(f"   sha256: {policy.digest} (data-access-api logs its first 12 characters at startup)")
    print(f"   effective min cohort size: {threshold}")
    print(f"   access rules: {len(policy.rules)}")
    for rule in policy.rules:
        scope = f"{len(rule.projects)} project(s)" if rule.projects else "all projects"
        extra = f", min_cohort_size={rule.min_cohort_size}" if rule.min_cohort_size is not None else ""
        print(f"     - {rule.id}: {rule.effect} {rule.action} for {scope}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
