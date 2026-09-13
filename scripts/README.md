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

# scripts/

Repo-root utility scripts: trust kit lifecycle (scaffold, register, sync, distribute, onboard),
local environment/status checks, cross-service drift guards run by pre-commit and CI, and secret
scanning. Most are invoked through `make` targets rather than run directly — see each section
below for the wrapping target.

## Trust kit scripts

These scaffold, write, and keep in sync the `trust/.env.<CODE>.<env>` kit files that carry a
trust's identity, credentials, and Hub-shared configuration. `trust_kit_lib.py` is the shared
in-place-upsert implementation behind the others, so a kit file's operator edits (host-local
ports, bind dirs) always survive a re-run.

- **`new_trust.py`** (`make new-trust TRUST_CODE=<CODE> TRUST_NAME="..." ENV=<env>`) — scaffolds
  `trust/.env.<CODE>.<env>` from the base template (`trust/.env.example`), prepending the trust's
  identity (`TRUST_NAME` / `TRUST_CODE` / `TRUST_REGION`). Refuses to overwrite an existing kit.
  The result is ready for `make register-trust KIT=<CODE>` to fill in credentials.
- **`trust_kit_lib.py`** — the single kit-file writer behind `distribute_trust_kits.py`,
  `sync_trust_kit.py`, and the AWS registration path. Merges a kit dict into a kit file while
  preserving operator edits: credentials (`TRUST_API_KEY` / `TRUST_INTERNAL_SERVICE_KEY`) are
  written only on a new registration and never clobbered on an idempotent re-run; metadata
  (`EXPECTED_TRUST_ID` / `FL_KIT_SLOT` / `FL_KIT_SLOT_NUMBER`) and the Hub-shared block are
  upserted unconditionally, the latter under a sentinel header added once on first write. Writes
  every kit file `0600`.
- **`distribute_trust_kits.py`** — writes trust kit files from `register_trust`'s JSON output.
  Array mode (default) reads a JSON array of kits on stdin and writes each to
  `trust/.env.<fl_kit_slot>`, seeding a host-local profile from the matching `.example` on first
  write; used by the dev `register-trust` Makefile pipe. `--target PATH` mode writes a single kit
  (object or one-element array) to an explicit path; used by
  `deploy/providers/AWS/scripts/register-trusts.sh` for stag/prod.
- **`sync_trust_kit.py`** (`make sync-trust-kit KIT=<CODE> PROD=<env>`) — refreshes only the
  Hub-shared block in `trust/.env.<KIT>` from the caller's environment (the root Makefile
  `include`s the right `.env.<env>` and exports it first). Credentials and operator edits are left
  untouched. Portable across dev/stag/prod — no docker compose exec, no ECS round-trip.
- **`onboard_onprem_trust.py`** (`make onboard-onprem-trust KIT=<slot>`) — a readiness checklist
  for an on-prem trust host, run as a precheck by `up-onprem-trust`. Diagnoses what's missing
  before `make up-onprem-trust` will succeed: public IP, docker swarm state, kit file presence,
  Hub-shared block, kit credentials, `FL_KIT_DIR` contents, OMOP/Orthanc data dirs, and GPU
  capacity (`NUM_AVAILABLE_GPUS` vs. what the host actually exposes). Each check renders ✅ / ❌ /
  ⏳ (pending on an earlier check) and exits non-zero if anything fails, so an operator gets
  concrete diagnostics instead of a cryptic compose or pydantic failure deeper in the stack.

## Status and environment checks

- **`check_local_status.py`** (run directly: `python3 scripts/check_local_status.py`) — verifies
  the local Docker Compose stack is functioning: Docker daemon, containers, networks, application
  endpoints (UI, API, FL API), trust endpoints (trust-api, imaging-api, data-access-api), database
  connectivity, and system resources. Exits 0 when all checks pass (warnings are acceptable), 1
  otherwise. Options include `--skip-endpoints`, `--skip-docker`, `--project-dir`, and `--env-file`.
  Resolves container and network names through compose project labels (honouring `FLIP_INSTANCE` /
  `MAIN_ENV_FILE`) so it reports correctly against a prefixed second stack, not just the default
  one.
- **`check_env_vars.py`** (pre-commit hook `check-env-vars`) — verifies every variable declared
  in `.env.development.example` is also present in `.env.development`, so the example file stays
  up to date and a new required variable can't be missed silently.

## Drift guards (pre-commit + CI backstop)

Each of these is a pre-commit hook with a matching CI job as the backstop for a commit made
without pre-commit installed:

- **`check-fl-provisioned.sh`** — fails fast when a backend's per-net FL credentials are missing,
  instead of letting the FL containers crash-loop. Neither backend's credentials are created by
  `make up` — they come from `make -C fl-services/<backend> provision...`.
- **`check-uv-version.sh`** — fails fast when `uv` is too old to understand the dependency
  cooldown (`exclude-newer = "3 days"`, which needs uv >= 0.10.0). An older uv silently discards
  the whole `[tool.uv]` table and resolves with no cooldown at all, rewriting `uv.lock` with no
  warning worth noticing.
- **`check_fl_api_validation_sync.sh`** — verifies that `safe_join` and `validate_bundle_url`
  (the SSRF and path-traversal guards in front of the server-side bundle fetch) stay
  byte-identical between the NVFLARE and Flower fl-api services. They're intentionally separate
  copies (different Docker build contexts, uv projects, and images), so a fix applied to one and
  not the other would silently leave the second vulnerable.
- **`check_tutorial_sync.sh`** — verifies that tutorial files kept as byte-identical copies of
  another file (Flower tutorial files copied from `fl-apps/flower/` templates; Ark+ NVFLARE files
  shared between the two evaluation apps) have not drifted. These can't be symlinks — `flwr build`
  excludes symlinks from the FAB — so each keeps a real copy that must be resynced by hand when
  its reference changes.
- **`utils.sh`** — shared shell helpers (colour-coded `log_info` / `log_success` / etc.) sourced
  by the shell scripts above; not run directly.

## fl_round_metrics/

Tooling for extracting and comparing per-round FL timings (not model metrics) from either a live
platform run (CloudWatch logs) or a local NVFLARE simulator workspace. See
[`fl_round_metrics/README.md`](fl_round_metrics/README.md) for the tools and the
`make round-metrics` / `make reproduce-overhead` wrapper in the arkplus fine-tuning tutorial.

## tests/

Stdlib-only tests for the scripts in this directory (`scripts/tests/test_*.py`), each following
the same `if __name__ == "__main__": main()` contract — no pytest, no external services beyond
`uv` itself (needed because `test_sync_trust_kit.py` drives `sync_trust_kit.py` through
`uv run --no-config`). Run the whole directory:

```bash
for test_file in scripts/tests/test_*.py; do
  echo "== $test_file"
  python3 "$test_file" || exit 1
done
```

CI runs the same loop in [`.github/workflows/test_trust_kit_scripts.yml`](../.github/workflows/test_trust_kit_scripts.yml),
triggered on changes under `scripts/**` and on the hub/trust compose files that
`test_container_identity.py` asserts against (so an edit to those triggers the guard even though
it touches nothing under `scripts/`).

## Secret scanning

This directory also contains scripts for detecting secrets and sensitive information in the FLIP
monorepo.

### Quick Start

#### Prerequisites

This monorepo uses `uv` for Python package management. If you haven't installed it yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### Initial Setup

Run the setup script once to install and configure all secret scanning tools:

```bash
./scripts/setup-secret-scanning.sh
```

This will:

- Install `pre-commit`, `detect-secrets`, and `trufflehog`
- Set up pre-commit hooks
- Create a baseline for detect-secrets
- Update .gitignore to exclude security reports

### Running Scans

#### Full Monorepo Scan

To scan the entire monorepo for secrets:

```bash
./scripts/scan-secrets.sh
```

#### Pre-commit Checks

Check staged files before committing:

```bash
pre-commit run
```

Check all files:

```bash
pre-commit run --all-files
```

**Available Pre-commit Hooks** (see [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) for
the authoritative list):

- **check-env-vars**: Verifies that all variables in `.env.development.example` exist in `.env.development`. This ensures the example file stays up to date and developers are aware of new required environment variables.
- **fl-apps-required-files**: Regenerates each backend's `fl-apps/<backend>/required_files.json` from its per-template `required_files.json` arrays. Rewrites-and-fails on drift (like `prettier`), so re-stage and commit again.
- **xnat-dcm2niix-pin-sync**: Checks that the dcm2niix Container Service image pin (`ARG DCM2NIIX_VERSION` in its Dockerfile) agrees across the three deploy configs that reference it as a literal tag string. Report-only — the sites are hand-written, not generated.
- **trufflehog**: Scans for high-entropy strings and verified secrets
- **detect-secrets**: Pattern-based secret detection with baseline support
- **check-added-large-files**: Prevents committing files larger than 1000 KiB (`--maxkb=1000` — the hook compares against `getsize // 1024`, so the cap is ~1.024 MB)
- **check-merge-conflict**: Detects merge conflict markers
- **check-yaml**: Validates YAML syntax
- **prettier**: Formats YAML under `deploy/providers/AWS/`
- **end-of-file-fixer**: Ensures files end with a newline
- **detect-private-key**: Detects private SSH/SSL keys
- **uv-lock** (one entry per uv project — root, flip-api, docs, trust-api, imaging-api, data-access-api, omop-db, xnat-tests, aws-deploy, flip-utils, fl-api-flower, fl-api-base): verifies each project's `uv.lock` is in sync with its `pyproject.toml` (`--check`, so it fails on drift instead of silently regenerating). Use `make lock` to regenerate when a `pyproject.toml` legitimately changes.

#### Component-Specific Scans

To scan specific components (flip-api, trust, etc.):

```bash
cd flip-api
trufflehog git file://. --only-verified
```

## Emergency: Remove Secrets from Git History

If secrets were accidentally committed, follow the [GitHub guide on removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository) using tools such as `git filter-repo` or BFG Repo-Cleaner.

⚠️ **WARNING**: This rewrites git history. Only use when absolutely necessary. Coordinate with all team members before force-pushing.

## Tools Used

### TruffleHog

High-entropy string and secret scanner with built-in detector patterns.

**Direct usage:**

```bash
trufflehog git file://. --only-verified --json
```

### detect-secrets

Pattern-based scanner with baseline support.

**Direct usage:**

```bash
detect-secrets scan --all-files
```

## CI/CD Integration

Secret scanning runs automatically on:

- Push to `main` or `develop` branches
- Pull requests
- Weekly schedule (Sundays at 2 AM UTC)

See [.github/workflows/secret-scanning.yml](../.github/workflows/secret-scanning.yml) for details.

## Managing False Positives

### detect-secrets Baseline

To update the baseline with new false positives:

```bash
detect-secrets scan --baseline .secrets.baseline --update
```

To audit the baseline:

```bash
detect-secrets audit .secrets.baseline
```

## Troubleshooting

### uv Not Found

This project uses `uv` for Python package management. Install it with:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

After installation, ensure `~/.local/bin` is in your PATH:

```bash
# Add to your ~/.bashrc, ~/.zshrc, or ~/.config/fish/config.fish
export PATH="$HOME/.local/bin:$PATH"
```

### Commands Not Found After Installation

If `pre-commit` or `detect-secrets` aren't found after running the setup script, the uv tool directory may not be in your PATH. Add it:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Or reinstall the tools globally via Homebrew:

```bash
brew install pre-commit
pip install detect-secrets  # Only if using pipx or virtual environment
```

### TruffleHog Not Found

Install via Homebrew:

```bash
brew install trufflesecurity/trufflehog/trufflehog
```

Or use Docker:

```bash
docker pull trufflesecurity/trufflehog:latest
```

### Pre-commit Hook Failures

If pre-commit hooks fail due to missing tools:

1. Ensure tools are installed: `which trufflehog`
2. Reinstall pre-commit hooks: `pre-commit install`
3. Update pre-commit: `pre-commit autoupdate`

## Best Practices

1. **Run scans before committing**: Use `pre-commit run` to catch secrets early
2. **Review scan results carefully**: Not all findings are real secrets
3. **Never commit real secrets**: Use environment variables or secret management
4. **Update baselines regularly**: Keep false positive baselines current
5. **Scan the entire history**: Use full scans periodically to find historical secrets

## Security Reports

Scan reports are saved to `.security-reports/` (gitignored). Review these carefully before sharing or opening issues.

## Support

For issues or questions about secret scanning:

- Check tool documentation: [TruffleHog](https://github.com/trufflesecurity/trufflehog), [detect-secrets](https://github.com/Yelp/detect-secrets)
- Review [CONTRIBUTING.md](../CONTRIBUTING.md) for security guidelines
