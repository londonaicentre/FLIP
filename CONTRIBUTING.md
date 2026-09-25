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

# Contributing to FLIP

- [Introduction](#introduction)
- [The contribution process](#the-contribution-process)
  - [Preparing pull requests](#preparing-pull-requests)
    1. [Checking the coding style](#checking-the-coding-style)
    1. [Unit testing](#unit-testing)
    1. [Where does my test go?](#where-does-my-test-go)
    1. [Signing your work](#signing-your-work)
  - [Submitting pull requests](#submitting-pull-requests)

## Introduction

Welcome to the Federated Learning Interoperability Platform (FLIP)! We're excited you're here and want to contribute. This documentation is intended for individuals and institutions interested in contributing to FLIP. FLIP is an open-source project and, as such, its success relies on its community of contributors willing to keep improving it. Your contribution will be a valued addition to the code base; we simply ask that you read this page and understand our contribution process, whether you are a seasoned open-source contributor or whether you are a first-time contributor.

### Communicate with us

We are happy to talk with you about your needs for FLIP and your ideas for contributing to the project. One way to do this is to create an issue discussing your thoughts. It might be that a very similar feature is under development or already exists, so an issue is a great starting point.

When creating issues, please use the appropriate issue template:

- [**Bug Report**](https://github.com/londonaicentre/FLIP/issues/new?template=BUG-REPORT-FORM.yml) -- for reporting bugs and unexpected behaviour
- [**Feature Request**](https://github.com/londonaicentre/FLIP/issues/new?template=FEATURE-ISSUE-FORM.yml) -- for proposing new features or enhancements
- [**Task**](https://github.com/londonaicentre/FLIP/issues/new?template=TASK-ISSUE-FORM.yml) -- for general tasks that would not require any coding.
- [**Documentation**](https://github.com/londonaicentre/FLIP/issues/new?template=DOCUMENTATION-ISSUE-FORM.yml) -- for reporting documentation issues or proposing improvements to documentation.

### Project overview

FLIP is developed by the [London AI Centre](https://www.aicentre.co.uk/) in collaboration with Guy's and St Thomas' NHS Foundation Trust and King's College London. It is an open-source platform for federated training and evaluation of medical imaging AI models across healthcare institutions, while ensuring data privacy and security.

The FLIP repository is a mono-repo: it consolidates the Central Hub API, Trust APIs, UI, Docker deployment, **and**
the federated learning code (base library, FL services, and tutorials) for both NVFLARE and Flower. Both backends
are provisioned in-tree (gitignored) under `fl-services/<backend>/provision/`. See the single canonical
[repository layout](README.md#repository-layout) in the root README and the README within each area for details.

## Setting up the development environment

### Prerequisites

- A Linux development host; a CUDA-capable GPU is required for GPU-backed tutorials and training
- [Docker Engine](https://docs.docker.com/engine/install/) with Compose and Swarm mode. The trust
  slot-collision guard identifies a running stack's owning kit through Compose's
  `com.docker.compose.project.environment_file` container label (verified live on Compose v5.1.3);
  a Compose too old to record that label does not lose the protection — the guard fails closed,
  refusing the operation with an explicit "the kit that owns them cannot be identified" stop
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  on GPU hosts
- GNU Make, `jq`, and `curl`
- [Python 3.12 or 3.13](https://www.python.org/downloads/) and [uv](https://docs.astral.sh/uv/) **>= 0.10.0** —
  earlier uv cannot parse the `exclude-newer = "3 days"` cooldown (see
  [Dependency cooldown](#dependency-cooldown-supply-chain-protection)); it warns, ignores the setting and
  re-resolves `uv.lock` without any cooldown. `scripts/check-uv-version.sh` checks this at the two entry
  points that re-resolve a lockfile with your own uv — `make lock` and
  `fl-services/nvflare/provision/scripts/provision-network.sh`. It is not a global gate: a bare
  `uv sync`, `uv run --project` or `uv lock` run by hand is unguarded, so keep your uv current rather
  than relying on the check to catch you. (The `uv-lock` pre-commit hook is not a gap here — it pins its
  own uv and runs `uv lock --check`, which verifies and never rewrites.)
- The AWS CLI configured for SSO access to the development environment — `make up` needs it for the
  development S3 buckets, not to sign in: the local identity provider is Keycloak (see
  [Environment variables](#environment-variables)), and `make central-hub` boots the hub with no AWS account
- [act](https://github.com/nektos/act) if you want to run GitHub Actions locally
- **GHCR login** — `make up` pulls the repo-built service images from GitHub Container Registry by default, so authenticate once with a PAT that has `read:packages`:
  ```bash
  echo "$GHCR_PAT" | docker login ghcr.io -u <your-github-username> --password-stdin
  ```
  Building everything locally instead (no GHCR access needed) is `make up BUILD=true` — see [Running the stack](#running-the-stack-pull-vs-build) below.

### Recommended IDE Setup

The file [`recommended_extensions.vsix`](recommended_extensions.vsix) contains a bundle of recommended VS Code
extensions for FLIP development. Install with:

```bash
code --install-extension recommended_extensions.vsix
```

Key extensions include:

- `ms-vscode-remote.vscode-remote-extensionpack` — connect to Docker containers and remote servers via SSH for in-container development, avoiding the need to rebuild images on every change
- Python linting/formatting (ruff, mypy)
- Docker tooling

Other useful tools:

- [Postman](https://www.postman.com/) — API testing
- [Homebrew](https://brew.sh/) — package manager for macOS/Linux

#### Open the multi-root workspace (not the folder)

FLIP is a monorepo of independent Python sub-projects, each with its own `.venv` and `pyproject.toml`. Open it via the
checked-in [`flip.code-workspace`](flip.code-workspace) — **File → Open Workspace from File…** — rather than opening the
repo root as a plain folder. The multi-root workspace lets Pylance use each folder's own interpreter and Ruff its own
config; for example, `nvflare` imports resolve only when `fl-services/nvflare/fl-api-base` is using its own `.venv`.
Opening the repo root as a single folder applies one interpreter (flip-api) to every file, so cross-project imports such
as `nvflare` show up as unresolved.

After opening, confirm the per-folder interpreter with **Python: Select Interpreter** (it prompts for the folder first,
then the `.venv`), and run **Developer: Reload Window** if an import is still flagged.

#### Coding-agent instructions (`AGENTS.md`)

This repo's instructions for coding agents live in [`AGENTS.md`](AGENTS.md), with one more per service directory.
`AGENTS.md` is the cross-tool standard and is now the only copy — the `CLAUDE.md` twin that used to sit beside each one
has been removed.

Claude Code reads `AGENTS.md` only from **version 2.1.277** onward, so check `claude --version` and upgrade if you are
behind. Three situations silently give a session **no project instructions at all** — no error, and nothing in the
output to say so:

- a Claude Code older than 2.1.277;
- the `instructionFiles` option set to `claude-md`, which turns the fallback off (`/config` → Project instructions). It
  is a user or organisation-managed setting and per-project settings are not read for it, so this repo cannot correct it
  for you;
- Claude Code running via Bedrock, Vertex or Foundry, where `AGENTS.md` support does not exist at all. Those need a
  `CLAUDE.md`, which this repo no longer carries.

One trap is worth knowing about, because it looks like nothing is wrong: the fallback is decided per project, and *any*
CLAUDE-named file satisfies it. Keep a personal `CLAUDE.md`, `.claude/CLAUDE.md` or `CLAUDE.local.md` inside your
checkout and every `AGENTS.md` in the tree is skipped — you lose the repo's instructions wholesale, in favour of your own
notes. Keep personal instructions in `~/.claude/` instead of in the working copy.

### Python environment management

FLIP uses [UV](https://docs.astral.sh/uv) for all Python services. Each service has a `pyproject.toml` and a
`.python-version` file in its root directory.

To install dependencies for a service:

```bash
uv sync
```

To add a new dependency:

```bash
uv add <package-name>            # runtime dependency
uv add <package-name> --dev      # development-only dependency
uv add <package-name> --group <group>  # dependency in a named group
```

The `pyproject.toml` file is the source of truth for dependencies. The Python version in `.python-version` must match
the version used in the service's Dockerfile.

### Dependency cooldown (supply-chain protection)

Recent npm and PyPI supply-chain attacks follow a consistent pattern: a maintainer's credentials are compromised, a
malicious release is published, the community detects it, and the package is yanked — usually within a few hours. To
keep poisoned releases out of FLIP's CI, developer machines, and Trust-side containers, FLIP enforces a **72-hour
cooldown** on dependency installs:

> No FLIP build, CI or local, may install a Python or JavaScript package whose release timestamp on its upstream
> registry (PyPI / npm) is less than 72 hours old. This applies to direct **and** transitive dependencies.

The policy is enforced through native package-manager configuration:

- **uv (Python)** — every `pyproject.toml` sets `tool.uv.exclude-newer = "3 days"`, so `uv lock` and `uv add` never
  resolve a release younger than 72 hours (the `uv.lock` records this as a rolling `exclude-newer-span`). The
  **Dependency Cooldown Check** job in [`secret-scanning.yml`](.github/workflows/secret-scanning.yml) runs
  `uv lock --check` on every project, failing CI if a lockfile drifts from its manifest or was generated under a
  wider `exclude-newer` window than the committed manifest allows.
- **npm (JavaScript)** — `flip-ui/.npmrc` sets `min-release-age=3`, so `npm install` refuses to resolve a release
  younger than 72 hours. This key was introduced in npm 11.10, so `flip-ui/Dockerfile` and the `test_flip_ui.yml`
  workflow use Node 24 LTS (which ships npm >= 11.10); Node 22 LTS bundles npm 10.x and silently ignores the key.
  `flip-ui/package.json`'s `engines` field still permits older Node (`^20.19.0 || >=22.12.0`) for compatibility, but
  installing on one of those silently drops the cooldown rather than failing — so develop against Node 24 locally
  to match CI. CI installs use `npm ci`, which fails on any `package-lock.json` / `package.json` mismatch. npm only
  enforces `min-release-age` at lockfile-write time (`npm install <pkg>`), not when installing from a pinned
  `package-lock.json`, so the npm cooldown rests on `.npmrc` rather than a CI gate.

There is no automated dependency-update bot wired into the repo today. Dependency bumps are hand-rolled PRs; the
two layers above (uv `exclude-newer` and npm `min-release-age` at install time, `uv lock --check` in CI) catch a
too-fresh package regardless of how it arrived in the lockfile.

The cooldown applies automatically when you run `uv add <package>` or `npm install <package>` — a release younger
than 72 hours is simply not selected. Run `make lock` to refresh every `uv.lock` after a dependency change.

#### Emergency override

For a genuine same-day patch of an active CVE, the cooldown can be bypassed for the single package that needs it:

- **uv** — add an `exclude-newer-package` entry under `[tool.uv]` for that package (for example
  `exclude-newer-package = { "<package>" = "<recent-timestamp>" }`) and re-run `uv lock`. The entry is committed, so
  the exception is visible in the pull request and `uv lock --check` still passes.
- **npm** — run `npm install <package> --min-release-age=0`, which overrides the `.npmrc` setting for that one
  command.

Any override must be justified in the pull-request description. Use it only for security patches that genuinely
cannot wait 72 hours.

### Environment variables

Environment variables for local development are defined in [`.env.development.example`](.env.development.example). This file uses
dummy/safe credentials for local use and **must not be used in production**. It centrally configures all services.

To get started, copy the example file:

```bash
cp .env.development.example .env.development
```

Then generate the internal service key (also generated automatically by `make up`):

```bash
make generate-internal-service-key
```

This writes `INTERNAL_SERVICE_KEY` with `INTERNAL_SERVICE_KEY_HASH` into `.env.development` for
fl-server-to-hub authentication.

#### Updating an existing checkout

Three changes affect checkouts created before them. None is picked up automatically, because
`.env.development` and the trust kit files are gitignored and never rewritten for you.

| What changed | What to do |
| --- | --- |
| `NLB_SUBDOMAIN` is now a live assignment in `.env.development.example` | Add `NLB_SUBDOMAIN=<your-nlb-subdomain>` to your `.env.development`. `scripts/check_env_vars.py` is a pre-commit hook requiring every variable in the example file to be present in yours, and its regex matches real `^KEY=` assignments only — so a still-commented `# NLB_SUBDOMAIN=` fails your next commit, naming the variable. Nothing in a purely local stack resolves the value; it is required because `scripts/trust_kit_lib.py` lists it among the Hub-shared keys. |
| uv floor raised to **>= 0.10.0** | `uv self update` (or reinstall). Below the floor, `make lock` and the NVFLARE provisioning script refuse to run rather than silently re-resolving `uv.lock` without the cooldown. |
| `NUM_AVAILABLE_GPUS` defaults to `0` in the two shipped dev kit examples (`trust/.env.GSTT.development.example`, `trust/.env.KCH.development.example`) | Only those two pre-populated example kits were changed; the base template (`trust/.env.example`) that `make new-trust` scaffolds from still defaults to `1`, and an existing `trust/.env.<CODE>.<env>` keeps its own value regardless. On a GPU dev host, set `NUM_AVAILABLE_GPUS=1` in the kit to restore passthrough — `make up-trust` prints a warning naming the variable when it is zero, so this is not silent. |
| `MIN_CLIENTS` is removed everywhere (FLIP#1230) | Nothing to add. Delete the `MIN_CLIENTS=` line from your `.env.development` and from any `trust/.env.<CODE>.<env>` kit if you like — a leftover is ignored. The FL images no longer read it: the per-job quorum is the participating-trust count, which fl-api writes into every job (`min_clients = len(trusts)` on NVFLARE, `flip-min-clients` on Flower), so a hub-wide value could only ever veto a legitimate project (set to 3, a 2-trust project never starts) and never lowered anything. On stag/prod the `MIN_CLIENTS` GitHub environment variable is now unread and can be deleted. |

For the full local stack, replace every placeholder in these minimum groups before running `make up`:

| Group | Required development values |
| --- | --- |
| AWS session | `AWS_PROFILE`, `AWS_REGION` |
| Central Hub auth | `ADMIN_USER_PASSWORD` — the password of every seeded dev identity (the Keycloak realm imports it; the dev Cognito pool's seed admin logs in with it). Leave `AUTH_BACKEND` unset: dev defaults to `keycloak`, the identity-provider container in `deploy/compose.development.yml`, so no AWS account is needed to sign in |
| Cognito (optional) | `AUTH_BACKEND=cognito` plus `AWS_COGNITO_USER_POOL_ID` and `AWS_COGNITO_APP_CLIENT_ID` — only to develop against the dev Cognito pool, with an AWS SSO session. Staging and production accept no other value |
| Local secrets | `POSTGRES_PASSWORD`, a base64-encoded 32-byte `AES_KEY_BASE64` |
| Runtime S3 | `FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME`, `FLIP_FL_RESULTS_BUCKET_NAME`, `FLIP_APP_BUNDLES_BUCKET_NAME`, `AICENTRE_BUCKET_NAME` |
| XNAT artifacts | `FLIP_ARTIFACTS_BUCKET_NAME`, containing the versioned WAR and plugin set described in [`trust/xnat/README.md`](trust/xnat/README.md#plugins) |

Development uses these configured AWS services directly; there is no LocalStack fallback. Authorised FLIP developers
can use the shared development values. Other deployers should create their own resources with the
[Central Hub deployment guide](docs/source/deploy-flip/deploy-central-hub.rst).

**Email needs no configuration in development** (FLIP#919). flip-api defaults to `EMAIL_BACKEND=console` in dev, which
logs the would-be message (recipient, template name, non-secret payload) instead of calling SES — so the access-request
and XNAT-credentials paths work with no SES identity, verified address or templates. Staging and production keep
`EMAIL_BACKEND=ses` and still require `AWS_SES_ADMIN_EMAIL_ADDRESS` / `AWS_SES_SENDER_EMAIL_ADDRESS`; the setting is
type-narrowed in `ProdSettings`, so the console backend cannot be selected there. Invitations are the identity
provider's own, not SES's: under the default Keycloak backend dev has no mail server, so a user registered from the
Admin Area is given the shared dev password (`ADMIN_USER_PASSWORD`) as a temporary one (flip-api logs that it did,
never the password) — they sign in once with it, Keycloak's account console
(`http://localhost:8180/realms/flip/account`) asks for a new password (the UI links there when the sign-in answers
"Account is not fully set up"), then they sign in to FLIP. Under `AUTH_BACKEND=cognito` the user pool still sends
real invite and password-reset emails.

**Sign-in in development goes through Keycloak** (FLIP#919). `make up` starts a `keycloak` service that imports the
dev realm `deploy/keycloak/flip-realm.json` at every boot and keeps no volume. Sign in as any well-known dev identity
from `flip-api/src/flip_api/utils/constants.py` (e.g. `aicentreflip@gmail.com`) with `ADMIN_USER_PASSWORD`; roles are
granted by flip-api's boot seed as before. Keycloak's admin console is `http://localhost:8180/admin` (`admin`/`admin`
unless `KEYCLOAK_ADMIN_USERNAME` / `KEYCLOAK_ADMIN_PASSWORD` are set; `KEYCLOAK_PORT` moves the host port). After
editing the realm run `make reset-keycloak` — the import skips a realm that already exists. Not available in-app under
Keycloak, because the browser uses the OIDC password grant, which has no equivalent: TOTP enrolment/challenge (keep
`ENFORCE_MFA=false`, which the dev compose sets — `true` only logs a warning and locks browser users out),
forgot-password, and the admin "Reset password" button; use the Keycloak console for those. Everything else —
register, roles, enable/disable, MFA reset from the admin screen, projects, cohorts, uploads — works the same. Scripts
that sign in (`make e2e_smoke`, `make -C flip-api create_testing_projects`, `make seed-demo-projects`, `make demo-users`,
the demo recorder) go through the configured provider, so they need no AWS session either. Details:
[`deploy/keycloak/README.md`](deploy/keycloak/README.md).

Trusts are registered on the **running hub** with `make register-trusts` (shipped dev roster) or
`make register-trust KIT=<CODE>` (one trust), which inserts each `trust` row (with its
`api_key_hash`), claims an FL kit slot, and fills that trust's kit file `trust/.env.<CODE>.<env>`
carrying `TRUST_API_KEY` and `TRUST_INTERNAL_SERVICE_KEY`. `make up` runs `register-trusts`
automatically once the hub is up.

Docker services receive these variables via the `env_file` directive in the
compose file — avoid hardcoding values in Dockerfiles or compose files directly.

**Authentication environment variables:**

- `TRUST_API_KEY_HEADER` — HTTP header name for trust-to-hub authentication.
- `TRUST_API_KEY` — single per-trust plaintext API key. Lives only in that trust's kit file
  (`trust/.env.<CODE>.<env>`), written by `make register-trusts`; never on the hub.
- `INTERNAL_SERVICE_KEY_HEADER` — HTTP header name for fl-server-to-hub authentication.
- `INTERNAL_SERVICE_KEY` — internal service key used by the fl-server on the Central Hub.
- `INTERNAL_SERVICE_KEY_HASH` — hub-side SHA-256 hash of the internal service key.
- `TRUST_INTERNAL_SERVICE_KEY_HEADER` — HTTP header name for trust-internal service auth (default
  `X-Trust-Internal-Service-Key`). Sent by every caller (trust-api, imaging-api, fl-client) on every
  call to imaging-api or data-access-api.
- `TRUST_INTERNAL_SERVICE_KEY` — single per-trust plaintext key, in that trust's kit file
  (`trust/.env.<CODE>.<env>`), minted by `register_trust`. Read by every trust-internal container. The hub
  never sees it. Distinct from `INTERNAL_SERVICE_KEY*` (which protects fl-server → flip-api on the
  Central Hub). See the [public security model](docs/source/security.rst#trust-internal-service-authentication).

FL clients (trust side) intentionally do **not** receive Central Hub API credentials. Only the fl-server (on the Central
Hub) communicates with flip-api. FL clients relay metrics and exceptions to the fl-server, which forwards them.

**FL-specific environment variables:**

- `FL_PROVISIONED_DIR` — path to the NVFLARE or Flower provisioned workspace, derived per-backend by `deploy/fl_backend.mk` from `FL_BACKEND`. The Makefile automatically converts this to an absolute path (Docker requires absolute paths for volume mounts). This directory contains certificates, keys, `fed_client.json`, and other files generated during provisioning for each network. Both are now provisioned in-tree (gitignored): NVFLARE at `fl-services/nvflare/provision/workspace-dev`, Flower at `fl-services/flower/provision/creds`.
- `FL_API_PORT` — port for FL API services (default: `8000`).

### Setting up AWS access

Some services (e.g. `flip-api`) interact with AWS via `boto3` — in development that is S3 (model-file uploads,
FL results, app bundles), which `make up` needs credentials for. Signing in does not: the hub alone boots and
authenticates against the local Keycloak with no AWS account (`make central-hub`).

Configure AWS SSO:

```bash
aws configure sso
```

For headless/SSH environments, use the device authorization flow:

```bash
aws configure sso --use-device-code
```

Log in to AWS in a new terminal session:

```bash
aws sso login --profile <your-profile-name>
```

To avoid specifying the profile name on every command:

```bash
export AWS_PROFILE=<your-profile-name>
```

### GitHub Secrets for CI

The CI/CD pipeline requires GitHub repository secrets to run tests and deployments. See
[.github/SECRETS.md](.github/SECRETS.md) for the complete list, how to generate them, and security best practices.

### Running the CI pipeline locally

To debug failing CI jobs without pushing, use `act` (requires Docker):

```bash
make ci
```

This runs all jobs defined in `.github/workflows/` locally.

### CI checks on forks

Contributors work from a [fork](#the-contribution-process), and a fork's CI runs with the fork's own `GITHUB_TOKEN`
and **without** the upstream repository secrets. Workflows that **publish or deploy** therefore cannot run on a fork —
they would only ever fail trying to reach `londonaicentre`-owned resources — so each is guarded with
`if: github.repository == 'londonaicentre/FLIP'` and shows up as **skipped** (neutral, not a red failure) on fork
pushes. These are:

- **Image publishing** — `Build and Push NVFLARE/Flower FL Docker Images`, and the `orthanc`, `xnat-*`, `flip-api`,
  and `trust-*` GHCR build-and-push workflows.
- **Releases** — `release.yml` and `release-pypi.yml` (git tags, GitHub releases, PyPI publishing).

Everything that **validates** your change still runs, and a red result is a real failure to fix: lint,
type-checking, unit and integration tests, docs, Terraform validation, Helm tests, and secret scanning.
Coverage upload to Codecov is non-blocking (`fail_ci_if_error: false`), so a missing `CODECOV_TOKEN` on your fork
never fails an otherwise-green job.

### Why a test suite shows as skipped

Skipped is also the normal result for a service test suite your change does not touch. On a PR into `develop` the
seven service workflows (`test_flip_ui.yml`, `test_flip_api.yml`, the three `test_trust_*_api.yml`,
`test_trust_omop_db.yml` and `test_trust_data_tools.yml`) still start, but each gates its jobs on
[`pr_paths_changed.yml`](.github/workflows/pr_paths_changed.yml), which runs the suite only when a changed file
matches the paths that workflow covers — the same list as its push `paths:` filter, plus the gate itself. A PR into
`main` always runs everything. So a `flip-ui`-only change legitimately shows the six trust and flip-api suites as
skipped, and that is not a fork restriction, a missing check, or something to re-run: check the `changes / decide`
job, which prints the file that matched or the number of files that did not.

Two consequences worth knowing. A suite skips only if **none** of its paths matched, so if you believe your change
affects a suite that skipped, the fix is to add the path it consumes to that workflow's list — in **both** copies,
or `scripts/tests/test_pr_paths_changed.py` fails. And because the gate reads the PR's own file list, editing the
gate re-runs every suite.

### Checkov security lint (Terraform)

`validate_terraform.yml` carries a `Checkov Security Lint` job (FLIP#1052 + the FLIP#1058 triage) alongside
`fmt`/`validate`: a curated checkov check list runs statically over `deploy/providers/AWS/**` and **fails the
PR's CI** on a regression. It covers IAM policy content — overly-broad statements such as a wildcard `Resource`/`Action` on a
restrictable data-access action, data exfiltration or privilege-escalation shapes, on both policy syntaxes
(`data "aws_iam_policy_document"` blocks and `jsonencode()` policies) — plus a small promoted set of
infrastructure-posture checks (IMDSv2-only EC2, module version pinning, HSTS, WAF Log4j rule, SSM/KMS posture).
No cloud credentials are needed, and checkov already knows which AWS actions support no resource-level scoping
(e.g. `ssmmessages:*`, `ec2:Describe*`) — those wildcards pass without ceremony. Run it locally with
`make checkov-lint` from the repo root (deliberately not the `deploy/providers/AWS` Makefile, whose parse-time
env guard needs the gitignored deploy env files).

Deliberate breadth or posture is acknowledged **in-code, with a rationale**, never by weakening the check list:
put `# checkov:skip=<CHECK_ID>:<why this is deliberate>` inside the flagged resource/data block. The check
list — including the classes triaged in FLIP#1058 and deliberately *not* promoted — lives in
`deploy/providers/AWS/scripts/checkov_lint.sh`, which self-tests against a canary fixture before scanning so a
broken checkov install can never produce a vacuous green. The script's own guards (version pin, unknown check
IDs, skip rationale, canary) are regression-tested by `deploy/providers/AWS/scripts/tests/test_checkov_lint.sh` with `checkov` stubbed,
run by the same workflow's `Deploy script tests` job.

### Secret scanning (detect-secrets)

Two scanners run on every PR. TruffleHog (`--only-verified`) fails only on a credential it can confirm is live.
detect-secrets is the structural one — it flags anything *shaped* like a secret (keyword assignments, high-entropy
strings, JWTs, basic-auth URLs) — and since FLIP#1215 **CI enforces it**: the `Detect Secrets Scan` job runs
`detect-secrets-hook --baseline .secrets.baseline` over every tracked file (lockfiles excluded), the same entry
point and version (`1.5.0`) as the pre-commit hook, and fails on any finding the baseline does not already carry.
Before that the job ran a bare `detect-secrets scan`, which prints a report and cannot exit non-zero, and the
pre-commit CI job ran the hook with `|| true` — so nothing ever failed and ~110 unbaselined test literals had
accumulated. A stale baseline (an entry whose line moved or whose literal disappeared — hook exit 3) also fails
the job, with the rewritten baseline in the log: commit the rewrite deliberately rather than let coverage rot.
The hook re-serialises the whole file when it rewrites it, so `.secrets.baseline` is committed in the hook's own
key order (`version`, `plugins_used`, `filters_used`, `results`, `generated_at`, `indent=2`); a rewrite then
diffs only the moved `line_number`s and `generated_at`, and that is the expected shape when an unrelated edit to
a baselined file (a Cypress fixture, say) shifts its lines.

Locally the pre-commit hook scans only the files in the commit being made, so it will flag a dummy value the
first time you touch a file that already contains one. To allowlist a false positive:

1. **Inline pragma, preferred** — append `# pragma: allowlist secret` (YAML, Python, shell, Make) or
   `// pragma: allowlist secret` (TypeScript) to the line. Where that would push a Python line past 120 columns,
   put `# pragma: allowlist nextline secret` on its own line directly above instead (nothing else may precede
   it on that line). The comment documents the decision next to the value, survives edits, and needs no baseline
   entry.
2. **Baseline entry** — only for files that cannot carry a comment (JSON fixtures). Run
   `uvx --from detect-secrets==1.5.0 detect-secrets scan <file>` and copy that file's `results` entries into
   `.secrets.baseline` with `"is_secret": false`, keeping the hook's key order above. Never run a bare
   `detect-secrets scan --baseline .secrets.baseline` and commit the result — it re-derives every repo-wide finding
   and buries the real change — and never add entries for gitignored files.
3. **Filter** — for a whole class of false positives (Alembic revision ids, Excalidraw element ids) add a
   `should_exclude_line` / `should_exclude_file` pattern under `filters_used` in the baseline, as the existing ones do.

Whatever you allowlist must be a value that is safe in a public repository: a documented mock credential
(`minioadmin`, `test-*`, `plain-api`), a synthetic token, or a recorded response whose issuer and accounts are
confirmed gone (the 2022 Cognito fixtures under `flip-ui/test/cypress/fixtures/auth/` are baselined on that
basis — say so in the PR). Prefer a synthetic fixture for anything new; a recording of a live system is not a
false positive.

### Running the stack (pull vs. build)

In development (`PROD` unset), `make up` **pulls** the repo-built service images
(`flip-api`, `trust-api`, `imaging-api`, `data-access-api`, `orthanc`) from GHCR
instead of building them — each carries `image:` + `pull_policy: always` in the dev
compose, and your local `src/` is bind-mounted on top, so editing a `.py` still
hot-reloads against the pulled image. Startup is fast and matches the published
`:stag` artifact's environment.

```bash
make up                # pull GHCR images (default; requires `docker login ghcr.io`)
make up BUILD=true     # rebuild the repo-built services from local source instead
```

Use `BUILD=true` when you've changed **dependencies** (`uv.lock`/`pyproject.toml`),
system packages, or a `Dockerfile` — those live in the image layer, so a plain
`make up` (which pulls) won't pick them up. `flip-ui` always builds locally (it has
no GHCR image). Stag/prod (`PROD=stag|true`) are unaffected: they run the prod
compose with baked images and no bind-mounts.

## The contribution process

*Fork the repository before making changes* [Learn how to fork](https://help.github.com/en/github/getting-started-with-github/fork-a-repo). All contributions to the `develop` branch must be made via pull requests. This allows us to review your changes and ensure they meet our quality standards before merging them into the main codebase.

*Pull request early*, *commit often*. Don't wait until your changes are perfect before creating a pull request.
Commit your changes in small, logical chunks with clear commit messages. This makes it easier for reviewers to understand your changes and provide feedback.

We encourage you to create pull requests early. It helps us track the contributions under development, whether they are ready to be merged or not. [Create a draft pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/changing-the-stage-of-a-pull-request) until it is ready for formal review.

### Preparing pull requests

To ensure code quality, FLIP relies on linting tools ([ruff](https://docs.astral.sh/ruff)), static type analysis ([mypy](https://github.com/python/mypy)), as well as a set of unit and integration tests.

This section highlights all the necessary preparation steps required before sending a pull request. To collaborate efficiently, please read through this section and follow them. Make sure you configure your coding environment to follow the configurations in the `pyproject.toml` files so these are automatically enforced.

- [Checking the coding style](#checking-the-coding-style)
- [Unit testing](#unit-testing)
- [Signing your work](#signing-your-work)

#### Checking the coding style

FLIP uses [ruff](https://docs.astral.sh/ruff) for both linting and formatting. The ruff configuration is defined in the `pyproject.toml` file at the root of the repository and in each service directory.

The project-wide ruff rules are:

```toml
[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
preview = true
select = ['I', 'F', 'E', 'W', 'PT', 'UP006', 'UP007', 'UP035', 'UP042', 'UP045']
```

We also use [mypy](https://github.com/python/mypy) for static type checking.

Before submitting a pull request, ensure all linting passes by running the following commands from the relevant service directory:

```bash
# Run linting with auto-fix
uv run ruff check . --fix

# Run type checking
uv run mypy .
```

Most services have a `Makefile` with a `test` target that runs linting, type checking, and tests in sequence. For example, from a Python service directory:

```bash
make test
```

The `flip-ui` service uses `make unit_test` (Vitest) instead of `make test`.

Documentation follows the [Google style guide](https://google.github.io/styleguide/pyguide.html) for Python docstrings.

If your PR contains code inspired by other code bases, you MUST inform us in your PR so we can add proper references to the original code and evaluate whether it can be incorporated into our License framework.

#### Unit testing

*If it's not tested, it's broken*, so all new functionality should be accompanied by an appropriate set of tests. Existing tests throughout the services can serve as examples.

FLIP uses [pytest](https://docs.pytest.org/) for testing and [coverage.py](https://coverage.readthedocs.io/) for measuring code coverage.

Tests are located within each service's directory (e.g. `flip-api/tests/`, `trust/trust-api/tests/`). Test file names follow the `test_[module_name].py` or `[module_name]_test.py` convention.

To run tests for a specific service, navigate to the service directory and run:

```bash
uv run pytest --tb=short --disable-warnings --cov=src/ --cov-report=html --cov-report=term-missing
```

Or use the Makefile shorthand:

```bash
make test
```

This will run ruff, mypy, and pytest in sequence. The coverage report is generated in HTML format in the `htmlcov` directory.

To run unit tests across all services in the main repository from the root:

```bash
make unit_test
```

`make tests` is a narrower target that runs `flip-ui` unit and Cypress e2e tests followed by the full `flip-api`
test suite (ruff, mypy, and pytest).

For the FL base library in `flip-utils/`, unit tests can be run either directly with pytest or via the shipped
Makefile target, which runs the same ruff → mypy → pytest sequence as the other services (and as the
`flip-tests` job in `unit-tests.yml`):

```bash
cd flip-utils && uv run pytest tests/unit -s -vv
# or:
make -C flip-utils unit-test   # ruff --fix + ruff format --check + mypy + pytest with coverage
make -C flip-utils mypy        # type check only
make -C flip-utils format      # apply ruff format (format-check: check only)
```

See [`flip-utils/README.md`](flip-utils/README.md) for the FL package's tests, and
[`fl-services/nvflare/README.md`](fl-services/nvflare/README.md) for provisioning FL networks.

**Kubernetes chart testing**: The K8s Helm chart at `trust/deploy/helm/` can be tested with:

```bash
# Lint + render + schema validation
make -C trust/deploy/helm test

# Render all FL backend variants
make -C trust/deploy/helm template-all-backends

# Validate rendered templates against K8s schema (requires kubeconform)
make -C trust/deploy/helm validate

# Place this trust's FL participant kit onto the node, BEFORE deploying
make -C trust/deploy/helm stage-kit KIT_SRC=<kit dir> KUBE_CONTEXT=<ctx>

# Against a live cluster: drive a real C-STORE through the PACS and read XNAT's
# receiver log (a C-ECHO cannot see an importer crash — FLIP#1228)
make -C trust/deploy/helm smoke-cstore
```

`make -C trust/deploy/helm deploy` (and `deploy-trust-k8s`) take `HELM_TIMEOUT` (default
`30m`), the single budget for every wait on the `xnat-init` job — the Helm hook wait in
`deploy` and the `kubectl wait` in `xnat-init` alike. Raise it per site
(`HELM_TIMEOUT=45m`) rather than editing the Makefile; set below the job's real duration
it reports a Helm timeout *after* the new pod spec has been applied, which reads as a hook
failure rather than an un-deployed chart.

`stage-kit` is a prerequisite of deploying with `flClient.enabled`: the chart never fetches
the kit (a trust holds no FLIP AWS credentials), so `flClient.kitHostPath` must already exist
on the node. It reads the backend from the kit's own shape and chowns to that backend's uid.
The previous `make patch-aws-creds` target is gone along with the chart's in-cluster S3 fetch;
see "Upgrading an install that fetched its kit from S3" in the K8s README for the full list of
removed values.

The chart has a `check_status.py` smoke test script (which also compares the running xnat-web
pod's plugin jars against `xnat.web.plugins.urls`), a `scripts/smoke-cstore.sh` DICOM ingest
smoke, and a `sync_k8s_kit.py` script that syncs a registered trust's kit file (hub registration
itself still goes through `register_trust` / `make register-trusts`) into the chart's Kubernetes
Secret and a Helm values override. See the [K8s README](trust/deploy/helm/README.md) for details.

**Testing fixtures**: For testing APIs and integration tests, we use [pytest fixtures](https://docs.pytest.org/en/latest/how-to/fixtures.html). Shared fixtures are defined in `conftest.py` files. In some cases, [`factory_boy`](https://factoryboy.readthedocs.io/) is used to create test data following production data structures.

All new functionality should be accompanied by an appropriate set of tests. Existing tests throughout the services can serve as examples.

Add these sections to the service's `pyproject.toml` to configure pytest and coverage:

```toml
[tool.coverage.report]
exclude_lines = ["if __name__ == .__main__.:"]
omit = ["*.venv/*", "*/tests/*", "*/__init__.py"]

[tool.pytest.ini_options]
python_files = ["test_*.py", "*_test.py"]
addopts = []
filterwarnings = ["ignore::DeprecationWarning", "ignore::FutureWarning"]
```

#### Where does my test go?

A test belongs in `tests/integration/` **if and only if it touches a real backing service**. Examples of "real backing service":

- A real Postgres (via the `session` fixture or Testcontainers)
- A real AWS service (S3, Cognito, SES) or a real identity provider (Keycloak under Testcontainers)
- A running sibling API (trust-api, data-access-api, etc.) reachable over HTTP
- A real Orthanc / XNAT / OMOP fixture

If your test mocks **all** external dependencies (database session, HTTP client, AWS clients, sibling APIs), it's a unit test — put it in `tests/unit/`, mirroring the source layout (e.g. tests for `src/flip_api/user_services/set_user_roles.py` go in `tests/unit/user_services/test_set_user_roles.py`).

FastAPI `TestClient` on its own does **not** make a test "integration" — what matters is whether the dependencies it transitively hits are real or mocked. A `TestClient`-based test that overrides every dependency (via `app.dependency_overrides`) and patches the DB session is a unit test; one that runs against an un-overridden real Postgres is an integration test.

This rule applies across all services: `flip-api/tests/`, `trust/trust-api/tests/`, `trust/imaging-api/tests/`, `trust/data-access-api/tests/`, etc.

The same mirroring holds everywhere, not just in the services: a test sits in the `tests/` directory of the tree it tests, at the same relative path, named `test_<file>.py` after the one file it exercises — `trust/deploy/helm/scripts/generate_values.py` → `trust/deploy/helm/tests/scripts/test_generate_values.py`. For a file that is not Python, name the test after the file: a Makefile's contract tests are `test_makefile.py` in the `tests/` beside it (`tests/`, `trust/tests/`, `trust/xnat/tests/`, `deploy/providers/AWS/tests/`), and workflows and actions are tested under `.github/tests/` (`workflows/test_release.py` for `release.yml`, `actions/test_release_tag_guard.py` for the `release-tag-guard` action). The repo-level `tests/` and `.github/tests/` trees run in `.github/workflows/test_trust_kit_scripts.yml`.

##### Tests for FL tutorials and app templates

Two trees sit outside any service and have their own home. `fl-tutorials/tests/` carries **two** suites, split at `tests/datasets/` because the two halves need different dependencies — `make -C fl-tutorials test` runs ruff plus both, and `.github/workflows/fl-tutorials-tests.yml` runs the same on every PR touching `fl-tutorials/**`:

- **`fl-tutorials/tests/`, minus `tests/datasets/`** — the CPU-only suite over the tutorial apps' transform chains (`make -C fl-tutorials pytest`). A test belongs here if it can assert on tutorial code with **no GPU, no dataset download, no FL image and no network** — transform composition, import-time correctness, and what the preprocessing chain actually feeds the model. Fixtures are synthesised in-process (see `fl-tutorials/tests/dicom_phantom.py`), never committed as data. Anything that needs real training to observe — convergence, metric values, multi-round behaviour — belongs instead with the GPU simulator harness (`make -C fl-tutorials run-tutorial`), which is not run in CI.
  The suite runs in **flip-utils' environment** (`flip-utils[full]`), which is what the FL images give these apps at runtime; it deliberately has no `pyproject.toml` of its own, and the per-tutorial `uv` environments are the wrong target (`arkplus_fine_tuning/pyproject.toml` does not declare `monai`, so that environment cannot import its own `data_utils.py`).
- **`fl-tutorials/tests/datasets/`** — the CPU-only suite over `fl-tutorials/datasets/**`, the mock-OMOP generation tooling (`make -C fl-tutorials pytest-datasets`). Same no-GPU/no-download/**no-network** rule, with fixtures built in-process. It runs against **each dataset's own uv project**, one pytest invocation per entry in `DATASET_TEST_PROJECTS`, rather than in flip-utils' environment: this is workstation tooling that never runs on an FL image and has no business pulling `pandera`/`sqlglot` into the FL runtime environment. The per-project split is also the only thing in CI that checks a dataset's `pyproject.toml` declares what its code actually imports. `tests/datasets/` anchors its own pytest rootdir (`tests/datasets/pytest.ini`) so the tutorial-app `conftest.py`, which imports monai and pydicom at module scope, is not loaded into these runs. Anything needing the published export — the end-to-end verification gate — is a Make target (`make -C fl-tutorials reproduce-<project>-omop`), not a test: it reaches the network. See `fl-tutorials/tests/README.md` for the full rationale and `fl-tutorials/datasets/README.md` for the generation and verification targets.
- **`fl-apps/`** — has no pytest suite; its invariant is the required-files manifest, checked by `fl-apps/check_required_files.sh` (pre-commit + `.github/workflows/fl-apps-check-required-files.yml`). Files that must stay byte-identical to another file — the Flower tutorial copies of the `fl-apps/flower/` templates, and the shared Ark+ evaluation sources — are pinned in `scripts/check_tutorial_sync.sh`.

##### flip-api: real-Postgres integration tests via Testcontainers

`flip-api/tests/integration/` boots a throwaway `postgres:16-alpine` container per pytest session via [testcontainers-python](https://github.com/testcontainers/testcontainers-python) (`tests/integration/conftest.py`). The fixture builds the schema by running the **Alembic migrations** (`alembic upgrade head`) — the same DDL dev/prod apply at boot — then seeds permissions / roles / role-permissions once, and truncates per-test tables between tests. Both the existing `session` fixture and FastAPI's `Depends(get_session)` are rewired at the throwaway DB, so a new test only needs to request `session` (raw SQL access) and/or `client` (`TestClient` against the same DB) — no per-test setup required.

CI runs these via `make integration_test` from `flip-api/`. Docker is preinstalled on `ubuntu-latest`, so no `services:` block is needed in the workflow. AWS-backed integration tests (Cognito, S3, SES) run against moto's in-process fake through the session-scoped `aws_mock` fixture (`test_cognito_round_trips.py`, `test_s3_round_trips.py`, `test_ses_round_trips.py`), and `test_keycloak_round_trips.py` boots the pinned Keycloak image under Testcontainers with the committed dev realm (`deploy/keycloak/flip-realm.json`), so the Keycloak provider runs end-to-end — a real register and delete, and a token from Keycloak's password grant through `verify_token`. The AWS-free boot path itself is proven by `.github/workflows/local_auth_smoke.yml`, which starts flip-db, keycloak and flip-api from the dev compose on a runner with no AWS credentials and runs `flip-api/tests/local_auth_smoke.py` to sign in as the seeded admin.

##### flip-api: database migrations (Alembic)

flip-api's PostgreSQL schema is owned by Alembic, not `SQLModel.metadata.create_all`. **Any schema-affecting change to `db/models/*.py` must ship a migration revision** in the same PR — run `make migration MESSAGE="..."` from `flip-api/` (flip-db must be up), review the autogenerated file (native PG enums + the `ARRAY(UUID)` column), and apply it with `make migrate`. The integration suite builds its schema from these migrations, and `tests/integration/test_migrations.py` is a **drift guard**: a model change without a matching revision makes the suite fail. See [`flip-api/README.md`](flip-api/README.md#database-migrations) for the full workflow and the enum gotchas.

#### Signing your work

FLIP enforces the [Developer Certificate of Origin](https://developercertificate.org/) (DCO) on all pull requests. All commit messages should contain the `Signed-off-by` line with an email address.

Git has a `-s` (or `--signoff`) command-line option to append this automatically to your commit message:

```bash
git commit -s -m 'a new commit'
```

The commit message will be:

```bash
    a new commit

    Signed-off-by: Your Name <yourname@example.org>
```

### Submitting pull requests

All code changes to the `develop` branch must be done via [pull requests](https://help.github.com/en/github/collaborating-with-issues-and-pull-requests/proposing-changes-to-your-work-with-pull-requests). All PRs should be associated with an issue.

1. Create a new ticket or take a known ticket from [the issue list](https://github.com/londonaicentre/FLIP/issues).
1. Check if there's already a branch dedicated to the task.
1. If the task has not been taken, [create a new branch](https://help.github.com/en/github/collaborating-with-issues-and-pull-requests/creating-a-pull-request-from-a-fork)
named `[ticket_id]-[task_name]`.
For example, branch name `19-ci-pipeline-setup` corresponds to issue #19.
The new branch should be based on the latest `develop` branch.
1. Make changes to the branch ([use detailed commit messages if possible](https://chris.beams.io/posts/git-commit/)).
1. Make sure that new tests cover the changes and the changed codebase passes all tests locally (see [Unit testing](#unit-testing)).
1. Run linting and type checking before pushing (see [Checking the coding style](#checking-the-coding-style)).
1. [Create a new pull request](https://help.github.com/en/desktop/contributing-to-projects/creating-a-pull-request) from the task branch to the `develop` branch, with a detailed description of the purpose of this pull request.
1. Check [the CI/CD status of the pull request](https://github.com/londonaicentre/FLIP/actions), make sure all CI/CD tests pass.
1. Wait for reviews; if there are reviews, make point-to-point responses, make further code changes if needed.
1. If there are conflicts between the pull request branch and the `develop` branch, pull the changes from `develop` and resolve the conflicts locally.
1. Reviewer and contributor may have discussions back and forth until all comments are addressed.
1. Wait for the pull request to be merged.

## Cutting a release

Releases are cut from `main`. The version is set in the root `pyproject.toml`, and merging to `main` triggers [`.github/workflows/release.yml`](.github/workflows/release.yml), which reads that version, creates a `v<MAJOR.MINOR.PATCH>` git tag, and publishes a GitHub Release with auto-generated notes. On the same merge, the per-service `.github/workflows/docker_build_*.yml` workflows rebuild every service and push the `:prod` image tag (alongside `:<sha>`) to GHCR. There is no separate release-publishing step beyond merging to `main`.

### Two release trains, two tag namespaces

A push to `main` cuts **two independent releases**, from two independently-versioned artefacts. They must never share a tag namespace: each workflow skips its own release when it finds its tag already present, so a version number claimed by one artefact would silently suppress the other's release — or, for flip-utils, its PyPI publish.

| Artefact | Version source | Workflow | Tag | Release title |
| --- | --- | --- | --- | --- |
| FLIP platform | root [`pyproject.toml`](pyproject.toml) | [`release.yml`](.github/workflows/release.yml) | `v<X.Y.Z>` | `Release v<X.Y.Z>` |
| flip-utils (PyPI `flip-utils`) | [`flip-utils/flip/__init__.py`](flip-utils/flip/__init__.py) | [`release-pypi.yml`](.github/workflows/release-pypi.yml) | `flip-utils-v<X.Y.Z>` | `flip-utils v<X.Y.Z>` |

The `flip-utils-` prefix is what keeps them apart: every `git tag --list 'v*.*.*'` lookup in the release and preview workflows matches platform tags only, so each train's changelog spans its own history. **Do not drop the prefix or widen those globs** — the two version sequences advance independently and will overlap.

> **Historical note:** flip-utils 0.4.1 was released just before this split and originally tagged `v0.4.1`, in the platform namespace. It was retro-tagged as `flip-utils-v0.4.1` (same commit, `c55f79e8`) and the old tag deleted, so the two namespaces are clean from `v0.4.0` / `flip-utils-v0.4.1` onwards. The published PyPI artefact was never affected.

See [flip-utils and the PyPI release path](#flip-utils-and-the-pypi-release-path) below and [`flip-utils/CONTRIBUTING.md`](flip-utils/CONTRIBUTING.md) for the flip-utils train in detail.

### Versioning

FLIP follows [Semantic Versioning](https://semver.org/). The version in the **root** [`pyproject.toml`](pyproject.toml) is the FLIP release version — it is what `release.yml` reads to create the git tag.

Separately, [`flip-utils/flip/__init__.py`](flip-utils/flip/__init__.py) carries `__version__`, the version of the published `flip-utils` package. It is what `release-pypi.yml` reads, and it is **not** required to track the root version.

Each service has its own version string:

- [`flip-api/pyproject.toml`](flip-api/pyproject.toml)
- [`flip-ui/package.json`](flip-ui/package.json)
- [`trust/trust-api/pyproject.toml`](trust/trust-api/pyproject.toml)
- [`trust/imaging-api/pyproject.toml`](trust/imaging-api/pyproject.toml)
- [`trust/data-access-api/pyproject.toml`](trust/data-access-api/pyproject.toml)

These are **independent**. Bump a service's version only when *that service* has user-visible changes, applying SemVer to the service alone. Services are not aligned with the root version on every release — a release where only `flip-ui` changed bumps the root and `flip-ui/package.json`, and nothing else. Per-service versions are informational (deployments select images by tag, not by version string), but keeping them honest makes them useful for audit and changelog scope. What a running container *reports* as its version is different: the four service images (flip-api, trust-api, imaging-api, data-access-api) bake `FLIP_RELEASE` — the `v<X.Y.Z>` release tag, or the `sha-<short7>` tag of a branch build — and their `/health` returns that, so the Connection Status page names the build a site runs rather than a pyproject number two builds can share (FLIP#1204). A local build carries no `FLIP_RELEASE` and falls back to the pyproject version.

### Pre-release checklist

Before opening the release PR from `develop` to `main`:

- `develop` is green in [CI](https://github.com/londonaicentre/FLIP/actions).
- All PRs intended for this release are merged into `develop` and carry an appropriate label. The release-notes categories come from [`.github/release.yml`](.github/release.yml): `enhancement` / `feature`, `bug` / `fix`, `documentation` / `docs`, `ci` / `build`, `chore` / `dependencies`. PRs labelled `ignore-for-release` are excluded.
- Bump the `version` in the root `pyproject.toml` to the new release version. Additionally bump the `version` in any service file (`flip-api/pyproject.toml`, `flip-ui/package.json`, `trust/*/pyproject.toml`) whose code changed in this release, per the independent-SemVer rule above. Leave unchanged services alone.
- If `flip-utils/**` changed in this release, bump `__version__` in [`flip-utils/flip/__init__.py`](flip-utils/flip/__init__.py) — [`check-version-bump.yml`](.github/workflows/check-version-bump.yml) fails the `develop` → `main` PR unless it is valid semver and strictly higher than the latest `flip-utils-v*.*.*` tag. It need not match — or differ from — the root version; the two trains tag in separate namespaces (see [flip-utils and the PyPI release path](#flip-utils-and-the-pypi-release-path)).
- Curate the release-notes header in [`.github/RELEASE_NOTES_TEMPLATE.md`](.github/RELEASE_NOTES_TEMPLATE.md) — Highlights, Breaking Changes, **Site upgrade**, New Features, Bug Fixes. Editing the file is the only way to change those sections; the preview comment on the PR is regenerated from it on every push. The *Site upgrade* section is the prompt trust operators act on (FLIP#1204): say whether the upgrade is required, the ordering (hub first / sites first / one Deployment-Mode window — a flag-day such as FLIP#1179's cipher change or an FL-framework bump is the latter), and whether a **refreshed kit** is needed because the Hub-shared block changed.
- Run `make unit_test` and `make integration_test` locally.

### Cutting the release

1. From a branch off `develop`, commit the version bumps above and open a PR targeting `develop` with title `Release v<X.Y.Z>`.
1. Once that merges and CI is green, open a PR from `develop` to `main`. [`validate_branch_origin.yml`](.github/workflows/validate_branch_origin.yml) rejects any PR to `main` that does not come from `develop`.
   **Merge it with a merge commit — never squash or rebase.** A squash leaves `main` with `develop`'s content but none of its history, so the *next* release PR conflicts on every file touched since the previous real merge (v0.5.0 was squashed and v0.6.0 hit 168 spurious conflicts). If that has already happened, reconcile once with `git merge -s ours --no-ff origin/main` on `develop` — it records `main` as an ancestor without changing a file — through a PR into `develop`.
1. On that PR, check the automated gates before merging:
   - [`pr-release-notes-preview.yml`](.github/workflows/pr-release-notes-preview.yml) posts a **release-notes preview** comment — the rendered template header plus the generated changelog — and updates it in place on every push. Read it as the last check that the notes are right.
   - [`check-version-bump.yml`](.github/workflows/check-version-bump.yml) and [`check-package-metadata.yml`](.github/workflows/check-package-metadata.yml) run when `flip-utils/**` changed.
1. On merge to `main`:
   - [`release.yml`](.github/workflows/release.yml) reads the root `pyproject.toml`, creates the `v<X.Y.Z>` git tag (the tag it pushes is on `main` by construction, which is what the image workflows' release-tag guard checks), and publishes the GitHub Release named `Release v<X.Y.Z>` with auto-generated notes.
   - [`release-pypi.yml`](.github/workflows/release-pypi.yml) reads `flip-utils/flip/__init__.py` and, if that version is not yet tagged, lints + tests + builds the package, publishes it to PyPI via OIDC trusted publishing, tags it, and publishes a GitHub Release named `flip-utils v<X.Y.Z>` with the template header, the generated changelog, and the build artifacts attached.
   - Every `docker_build_*.yml` workflow under [`.github/workflows/`](.github/workflows/) rebuilds its service and pushes the `:prod` and `:<sha>` tags to GHCR.
   - `release.yml` then **dispatches** every image workflow — `docker_build_*.yml` and both `fl-docker-build-*.yml` — at the new tag (`gh workflow run <wf> --ref v<X.Y.Z>`), building every image at the release commit, unfiltered, and pushing `:v<X.Y.Z>` (FLIP#1204). This is the tag hub and sites deploy: a release is one identity across the whole stack, not a different `sha-` per service. The image workflows also carry a `push.tags: ['v*.*.*']` trigger, but it cannot serve the real release: the tag is pushed with the workflow's own `GITHUB_TOKEN`, and GitHub starts no workflow for an event created that way (only `workflow_dispatch` / `repository_dispatch` are exempt) — the push trigger is what a **hand-pushed** tag uses, i.e. a release candidate. `.github/tests/workflows/test_release.py` holds the dispatch roster to the exact set of publishing workflows, so one added without being listed fails CI.
1. Verify on the [Releases page](https://github.com/londonaicentre/FLIP/releases) that the new release exists and the notes look right — the curated header (including *Site upgrade*) now sits above the generated changelog. Verify on [GHCR](https://github.com/orgs/londonaicentre/packages) that every image carries `:v<X.Y.Z>` (twelve workflows; `.github/tests/workflows/test_docker_build.py` and `test_fl_docker_build.py` guard the triggers) and that the `:prod` tags were updated. If the package was released, verify it on [PyPI](https://pypi.org/project/flip-utils/).

### Rolling the release out

The release is not deployed by merging. Two steps, in this order:

1. **Hub.** Enable Deployment Mode, wait for `GET /fl/quiesce` to report no busy net, then `make -C deploy/providers/AWS deploy-centralhub PROD=true TAG=v<X.Y.Z>` (the guard accepts `sha-<short7>` or `v<X.Y.Z>`), then disable Deployment Mode. The UI is still `make deploy-ui PROD=true` (FLIP#1186). If the apply that accompanied the merge rotated any Hub-shared value — the AES key, the FL kit date — reconcile the operator env from deployed state and re-issue kits **before** telling sites to upgrade: `deploy/providers/AWS/scripts/reconcile_ci_env.py --env prod --profile prod --compare .env.production` → `make sync-trust-kits PROD=true` → `make -C deploy/providers/AWS package-onprem-trust-kit KIT=<CODE>`.
1. **Sites, operator-triggered.** Each site's operator moves their FLIP checkout to the tag (`git fetch --tags origin && git checkout v<X.Y.Z>` — the compose files, Makefiles and the verb itself come from the checkout, and the verb refuses a release tag from any other checkout) and runs `make upgrade-onprem-trust KIT=<slot>` (Kubernetes: `upgrade-trust-k8s`; EC2: `upgrade-trust-ec2`, both from a checkout at the tag), which defaults to the release the hub now reports. Nothing pushes upgrades to sites; the release notes' *Site upgrade* section is the prompt, and the Connection Status drawer shows which containers are still on another build. The full operator runbook is `docs/source/sys-admin/admin-upgrading-sites.rst`.

### Release notes

There is no `CHANGELOG.md` — the GitHub Releases page is the changelog. Release notes come from two pieces:

- **The generated changelog** — GitHub's release-notes API lists every PR merged since the previous `v*.*.*` tag, plus a contributors section, categorised by PR label according to [`.github/release.yml`](.github/release.yml): `enhancement` / `feature`, `bug` / `fix`, `documentation` / `docs`, `ci` / `build`, `chore` / `dependencies`, then Other Changes. PRs labelled `ignore-for-release` are excluded. Curating this means labelling each PR correctly **before** it merges into `develop` — it cannot be fixed at release time.
- **The hand-written header** — [`.github/RELEASE_NOTES_TEMPLATE.md`](.github/RELEASE_NOTES_TEMPLATE.md), with `{{VERSION}}`, `{{TAG}}`, and `{{PREV_TAG}}` substituted. Edit this file on the release branch to fill in Highlights and the Breaking Changes / New Features / Bug Fixes summaries.

Both are rendered into the preview comment on the `develop` → `main` PR, and both go into the releases published by `release.yml` and `release-pypi.yml` — the template header above the generated changelog. (Until FLIP#1204 the platform release carried the generated changelog only, so the curated sections never reached the page trust operators read.)

### flip-utils and the PyPI release path

`flip-utils` is the only component published as a package ([`flip-utils` on PyPI](https://pypi.org/project/flip-utils/)), so it has a release path of its own driven by `__version__` in [`flip-utils/flip/__init__.py`](flip-utils/flip/__init__.py) rather than the root `pyproject.toml`.

The two paths use **separate tag namespaces** — `v<X.Y.Z>` for the platform, `flip-utils-v<X.Y.Z>` for the package (see [Two release trains, two tag namespaces](#two-release-trains-two-tag-namespaces) above) — and each skips when its own tag already exists. Bump only the root version and you cut a platform release; bump only `__version__` and you cut a package release. Because the namespaces no longer collide, bumping both in the same release PR is safe: the two workflows tag independently and neither can suppress the other, even when the version numbers happen to match.

Full detail — the per-PR gates, the trusted-publishing setup, and the `release.sh` manual fallback — is in [`flip-utils/CONTRIBUTING.md`](flip-utils/CONTRIBUTING.md).

### Deploying the release

Hub infrastructure is applied by CI on the merge to `main` (`terraform_apply.yml`, FLIP#962); the hub's ECS services move to the release with `make -C deploy/providers/AWS deploy-centralhub PROD=true TAG=v<X.Y.Z>` and the sites follow on their operators' command — see [Rolling the release out](#rolling-the-release-out) above and [`deploy/providers/AWS/README.md`](deploy/providers/AWS/README.md) for the hub-side detail (Deployment Mode, rollback). For staging, no release tag is required: merging to `develop` publishes `:stag` images and applies stag automatically; a stag site can be moved to a specific build with `TAG=sha-<short7>`.

### Hotfixes

For an urgent fix on `main` without pulling in unrelated `develop` work:

1. Branch from `main`, apply the fix, bump the patch version in the root `pyproject.toml` (and any affected service).
1. Open a PR targeting `main`. On merge, the same automation kicks in — `release.yml` tags `v<X.Y.Z+1>` and `docker_build_*.yml` rebuilds `:prod`.
1. Forward-port the fix to `develop` so the next regular release includes it.

### Testing a release candidate before merge to main

Two ways, depending on how much of the stack you need.

**One or two services** — branch builds **do not** auto-publish to GHCR, so trigger the relevant build workflows by hand:

```bash
gh workflow run docker_build_flip_api.yml --ref <branch-name>
gh workflow run docker_build_trust_trust_api.yml --ref <branch-name>
# ...one per service whose image you want to test
```

Wait for green completion, then point your `.env` file's `DOCKER_TAG` at the sanitized branch name (the per-service workflows publish a `:<branch>` tag on every push).

**The whole stack at one tag — a release candidate** (FLIP#1204). Push a *pre-release* tag at the release PR's head, `v<X.Y.Z>-rc.<N>`; every image workflow builds and pushes `:v<X.Y.Z>-rc.<N>`, exactly as the stable tag will, and a staging site can pin it with `make upgrade-onprem-trust … TAG=v<X.Y.Z>-rc.<N>`. Delete the git tag once the candidate is judged (`git push origin --delete v<X.Y.Z>-rc.<N>`): `release.yml` and the release-notes preview find the previous release with `git tag --list 'v*.*.*'`, and a leftover candidate would become the next release's `PREV_TAG`, shrinking its changelog to the span since the rc. The images can stay — they are plainly pre-release and nothing points sites at them.

A tag push is not gated by branch protection — anyone with write access can push a `v*` tag at any commit — so every image workflow checks **what a stable tag may point at** itself (`.github/actions/release-tag-guard`): a `v<X.Y.Z>` whose commit is not on `main` fails the build rather than publishing release-looking images from unreleased code. Pre-release tags pass — that is the candidate path above. `.github/tests/actions/test_release_tag_guard.py` holds every image workflow to it. *Who* may create a `v*` tag is deliberately left to write access (a repository tag ruleset could restrict it to admins, but GitHub does not let the built-in Actions app bypass one, so `release.yml` would need its own GitHub App token first — not worth it for the current roster).

A candidate proves the **push** path. The real release goes through the **dispatch** path in `release.yml`, whose image runs arrive as `workflow_dispatch` on the tag ref rather than as a tag push — so when proving a change to the image workflows, dispatch one at the candidate tag as well: `gh workflow run docker_build_flip_api.yml --ref v<X.Y.Z>-rc.<N>` must produce the same `:v<X.Y.Z>-rc.<N>` image.

## Adding a new service

To extend the platform with a new service, add a definition to the appropriate Docker Compose file:

```yml
# deploy/compose.development.yml
services:
  new-service:
    build:
      context: ../path/to/service
      dockerfile: Dockerfile
    ports:
      - "8080:8080"
    volumes:
      - ../path/to/service:/app
    depends_on:
      - flip-db
    env_file:
      - ../.env.development
```

Create a directory following the standard service layout:

```
new-service/
├── src/
│   └── new_service/
├── tests/
├── Dockerfile
├── Makefile
├── pyproject.toml
└── .python-version
```

Optionally add Makefile shortcuts at the repository root:

```makefile
new-service:
    docker compose -f deploy/compose.development.yml up -d new-service
```

## Creating test data for manual testing

To create projects in various pipeline stages (`unstaged`, `staged`, `approved`) for manual testing:

```bash
make -C flip-api create_testing_projects
```

The script signs in through the configured identity provider: the local Keycloak needs no AWS session,
`AUTH_BACKEND=cognito` does.

To clean up the test data:

```bash
make -C flip-api delete_testing_projects
```

These are also available as VS Code tasks via **Terminal > Run Task** — look for `Create testing projects` and
`Delete testing projects`.

## Building the documentation

The ReadTheDocs site is Sphinx over `docs/`; build it locally with `make -C docs docs` (see
[`docs/README.md`](docs/README.md)). Two things about that build are easy to trip over:

- It needs **graphviz** (`dot` on PATH). `docs/source/conf.py` renders the Central Hub AWS diagrams from
  `deploy/providers/AWS/architecture/central_hub.py` at build time — no PNG is committed for the site — and
  fails loudly without it. `FLIP_DOCS_SKIP_DIAGRAMS=1 make -C docs docs` gives a text-only build on a host
  without graphviz. ReadTheDocs and the docs CI job install graphviz themselves.
- Those diagrams are **drift-guarded against the Terraform**: `deploy/providers/AWS/tests/test_architecture_diagram.py`
  fails when a drawn resource disappears from the `.tf` files or a load-bearing one (an ECS service, bucket,
  load balancer, …) is added without being drawn. A Terraform change of that kind updates
  `TERRAFORM_ADDRESSES` in the script in the same PR, then `make aws-diagram` refreshes the two committed
  copies the AWS README embeds.
- It needs **network access to huggingface.co** (and `*.hf.co`). The user-guide GIFs are not tracked in git
  (FLIP#1236): `conf.py` fetches the version pinned in `docs/.gifs_version` from the public dataset
  `aicentreflip/docs-gifs` into the gitignored `docs/source/assets/generated/gifs/`, verifying every file
  against the published manifest, and fails loudly if it cannot. A repeat build makes no network request.
  `FLIP_DOCS_SKIP_GIF_FETCH=1 make -C docs docs` builds text-only offline (the user-guide pages then warn
  about their missing images). See "Documentation GIFs" below.


## Documentation GIFs

The animated walkthroughs in the user guides (`docs/source/user-guides/user-common.rst` and
`docs/source/sys-admin/admin-project-and-user-management.rst`) are Cypress recordings of the UI against a
fully mocked backend. **They are not tracked in git** (FLIP#1236 — 65 MB that re-recording rewrote on every
run was 95% of a clone). Instead:

- `.github/workflows/regenerate_docs_gifs.yml` runs on every push to `develop` that touches `flip-ui/src/**`,
  the Cypress docs harness (`flip-ui/test/cypress/docs/**`, `cypress.docs.config.ts`,
  `scripts/videos-to-gifs.sh`) or the workflow itself, or by hand via *Run workflow*: its `record` job mints
  the version tag, records the demo specs under `flip-ui/test/cypress/docs/<category>/<name>.spec.ts` and
  converts each video to `docs/source/assets/generated/gifs/<category>/<name>.gif` with `ffmpeg`; its
  `publish` job — **`develop` only**; a dispatch from a branch records without publishing — publishes that
  directory to the public Hugging Face dataset
  [`aicentreflip/docs-gifs`](https://huggingface.co/datasets/aicentreflip/docs-gifs) as **one commit and one
  tag** `YYYYMMDDTHHMMSSZ-<sha7>` (`docs/scripts/publish_docs_gifs.py`), verifies the tag resolves
  anonymously, and opens a PR whose only diff is the pin, `docs/.gifs_version`. A later run opens a fresh PR
  and closes the superseded one.
- The docs build (`make -C docs docs`, the docs CI job, ReadTheDocs) fetches the pinned tag at build time
  (`docs/scripts/fetch_docs_gifs.py`, called from `docs/source/conf.py`) — see "Building the documentation".
- **Review the pin PR in its ReadTheDocs preview** (the `docs/readthedocs.org:londonaicentreflip` check):
  RTD builds the PR with the new pin, so the pages show the recordings in context. Re-recording is nondeterministic (frame
  timing plus the animated demo cursor), so every GIF is new bytes even where the UI didn't change — merge if
  the genuinely-changed clips look right, otherwise close.
- **Dataset tags are never moved or deleted**, so `stable` and every historical docs version keep resolving the
  tag they were built with. A re-recording is always a new tag.

### Adding a new GIF

Authoring is two-step, because a PR cannot ship the GIF itself:

1. Add the demo spec `flip-ui/test/cypress/docs/<category>/<name>.spec.ts` (the filename maps 1:1 to the GIF;
   reuse the functional suite's fixtures and `globalIntercepts`, and layer the cursor overlay from
   `flip-ui/test/cypress/docs/support/demoCursor.ts` so the recording reads as visible user actions) and the
   figure `.. figure:: ../assets/generated/gifs/<category>/<name>.gif` in the rst. `make -C docs test` checks
   the two agree. Until the GIF is published, the docs build warns "image file not readable" for that one
   figure — expected, and deliberately not fatal.
2. Once the PR merges to `develop`, the workflow records and publishes everything and opens the pin PR;
   merging that makes the new figure render.

### Previewing locally

```bash
cd flip-ui
npm run docs:record   # records videos under test/cypress/videos/
npm run docs:gifs     # ffmpeg → docs/source/assets/generated/gifs/<category>/*.gif (requires ffmpeg on PATH)
FLIP_DOCS_SKIP_GIF_FETCH=1 make -C ../docs docs   # build on YOUR recordings, not the pinned ones
```

Without the skip flag the build restores the pinned bytes over any file that differs — the pin wins.

### Publishing by hand

Rarely needed (the workflow does it), but the same script works from a laptop with an account that can
write to the dataset:

```bash
uvx --from 'huggingface_hub>=1.6' hf auth login
uv run docs/scripts/publish_docs_gifs.py --source-commit "$(git rev-parse origin/develop)" --dry-run
uv run docs/scripts/publish_docs_gifs.py --source-commit "$(git rev-parse origin/develop)"   # then pin the printed tag
```

`--gifs-dir` points at a different tree; `FLIP_DOCS_GIFS_REPO` / `FLIP_DOCS_GIFS_REVISION` (e.g. `main`) let a
build read another dataset or an untagged revision, mirroring `HF_TRUST_DATA_REVISION`.
