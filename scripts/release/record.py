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
#
"""Render the release notes' *Release checks* section from ``release-gates`` results (FLIP#1608).

``make release-record TAG=v0.13.0 [PR=<n>] [APPLY=1]``

The section lives in ``.github/RELEASE_NOTES_TEMPLATE.md`` and is published with the release (CONTRIBUTING
§ Pre-release checklist). A box is ticked only by a passing result; anything failed or not run stays ``[ ]``
and says so. Refuses results from another commit: the gates must have run at the PR head, or at a commit that
differs from it only in the template itself (the commit that records them). Prints the section; ``APPLY=1``
writes it into the template, leaving the commit to you.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_lib import REPO, TEMPLATE, ReleaseError, git, head_sha, load_results, pr_head  # noqa: E402

HEADING = "## :white_check_mark: Release checks"
COMMENT = (
    "<!-- The gates that cannot run in CI: no GitHub-hosted runner has a GPU, and both the suite and the smoke "
    "test need real hardware and a full stack. Written by `make release-record` from `make release-gates` "
    "results — see *Pre-release checklist* in CONTRIBUTING.md. -->"
)


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h} h {m} min" if h else f"{m} min {s} s" if m else f"{s} s"


def _box(result: dict | None) -> str:
    return "[x]" if result and result.get("status") == "passed" else "[ ]"


def _state(result: dict | None) -> str:
    """How a gate ended, for a gate that did not pass."""
    if not result or result.get("status") == "not-run":
        return "not run"
    return f"failed (exit {result.get('exit_code')}, log `{Path(result['log']).name}`)"


def _tutorials(label: str, result: dict | None) -> str:
    if not result or result.get("status") != "passed":
        return f"{label}: {_state(result)}."
    m = result.get("metrics", {})
    runs = ", ".join(f"{t['name']} {_duration(t.get('seconds'))}" for t in m.get("tutorials", []))
    return f"{label}: all {m.get('total', '?')} tutorials passed in {_duration(result.get('seconds'))} — {runs}."


def render(results: dict[str, dict]) -> str:
    """The *Release checks* section for these results (all boxes empty when there are none)."""
    any_result = next((r for r in results.values() if r.get("sha")), {})
    # The notes are public: name the hardware, never the host (its name stays in the JSON).
    host = f"{any_result['gpu']} workstation" if any_result.get("gpu") else ""
    started = [r["start"] for r in results.values() if r.get("start")]
    date = datetime.fromisoformat(min(started)).strftime("%-d %B %Y") if started else ""
    tn, tf = results.get("tutorials-nvflare"), results.get("tutorials-flower")
    sn, sf = results.get("smoke-nvflare"), results.get("smoke-flower")
    lines = [
        HEADING,
        "",
        COMMENT,
        "",
        "Tutorial suite, on a GPU host:",
        "",
        f"- {_box(tn)} FLARE — `make -C fl-tutorials run-all-tutorials`",
        f"- {_box(tf)} Flower — `make -C fl-tutorials run-all-tutorials FL_BACKEND=flower`",
        f"- {'[x]' if host and date else '[ ]'} Host and date recorded: {host or '—'}, {date or '—'}",
        "",
    ]
    if results:
        lines += [_tutorials("FLARE", tn), _tutorials("Flower", tf), ""]
    lines += [
        "Full-platform smoke test, against a running deployment:",
        "",
        f"- {_box(sn)} FLARE — `make e2e_smoke`",
        f"- {_box(sf)} Flower — `make e2e_smoke FL_BACKEND=flower`",
        "",
    ]
    if results:
        sha = any_result.get("sha", "")[:9]
        instance = any_result.get("instance", "")
        detail = []
        for label, r in (("FLARE", sn), ("Flower", sf)):
            if r and r.get("status") == "passed":
                status = r.get("metrics", {}).get("final_status", "?")
                detail.append(f"{label} reached {status} in {_duration(r.get('seconds'))}")
            else:
                detail.append(f"{label} {_state(r)}")
        lines += [
            f"Run at `{sha}` on an isolated instance (`{instance}`) built from that commit — every repo-built "
            f"image at `:{instance}`, checked by `make release-verify` — started from empty state: "
            f"{'; '.join(detail)}.",
            "",
        ]
        suites = []
        for name, cmd in (("unit", "make unit_test"), ("integration", "make integration_test")):
            r = results.get(name)
            suites.append(f"`{cmd}` {'passed' if r and r.get('status') == 'passed' else _state(r)}")
        lines += [f"At the same commit: {', '.join(suites)}.", ""]
    return "\n".join(lines)


def replace_section(template: str, section: str) -> str:
    """Swap the *Release checks* section (up to the next ``## `` heading) for ``section``."""
    pattern = re.compile(rf"^{re.escape(HEADING)}\n.*?(?=^## )", re.S | re.M)
    if not pattern.search(template):
        raise ReleaseError(f"no '{HEADING}' section followed by another heading in {TEMPLATE.name}")
    return pattern.sub(lambda _: section.rstrip("\n") + "\n\n", template, count=1)


def check_commit(results: dict[str, dict], head: str) -> None:
    """Refuse results from another commit; a head that only adds the template's own update is the same code."""
    shas = {r["sha"] for r in results.values() if r.get("sha")}
    if len(shas) != 1:
        raise ReleaseError(f"results come from {len(shas)} different commits: {', '.join(s[:9] for s in shas)}")
    (sha,) = shas
    if sha == head:
        return
    changed = set(git("diff", "--name-only", sha, head).splitlines())
    if changed - {str(TEMPLATE.relative_to(REPO))}:
        raise ReleaseError(f"the gates ran at {sha[:9]}, but {head[:9]} changes more than the release notes since")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--pr", default="")
    parser.add_argument("--apply", action="store_true")
    a = parser.parse_args(argv)
    try:
        results = load_results(a.tag)
        if not results:
            raise ReleaseError(f"no results for {a.tag}; run make release-gates first")
        check_commit(results, pr_head(a.pr) if a.pr else head_sha())
        section = render(results)
        if a.apply:
            TEMPLATE.write_text(replace_section(TEMPLATE.read_text(), section))
            print(f"📝 updated {TEMPLATE.relative_to(REPO)}; review and commit it")
        else:
            print(section)
    except ReleaseError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
