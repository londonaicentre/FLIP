---
description: Prepare or cut a FLIP platform release using the repository release process
argument-hint: "[version, e.g. v0.13.0] [prepare|gates|record|main|post-merge] [gpu-host, e.g. bioeng625-pc]"
allowed-tools: Bash(gh:*), Bash(git:*), Bash(make:*), Bash(uv:*), Bash(ssh:*), Bash(docker:*), Bash(curl:*), Read, Edit, Write, Grep, Glob
---

<!--
Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

Target: **$ARGUMENTS** (default: the next minor after the latest `v*.*.*` tag; mode `prepare`).

Read `.agents/skills/release/SKILL.md` and follow it. That file is shared with other agents and holds the
procedure, the scope limits and the GPU-host gates. Also read the CONTRIBUTING section it points to.

On top of it, for Claude Code:

- **GPU host.** If this session is not on the GPU host (a laptop or Mac), `release-gates` runs there
  over SSH; `release-prepare`, `release-record` and the `gh`/`git` work stay
  local. Take the host from the arguments, or else from memory. Ask only if neither has it, and name the example:
  the core team uses `bioeng625-pc`. Use the user's own SSH alias for it, and check it with
  `ssh <alias> 'nvidia-smi -L && docker info -f {{.Name}}'` first. The scripts put `~/.local/bin` on the PATH
  themselves, since non-interactive SSH may not load it.
- **Resume, don't restart.** Look for an open `Release v<X.Y.Z>` PR, an existing `release-v<X.Y.Z>` worktree (locally
  and on the GPU host) and existing `release-gates/<TAG>/*.json`; rerun only the gates that did not pass at the
  current head (`GATES=…`).
- **Memory.** If your memory has release notes or owed items tied to "next release" or "release→main", such as
  GitHub environment variable cleanups, write them one per line to a file and pass it as
  `make release-main-check OWED=<file>`, so they land in the `main`-PR body as after-merge steps. Also follow any
  turn-taking rules it records for the shared host.
- **Remote runs.** Start `release-gates` with `run_in_background`
  (`ssh <alias> 'cd <wt> && make release-gates TAG=… PR=<n> > release-gates.out 2>&1'`) and wait for the
  notification; don't poll in a loop. Copy `release-gates/<TAG>/` back to the local checkout for `release-record`
  (or run it on the host). If the host needs `sudo`, give the user the command with `!`. To stop a remote process,
  kill it by PID or use a bracketed pattern (`pgrep -f "[e]2e_smoke"`): a bare `pkill -f <pattern>` matches the
  SSH shell that carries the pattern and kills that shell first.
- **Parallelism.** Curate the notes and prepare the single dependency-fixes PR while the remote gates run.
- **Report.** End with a checklist that marks each gate passed, pending or failed from its JSON (commit, timings,
  log path), and lists the PR links with their reviewer requests verified.
