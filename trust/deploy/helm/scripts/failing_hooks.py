#!/usr/bin/env python3
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

"""Name the hooks that did not succeed on the current release revision (FLIP#1645).

`make deploy` runs `helm upgrade --wait`, so Helm itself judges whether the manifests applied,
whether every workload rolled out, and whether the post-upgrade hooks succeeded. Its exit code is
therefore the verdict and nothing here re-derives it. What Helm's exit code does NOT say is *which*
of the three post-upgrade hooks failed — `xnat-init` (weight 2), `omop-vocab-load` (5) and
`trust-seed` (6). Helm records each hook's `last_run.phase` per revision, so this reads that record
off `helm status -o json` on stdin and prints one line for the operator.

Diagnostics only: it never decides anything and the Makefile ignores its exit code. Unreadable or
absent input prints nothing, because a missing diagnostic must not turn a failed deploy into a
different outcome than the one Helm already returned.
"""

from __future__ import annotations

import json
import sys

SUCCEEDED = "Succeeded"


def failing_hooks(status: dict) -> list[tuple[str, str]]:
    """Hooks whose last run on this revision is anything other than Succeeded.

    A hook with no `last_run` phase never ran on this revision (Helm skips the remaining hooks once
    one fails), which is worth naming too: it is reported with an empty phase as "did not run".
    """
    out: list[tuple[str, str]] = []
    for hook in status.get("hooks") or []:
        phase = (hook.get("last_run") or {}).get("phase") or ""
        if phase != SUCCEEDED:
            out.append((hook.get("name") or "<unnamed>", phase or "did not run"))
    return out


def render(status: dict) -> str:
    hooks = failing_hooks(status)
    if not hooks:
        return ""
    listed = ", ".join(f"{name} ({phase})" for name, phase in hooks)
    return f"hooks that did not succeed on revision {status.get('version', '?')}: {listed}"


def main() -> int:
    try:
        status = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(status, dict):
        return 0
    line = render(status)
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
