---
description: Fix the open Dependabot alerts in one PR — lock bumps, security floors, verification, and a PR body in the repo's usual shape
argument-hint: "[alert numbers… | vX.Y.Z for a release's dependency-fixes PR] [--dry-run]"
allowed-tools: Bash(gh:*), Bash(git:*), Bash(make:*), Bash(uv:*), Bash(uvx:*), Bash(npm:*), Bash(npx:*), Bash(curl:*), Bash(jq:*), Read, Edit, Write, Grep, Glob
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

Target: **$ARGUMENTS** (default: every open alert on `londonaicentre/FLIP`).

- Alert numbers → fix only those.
- `vX.Y.Z` → this is a release's fixes: branch `dependabot-v<X.Y.Z>`, PR title `v<X.Y.Z> dependency fixes`. It stays
  out of the version-bump PR and merges before the release gates run, so they test it.
- `--dry-run` → stop after the triage table (step 2).

**All the alerts go in one PR**, never one PR per package or ecosystem. Read CONTRIBUTING.md *Dependency cooldown*
and *Security floors* first: they are the rules this command applies. Past examples: #1697 + #1703 (lock bumps, then
the floors the review asked for), #1674 + #1670 (53 alerts, then floors), #1388 (npm relock), #1038 (ghosts and
blocked alerts), #1555 (an alert cleared by removing an unused dependency).

## 1. Set up

- `git fetch origin`, then a new worktree off `origin/develop` on branch `chore/dependabot-fixes-<YYYY-MM-DD>` (or
  the release branch). Leave the main checkout and its untracked files alone. In the new worktree, run
  `cp .env.development.example .env.development`: the service configs read it, and without it the unit tests fail
  on pydantic `missing` errors.
- Use **uv 0.11.16** for every lock, the version CI's cooldown check pins (`secret-scanning.yml`): check `uv --version`
  and `./scripts/check-uv-version.sh`, else run `uvx uv@0.11.16 lock …`. An older uv silently drops the cooldown and
  rewrites the whole lockfile.
- For `flip-ui`, use Node 24 / npm ≥ 11.10. Older npm ignores `min-release-age` in `flip-ui/.npmrc`.

## 2. Triage

```bash
gh api --paginate 'repos/londonaicentre/FLIP/dependabot/alerts?state=open' --jq '.[] | [.number,
  .dependency.package.ecosystem, .dependency.package.name, .dependency.manifest_path, .dependency.scope,
  .security_advisory.severity, .security_advisory.ghsa_id, .security_vulnerability.first_patched_version.identifier]
  | @tsv'
```

Group the alerts by package, then put each group in one bucket:

| Bucket | Test | Action |
|---|---|---|
| **Fixable** | a patched release that is more than 72 h old | fix it (step 3) |
| **In cooldown** | the patched release is under 72 h old (PyPI: `curl -s https://pypi.org/pypi/<pkg>/<ver>/json \| jq -r '.urls[0].upload_time_iso_8601'`; npm: `npm view <pkg> time --json \| jq -r '."<ver>"'`) | leave it out and give the time it becomes installable. Never widen `exclude-newer` or `min-release-age` |
| **Ghost** | `manifest_path` is no longer tracked on `develop` (`git ls-files`) | no code change. List it to dismiss as `not_used` after the user agrees |
| **Major only** | the only patched release is a new major version that the packages depending on it don't accept yet (minimatch 11 in #1747; check each one with `npm view <parent>@latest dependencies.<pkg>`) | try forcing it (an npm override or a uv constraint) and keep it only if that project's lint, tests and build all pass. If any of them fails, treat it as Blocked and name the API that broke |
| **Blocked** | the fix conflicts with a pin (for example, torch capping setuptools); `uv lock` prints the conflict | Python: add a comment at the omission in that `pyproject.toml`, with the alert, the reason and the issue that unblocks it (#1038 style). npm: `package.json` can't hold comments, so give the reason in the PR body |
| **No patch** | `first_patched_version` is null | remove the dependency if nothing uses it (#1555). Otherwise treat it as Blocked |

Show the table (alert numbers, package, manifest, severity, current → target version, bucket). Stop there on
`--dry-run`.

## 3. Fix

**Python (uv).** In each affected project: `uv lock --upgrade-package <pkg>`. Never use a bare `uv lock --upgrade`,
which moves unrelated pins. Then apply the *Security floors* rule:

- Raise any floor the package already has (a dependency specifier or a `[tool.uv] constraint-dependencies` entry).
- In a project that ships (a service image, an FL image, `flip-utils`, `trust/xnat/export-mask`), add a transitive fix
  to `constraint-dependencies`, next to the other advisory entries, with the GHSA IDs in a trailing comment.
- In `flip-utils`, a vulnerable **direct** dependency (including the `[full]` extras) also needs its specifier raised.
  PyPI installs use plain pip, which reads neither `uv.lock` nor `[tool.uv]` (#1703, monai).
- Leave dev and docs tooling (`docs/`, `deploy/providers/AWS/`, dev-only groups) lock-only, unless it already has a
  floor.
- Projects that consume `flip-utils` by path need relocking when its constraints change. Run `make lock` (it uses
  the root `UV_PROJECTS` list) and check that the diff only touches what you meant to change.
- flwr moves everywhere at once or not at all: the `flwr/base` tag (`fl-services/flower/fl-base/Dockerfile`,
  `compose.secure.yml`) and the locked flwr in `fl-services/flower/fl-api-flower` and in `flip-utils`' dev group
  (#1801). `tests/test_fleet_proxy_allowlist.py` fails while they differ.

**npm (`flip-ui`).** For a transitive fix, add an entry to `overrides` in `package.json` with a range like
`">=<fixed> <<next major>"`. For a direct dependency, raise its range. Then run `npm install --package-lock-only` and
confirm with `npm ls <pkg>`. List every incidental relock movement in the PR body (#1388).

**`map-apps/*/requirements.txt`**: bump the pin.

**A major-version bump** (Pillow 12 → 13 in #1674, say) has to pass that project's test suite before it goes in.
Call it out in the PR body.

## 4. Verify

- `uv lock --check` passes in every `UV_PROJECTS` directory, using uv 0.11.16.
- Each affected Python project runs its own suite: `make -C <service> unit_test` for flip-api and the trust APIs,
  `make -C flip-utils unit-test`, `make -C fl-services/nvflare/fl-api-base local_test`, and pytest in
  `trust/xnat/export-mask` or the Flower FL API. Keep the pass counts for the PR body.
- `flip-ui`: `npm ci && npm run lint && npm run test:unit`.
- Rerun the alert query and check that every alert in the Fixable bucket now resolves to at least its patched version
  in the lockfile.

A failure means stopping and fixing it, or moving that package to Blocked with the reason. Never present it as
passing.

## 5. PR

- Commit with `git commit -s` (signed off, no co-author). Push. Open the PR against `develop` with the
  `dependencies` and `security` labels. A release's fixes PR also takes `ignore-for-release`.
- Title: `chore(deps): upgrade dependencies with open Dependabot alerts`, or `v<X.Y.Z> dependency fixes`.
- Body: fill in `.github/pull_request_template.md`:
  - **Description**: a table of Package | Lockfile | Version (old → new) | Severity | Alert, where Alert links
    `https://github.com/londonaicentre/FLIP/security/dependabot/<n>` and gives the GHSA. Add a floors table
    (where | floor | advisories), then any skipped alerts with their bucket and reason.
  - **Linked Issues**: "None: Dependabot alerts close on their own once `develop` is re-scanned after merge."
  - **Testing**: each command you ran, with its pass count.
- Watch CI until it is green. Then report the PR link, what got fixed, what was skipped and why, and the
  ghost-alert dismissals awaiting the user's go-ahead
  (`gh api -X PATCH repos/londonaicentre/FLIP/dependabot/alerts/<n> -f state=dismissed -f dismissed_reason=not_used -f dismissed_comment="…"`).
