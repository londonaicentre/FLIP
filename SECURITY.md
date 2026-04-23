# Security Policy

## Supported versions

| Version | Supported |
| --------- | ----------- |
| latest  | ✅        |

## Reporting a vulnerability

Do **not** open a public GitHub issue for security bugs.

Please use the private GitHub Security Advisory feature:

1. Go to the [Security tab](https://github.com/londonaicentre/FLIP/security) of this repository.
2. Click **"Report a vulnerability"**.
3. Fill in the advisory form with as much detail as possible (description, reproduction steps, impact, suggested fix).

The maintainers will acknowledge the report within **5 business days** and aim to release a fix within **90 days** under a coordinated-disclosure embargo.

If you are unable to use GitHub, you can contact the maintainers by email: `gstt.ai-centre@nhs.net`.

## Resolving a vulnerability (private PR workflow)

Fixes for an accepted GitHub Security Advisory (GSA) must be developed and reviewed **privately**, before the advisory is published. Do **not** open a public pull request against `develop`, push a fix branch to `origin`, or reference the CVE/GHSA ID in any public commit, issue, or PR while the advisory is under embargo.

The standard contribution rules in [`CONTRIBUTING.md`](CONTRIBUTING.md#submitting-pull-requests) (branch naming, DCO sign-off, tests, lint/type-check) still apply — the only things that change are **where** the branch and PR live, and **how careful** you are with public metadata.

### 1. Create a temporary private fork

1. Open the draft advisory in the [Security tab](https://github.com/londonaicentre/FLIP/security) and scroll to the **"Development"** panel.
2. Click **"Start a temporary private fork"**. GitHub creates a fork that is visible only to repository maintainers and anyone explicitly added as a collaborator on the advisory.
3. Clone that private fork locally in a **separate working tree** from your usual FLIP clone. Do not add the public `londonaicentre/FLIP` repo as a remote on the private-fork checkout — that avoids accidental `git push` to the public repo.

### 2. Branch and commit inside the private fork

- Base the fix branch on the advisory's target branch (usually `develop`).
- Follow the normal `[ticket_id]-[task_name]` convention, but pick a **neutral task name** that does not describe the vulnerability. Prefer the GHSA ID, e.g. `ghsa-xxxx-yyyy-zzzz-hardening`, over something like `fix-sql-injection-in-login`.
- Sign off every commit (`git commit -s`) — DCO still applies.
- Keep commit messages factual and neutral until the advisory is published. Detailed impact analysis, affected versions, and reproduction steps live in the advisory itself, not in git history.

### 3. Review and local verification

- Open the pull request **inside the temporary private fork**, not against the public repo.
- Request review from at least one other maintainer who is a collaborator on the advisory.
- GitHub Actions workflows from the public repo do **not** run on the private fork, so lint, type-check, and tests must be verified locally before review:

  ```bash
  make test         # from the affected service directory (ruff + mypy + pytest)
  make unit_test    # unit-only, if Docker isn't available
  ```

- Apply the same quality bar as a normal PR: tests that cover the fix, clean ruff/mypy/ESLint, and any documentation updates the change warrants.

### 4. Merge and disclose

1. Once approved, merge the PR **inside the private fork**. This stages the fix for release without exposing it.
2. Coordinate a disclosure window with the reporter and maintainers.
3. Publish the advisory. GitHub will offer to push the merged fix commits to the public repository at that moment — accept, and verify that `develop` (and any backport branches) now contain the fix.
4. If a CVE was not auto-assigned, request one via the advisory, then cut a release that includes the fix.

### 5. If you cannot use the private fork

- Maintainers without access to the private fork feature may apply the patch on a **local** clone and push only at disclosure time. Never stage security fixes on `origin` (public) before the embargo lifts.
- External contributors who cannot be added as advisory collaborators should send patches by email to `gstt.ai-centre@nhs.net`, signed off per DCO. A maintainer will apply them inside the private fork on the contributor's behalf and credit them in the advisory.
