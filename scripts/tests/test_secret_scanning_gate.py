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
"""Guards that keep the detect-secrets CI gate a gate (FLIP#1215).

Before FLIP#1215 the ``Detect Secrets Scan`` job ran a bare ``detect-secrets scan``
(prints a report, always exits 0) and the ``Pre-commit Hooks`` job ran the hook with
``|| true``; every run was green while ~110 unbaselined literals accumulated. Each
assertion here pins one way that regression could come back quietly:

- the scan step must run ``detect-secrets-hook`` *against the baseline* — the bare
  ``scan`` subcommand cannot fail;
- the scan step must refuse an empty file list before calling the hook — given no
  filenames the hook exits 0 without reading anything, so a broken file-list
  derivation would pass the job having scanned nothing;
- the pre-commit job's detect-secrets line must not swallow its exit code;
- the pinned ``detect-secrets`` version must equal the pre-commit hook's ``rev``, or CI
  and a developer's commit can disagree about what is a finding;
- the exit-3 branch (hook rewrote a stale baseline) must fail the job rather than pass
  on a silently narrowed baseline;
- every baseline entry must be an explicit ``is_secret: false`` decision — an
  unaudited entry is a literal blessed by omission — and none may name a gitignored
  path, which can only come from a scan outside git.

Deliberately stdlib-only and executable as a plain script — ``test_trust_kit_scripts.yml``
runs these with ``python <file>``, not pytest, and PyYAML is not installed there. The
workflow is read line-wise for the few strings that matter.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "secret-scanning.yml"
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"
BASELINE = REPO_ROOT / ".secrets.baseline"


def _pre_commit_rev() -> str:
    """Return the version the detect-secrets pre-commit hook is pinned to (``rev: v1.5.0`` -> ``1.5.0``)."""
    text = PRE_COMMIT.read_text()
    match = re.search(r"repo: https://github\.com/Yelp/detect-secrets\s*\n\s*rev: v?([0-9][0-9.]*)", text)
    if not match:
        raise ValueError(f"no Yelp/detect-secrets hook with a rev in {PRE_COMMIT}")
    return match.group(1)


def _workflow_code_lines() -> list[str]:
    """The workflow's lines with comment lines dropped, so a comment naming a command cannot satisfy a check."""
    return [line for line in WORKFLOW.read_text().splitlines() if not line.strip().startswith("#")]


def check_scan_job_runs_the_hook_against_the_baseline(failures: list[str]) -> None:
    code = "\n".join(_workflow_code_lines())
    if not re.search(r"detect-secrets-hook\s+--baseline\s+\.secrets\.baseline\b", code):
        failures.append(
            f"{WORKFLOW.name}: the Detect Secrets Scan job must run "
            "`detect-secrets-hook --baseline .secrets.baseline` — a bare `detect-secrets scan` "
            "prints a report and exits 0, so it cannot fail (FLIP#1215)."
        )
    for line in code.splitlines():
        if re.match(r"detect-secrets scan\b", line.strip()):
            failures.append(
                f"{WORKFLOW.name}: `detect-secrets scan` is not a gate; use `detect-secrets-hook --baseline`."
            )


def check_scan_step_refuses_an_empty_file_list(failures: list[str]) -> None:
    """The hook exits 0 when given no filenames, so the step must fail on an empty list *before* calling it."""
    code = "\n".join(_workflow_code_lines())
    hook_call = re.search(r"detect-secrets-hook\s+--baseline\s+\.secrets\.baseline", code)
    guard = re.search(r'if \[ "\$\{#files\[@\]\}" -eq 0 \]; then\n(.*?)\n\s*fi\s*\n', code, re.DOTALL)
    if not guard:
        failures.append(
            f"{WORKFLOW.name}: the scan step must refuse an empty file list "
            '(`if [ "${#files[@]}" -eq 0 ]; then … exit 1; fi`) — `detect-secrets-hook` with no filenames '
            "exits 0 without reading anything, so the job would pass having scanned nothing."
        )
        return
    if not re.search(r"\bexit 1\b", guard.group(1)):
        failures.append(f"{WORKFLOW.name}: the empty-file-list guard must `exit 1`, not fall through to the hook.")
    if hook_call and guard.start() > hook_call.start():
        failures.append(f"{WORKFLOW.name}: the empty-file-list guard must precede the `detect-secrets-hook` call.")


def check_pre_commit_job_does_not_swallow_the_hook(failures: list[str]) -> None:
    for lineno, line in enumerate(WORKFLOW.read_text().splitlines(), start=1):
        if "pre-commit run detect-secrets" in line and "|| true" in line:
            failures.append(
                f"{WORKFLOW.name}:{lineno}: `pre-commit run detect-secrets … || true` discards the only "
                "exit code that job produces for secrets."
            )


def check_ci_pin_matches_pre_commit_rev(failures: list[str]) -> None:
    rev = _pre_commit_rev()
    pins = re.findall(r"pip install detect-secrets==([0-9][0-9.]*)", WORKFLOW.read_text())
    if not pins:
        failures.append(f"{WORKFLOW.name}: detect-secrets must be pinned (`pip install detect-secrets==<rev>`).")
        return
    for pin in pins:
        if pin != rev:
            failures.append(
                f"{WORKFLOW.name}: CI pins detect-secrets=={pin} but the pre-commit hook is at v{rev}; "
                "the two must agree or a developer's commit and CI disagree about what is a finding."
            )


def check_stale_baseline_fails_the_job(failures: list[str]) -> None:
    text = WORKFLOW.read_text()
    # The branch body ends at the `fi` on its own line — a bare `fi` would stop at "files".
    branch = re.search(r'if \[ "\$rc" -eq 3 \]; then\n(.*?)\n\s*fi\s*\n', text, re.DOTALL)
    if not branch:
        failures.append(
            f"{WORKFLOW.name}: no handling for detect-secrets-hook exit 3 (baseline rewritten because stale)."
        )
        return
    if not re.search(r"\bexit 1\b", branch.group(1)):
        failures.append(f"{WORKFLOW.name}: the exit-3 (stale baseline) branch must `exit 1`, not pass.")


def check_baseline_entries_are_explicit_decisions(failures: list[str]) -> None:
    baseline = json.loads(BASELINE.read_text())
    rev = _pre_commit_rev()
    if baseline.get("version") != rev:
        failures.append(f".secrets.baseline: version {baseline.get('version')!r} != pre-commit hook rev {rev!r}.")
    results = baseline.get("results", {})
    for filename, entries in results.items():
        if not (REPO_ROOT / filename).is_file():
            failures.append(f".secrets.baseline: {filename} is not a file in the repo — stale entry.")
            continue
        for entry in entries:
            if entry.get("is_secret") is not False:
                failures.append(
                    f".secrets.baseline: {filename}:{entry.get('line_number')} ({entry.get('type')}) has no "
                    '`"is_secret": false` — a baselined entry must be an audited false positive, not a literal '
                    "blessed by omission."
                )
    if results:
        ignored = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", *results],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        for filename in ignored:
            failures.append(
                f".secrets.baseline: {filename} is gitignored — such an entry can only come from a scan outside "
                "git and must not be committed."
            )


def main() -> int:
    failures: list[str] = []
    try:
        check_scan_job_runs_the_hook_against_the_baseline(failures)
        check_scan_step_refuses_an_empty_file_list(failures)
        check_pre_commit_job_does_not_swallow_the_hook(failures)
        check_ci_pin_matches_pre_commit_rev(failures)
        check_stale_baseline_fails_the_job(failures)
        check_baseline_entries_are_explicit_decisions(failures)
    except ValueError as exc:
        failures.append(str(exc))

    if failures:
        print("❌ detect-secrets gate guards failed:\n")
        for f in failures:
            print(f"  - {f}\n")
        return 1
    print("✅ detect-secrets gate guards passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
