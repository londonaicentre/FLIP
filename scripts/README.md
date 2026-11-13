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

Repo-root utility scripts: local environment and status checks, cross-service drift guards,
release tooling (`release/`) and secret scanning. Most run through a `make` target, a CI workflow
or a pre-commit hook; each section below names it.

The trust kit lifecycle scripts (`new_trust.py`, `trust_kit_lib.py`, `distribute_trust_kits.py`,
`sync_trust_kit.py`, `onboard_onprem_trust.py`) and `site_upgrade.py`, with their tests, live in
[`trust/deploy/scripts/`](../trust/deploy/scripts/README.md) (moved in FLIP#1438).

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

## Drift guards

None of these is a pre-commit hook. The first two are `make`-time guards that run in no CI
workflow; the other three are CI-only:

- **`check-fl-provisioned.sh`** (the `make up` / `make up-no-trust` guard, via the root
  Makefile's `_check-fl-provisioned`) — fails fast when a backend's per-net FL credentials are
  missing, instead of letting the FL containers crash-loop. Neither backend's credentials are
  created by `make up` — they come from `make -C fl-services/<backend> provision...`.
- **`check-uv-version.sh`** (the `make lock` guard) — fails fast when `uv` is too old to
  understand the dependency cooldown (`exclude-newer = "3 days"`, which needs uv >= 0.10.0). An
  older uv silently discards the whole `[tool.uv]` table and resolves with no cooldown at all,
  rewriting `uv.lock` with no warning worth noticing.
- **`check_fl_api_validation_sync.sh`** (CI: `fl-api-validation-sync.yml`) — verifies that the
  code the FLARE and Flower fl-api services share stays in sync: `safe_join` and
  `validate_bundle_url` (the SSRF and path-traversal guards in front of the server-side bundle
  fetch) and the other shared bundle-URL helpers in `fl_api/utils/validation.py` stay
  byte-identical, and `refusals.py`, `auth.py` and `job_credential.py` under `fl_api/utils/` match
  byte for byte. They're intentionally separate copies (different Docker build contexts, uv projects, and
  images), so a fix applied to one and not the other would silently leave the second vulnerable.
- **`check_tutorial_sync.sh`** (CI: `fl-apps-check-tutorial-sync.yml`) — verifies that tutorial
  files kept as byte-identical copies of another file have not drifted. Its pairs are hand-listed
  and tutorial-to-tutorial: the Ark+ FLARE pair (`data_utils.py` and `arkplus_flat_models.py`,
  shared between the two evaluation apps), and the Flower copies of tutorials that exist on both
  backends, with the FLARE copy as the reference: the EHR risk-prediction and biomarker
  causal-discovery app files, and every twin pair's `query.sql` / `query_novocab.sql`.
  The copies can't be symlinks, because `flwr build` leaves symlinks out of the FAB (the Flower
  app bundle). Each is a real file that must be resynced by hand when its reference changes.
  Flower tutorial vs `fl-apps/flower/` template parity is not checked here:
  `fl-tutorials/tests/test_flower_platform_parity.py` derives those pairs from the tree.
- **`pr_paths_changed.py`** (CI: `pr_paths_changed.yml`, called by every gated service-test
  workflow) — implements the PR path gate: reads a service workflow's `paths:` filter, fetches the
  PR's changed files, and outputs `run=true` when the filter matches so the workflow's jobs
  gate on it. A PR into `main` always runs everything. `scripts/tests/test_pr_paths_changed.py`
  pins the gate against drift.

## fl_round_metrics/

Tooling for extracting and comparing per-round FL timings (not model metrics) from either a live
platform run (CloudWatch logs) or a local FLARE simulator workspace. See
[`fl_round_metrics/README.md`](fl_round_metrics/README.md) for the tools and the
`make round-metrics` / `make reproduce-overhead` wrapper in the arkplus fine-tuning tutorial.

## tests/

Tests for the scripts in this directory are in `scripts/tests/test_*.py`. They use only the
standard library, not pytest, and each runs as a plain script (`if __name__ == "__main__": main()`).
The only external tool needed is `uv`, because
`trust/deploy/scripts/tests/test_sync_trust_kit.py` runs `sync_trust_kit.py` through
`uv run --no-config`.

Run the tests here and in `trust/deploy/scripts/tests/` together:

```bash
for test_file in scripts/tests/test_*.py trust/deploy/scripts/tests/test_*.py; do
  echo "== $test_file"
  python3 "$test_file" || exit 1
done
```

CI runs the same loop in [`.github/workflows/test_trust_kit_scripts.yml`](../.github/workflows/test_trust_kit_scripts.yml)
on every pull request into `develop`. On push to `develop` it triggers on `scripts/**`,
`trust/deploy/scripts/**` and the files outside them that a guard checks, such as the hub and trust
compose files `test_container_identity.py` asserts against, so a compose-only edit still runs it.

## Secret scanning

This directory also contains scripts for detecting secrets and sensitive information in the FLIP
monorepo.

`utils.sh` holds shared shell helpers (colour-coded `log_info`, `log_success`, etc.). Only the two
secret-scanning scripts (`scan-secrets.sh`, `setup-secret-scanning.sh`) source it. It is not run directly.

### Quick Start

#### Prerequisites

This monorepo uses `uv` for Python package management. If you haven't installed it yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### Initial Setup

Run the setup script once per clone. It installs the scanning tools and the pre-commit hooks:

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

Reports are written to `.security-reports/` (gitignored). They may contain the secrets found, so review them before
sharing or attaching them to an issue.

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

- **check-env-vars**: runs `check_env_vars.py`; see [Status and environment checks](#status-and-environment-checks).
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

#### Running the tools directly

```bash
trufflehog git file://. --only-verified --json   # TruffleHog, from the repo root
detect-secrets scan --all-files                 # detect-secrets
```

### Emergency: remove secrets from Git history

If secrets were accidentally committed, follow the [GitHub guide on removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository) using tools such as `git filter-repo` or BFG Repo-Cleaner.

⚠️ **WARNING**: This rewrites git history. Only use when absolutely necessary. Coordinate with all team members before force-pushing.

### CI/CD Integration

Secret scanning runs automatically on:

- Push to `main` or `develop` branches
- Pull requests
- Weekly schedule (Sundays at 2 AM UTC)

See [.github/workflows/secret-scanning.yml](../.github/workflows/secret-scanning.yml) for details.

### Managing False Positives

#### detect-secrets Baseline

To update the baseline with new false positives:

```bash
detect-secrets scan --baseline .secrets.baseline --update
```

To audit the baseline:

```bash
detect-secrets audit .secrets.baseline
```

### Troubleshooting

#### uv, pre-commit or detect-secrets not found

Install `uv` as in [Prerequisites](#prerequisites). Then make sure `~/.local/bin`, where uv puts itself and the
tools it installs, is on your PATH:

```bash
# Add to your ~/.bashrc, ~/.zshrc, or ~/.config/fish/config.fish
export PATH="$HOME/.local/bin:$PATH"
```

If `pre-commit` or `detect-secrets` is still missing after fixing PATH, install them via Homebrew:

```bash
brew install pre-commit
pip install detect-secrets  # Only if using pipx or virtual environment
```

#### TruffleHog Not Found

Install via Homebrew:

```bash
brew install trufflesecurity/trufflehog/trufflehog
```

Or use Docker:

```bash
docker pull trufflesecurity/trufflehog:latest
```

#### Pre-commit Hook Failures

If pre-commit hooks fail due to missing tools:

1. Ensure tools are installed: `which trufflehog`
2. Reinstall pre-commit hooks: `pre-commit install`
3. Update pre-commit: `pre-commit autoupdate`

### Best Practices

1. **Run scans before committing**: Use `pre-commit run` to catch secrets early
2. **Review scan results carefully**: Not all findings are real secrets
3. **Never commit real secrets**: Use environment variables or secret management
4. **Update baselines regularly**: Keep false positive baselines current
5. **Scan the entire history**: Use full scans periodically to find historical secrets

### Support

For issues or questions about secret scanning:

- Check tool documentation: [TruffleHog](https://github.com/trufflesecurity/trufflehog), [detect-secrets](https://github.com/Yelp/detect-secrets)
- Review [CONTRIBUTING.md](../CONTRIBUTING.md) for security guidelines
