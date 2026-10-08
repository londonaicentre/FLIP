<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

# GitHub Secrets Configuration for CI

This document describes which secrets the CI/CD pipeline reads, and which values it deliberately does not.

## Test workflows: generated credentials, not secrets

The test workflows bring up throwaway stacks (Testcontainers Postgres, the `trust/deploy/compose.test.yml` stack,
the local hub smoke). Their credentials only have to be consistent within one run, so they are **generated per run**
rather than read from secrets:

| Value | Generated with |
|---|---|
| `AES_KEY_BASE64` | `openssl rand -base64 32` (32 bytes, as every `get_aes_key()` requires) |
| `POSTGRES_PASSWORD` (and the trust-side `DATA_ACCESS_` / `OMOP_POSTGRES_PASSWORD`) | `openssl rand -hex 16` |
| `TRUST_API_KEY` | `openssl rand -hex 16` |

- **Where:** `test_flip_api.yml`, `local_auth_smoke.yml`, and `.github/actions/setup-trust-test-env` (shared by the
  trust-api and data-access-api jobs).
- **Pinned values:** the trust integration jobs pass the AES key and database password that `compose.test.yml` pins
  for its stack, because the containerised data-access-api and omop-db must agree with the test process.
- **Why not secrets:** a pull request from a fork is given no secrets, so any test that needs one either fails (the
  local hub smoke did) or silently runs in a different configuration from a same-repo PR. Generated values make every
  run alike, and leave nothing for a test job to leak.

Do not reintroduce `secrets.*` for a value a test stack only needs to be self-consistent.

## Secrets CI does read

| Secret | Where it lives | Used by |
|---|---|---|
| `CODECOV_TOKEN` | `flip` environment | coverage upload in the test workflows |
| `HF_TOKEN` | `flip` environment | `regenerate_docs_gifs.yml` (publishes the docs GIFs, gated to `develop`) |
| Terraform inputs (`AES_KEY_BASE64`, `ADMIN_USER_PASSWORD`, `INTERNAL_SERVICE_KEY`, …) | `aws-stag` / `aws-prod` environments | `terraform_plan/apply/drift.yml` (see below) |
| `GITHUB_TOKEN` | provided by Actions | image publishing, releases, PR automation |

The Terraform `AES_KEY_BASE64` is the platform's real hub↔trust key. It is unrelated to the throwaway test keys above
and must never be copied into a test workflow.

## Local Development

For local development, developers should:

1. Copy `.env.development.example` to `.env.development`:

   ```bash
   cp .env.development.example .env.development
   ```

2. Update the placeholder values with their own credentials (`openssl rand -base64 32` for `AES_KEY_BASE64`)

3. **Never commit `.env.development`** (it's in `.gitignore`)

## Adding a New Secret

Prefer generating the value in the workflow when only a throwaway stack consumes it (see above). When CI genuinely
needs a credential:

1. Add the placeholder value to `.env.development.example`
2. Add the secret to the GitHub environment the job uses (not a repository secret)
3. Reference it only from the job that needs it
4. Document it in this file

## Terraform environment secrets (not repository secrets)

The AWS deployment pipeline does **not** use repository secrets. It uses two
GitHub *environments* — `aws-stag` and `aws-prod` — so the values are scoped to the
environment and readable only on the branches its deployment branch policy admits
(`aws-prod` admits `main` alone; that restriction is security, not tidiness).

Which account and which mode an environment drives is set by its **`TF_PROD`**
variable — the `deploy/env_mode.mk` token, one of `stag` | `true` | `lza-stag` |
`lza` — together with `TF_PLAN_ROLE_ARN` / `TF_APPLY_ROLE_ARN`, the OIDC roles it
assumes. Those roles are not created by FLIP's pipeline: they come from the
`deploy/providers/AWS/modules/terraform_ci_bootstrap` module, which the platform
repositories (`aicentre-iac`, `aicentre-lza-iac`) apply in each account — or, in an
account you own, `deploy/providers/AWS/ci`. The seeding script reads both ARNs from
IAM, after checking the roles trust this repository's environment. Repointing an environment at a different AWS account is a change to those
values, not to any workflow. Because that variable alone decides the estate's
shape, the workflows also state the **class** their branch implies
(`EXPECTED_ENV_CLASS`: `main` → `prod`, otherwise `stag`; `stag` in
`terraform_plan.yml`) and `compose-ci-env.sh` refuses to compose when `TF_PROD`
names the other class — so a mis-set variable is a red compose step rather than an
unattended apply that plans production with staging's shape. The full key list, the
seeding procedure and the per-account bootstrap order are documented with the stack
that consumes them: `deploy/providers/AWS/README.md` → "Terraform CI: plan on PR,
apply on merge" and "Repointing CI at the LZA accounts". Seeding is done by
`deploy/providers/AWS/scripts/setup-github-environments.sh --mode <token>` (repo
admin, `--dry-run` first, from a machine holding the operator's `.env.<env>` file).

## Security Notes

- ✅ `.env.development` is in `.gitignore` and should never be committed
- ✅ Real credentials come from GitHub environment secrets, never hardcoded in workflows
- ✅ Test-stack credentials are generated per run, never read from secrets
- ✅ The `.env.development.example` file should only contain placeholder or localhost values
- ⚠️ Rotate secrets periodically following your organization's security policies
