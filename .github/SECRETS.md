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

This document describes the GitHub secrets required for the CI/CD pipeline.

## Overview

The CI workflows use a hybrid approach for environment configuration:

1. **Base configuration**: Copied from `.env.development.example` (checked into version control)
2. **Sensitive overrides**: Set via GitHub repository secrets (not in version control)

This approach minimizes the number of secrets to manage while keeping sensitive data secure.

## Required GitHub Secrets

Configure these secrets in your GitHub repository settings (Settings → Secrets and variables → Actions):

### 1. `AES_KEY_BASE64`

**Description**: Base64-encoded 32-byte AES-256 encryption key used by trust services (imaging-api, data-access-api, trust-api).

**How to generate**:

```bash
# Generate a random 32-byte key and encode it in base64
openssl rand -base64 32
```

**Example value**: `dGVzdC1hZXMta2V5LWZvci1jaS10ZXN0aW5nLTMyYnl0ZXM=`

**Used by**:

- `imaging_api.yml`
- `data_access_api.yml`
- `trust_api.yml`
- `central_hub_api.yml`

---

### 2. `TRUST_API_KEY`

**Description**: Per-trust API key for authenticating trust-to-hub service calls. Each trust gets a unique key; the hub stores SHA-256 hashes in `TRUST_API_KEY_HASHES` and validates incoming keys with constant-time comparison.

**How to generate**:

```bash
make generate-trust-api-keys
```

**Example value**: `test-trust-api-key-for-ci`

**Used by** (trust-side CI only):

- `trust_api.yml`
- `imaging_api.yml`
- `data_access_api.yml`

> **Note**: The central hub (`flip-api`) validates per-trust keys via `TRUST_API_KEY_HASHES`. Plaintext keys are stored in `TRUST_API_KEYS` JSON dict in the env file.

---

### 3. Database Passwords (Optional)

These are set to static values in CI but could be made into secrets if needed:

- `POSTGRES_PASSWORD`: PostgreSQL password for central hub database (currently hardcoded to `test_password` in CI)
- `OMOP_POSTGRES_PASSWORD`: OMOP database password (currently hardcoded to `test_password` in CI)
- `DATA_ACCESS_POSTGRES_PASSWORD`: Data access user password (currently hardcoded to `test_password` in CI)

## Fallback Values

All secrets have fallback values that will be used if the secret is not configured:

- `AES_KEY_BASE64`: Falls back to `dGVzdC1hZXMta2V5LWZvci1jaS10ZXN0aW5nLTMyYnl0ZXM=`
- `TRUST_API_KEY`: Falls back to `test-trust-api-key-for-ci` (trust-side CI workflows only)

This ensures CI doesn't break if secrets are missing, but these fallback values should **never** be used in production.

## How CI Workflows Use Secrets

Each CI workflow follows this pattern:

```yaml
- name: Setup environment file
  run: |
    cp .env.development.example .env.development
    # Override sensitive values with GitHub secrets
    echo "AES_KEY_BASE64=${{ secrets.AES_KEY_BASE64 }}" >> .env.development
    # TRUST_API_KEY is only needed in trust-side workflows (trust-api, imaging-api, data-access-api)
    echo "TRUST_API_KEY=${{ secrets.TRUST_API_KEY }}" >> .env.development
    echo "DATA_ACCESS_POSTGRES_PASSWORD=${{ secrets.POSTGRES_PASSWORD }}" >> ../../.env.development
    echo "OMOP_POSTGRES_PASSWORD=${{ secrets.POSTGRES_PASSWORD }}" >> ../../.env.development
    echo "POSTGRES_PASSWORD=${{ secrets.POSTGRES_PASSWORD }}" >> ../../.env.development
    echo "SES_VERIFIED_EMAIL=${{ secrets.SES_VERIFIED_EMAIL }}" >> .env.development
    echo "AWS_SES_ADMIN_EMAIL_ADDRESS=${{ secrets.SES_VERIFIED_EMAIL }}" >> ../../.env.development
    echo "AWS_SES_SENDER_EMAIL_ADDRESS=${{ secrets.SES_VERIFIED_EMAIL }}"
```

This approach:

1. Copies the example file (contains safe default values)
2. Appends secret values to override placeholders
3. Makes the complete `.env.development` file available to tests

## Local Development

For local development, developers should:

1. Copy `.env.development.example` to `.env.development`:

   ```bash
   cp .env.development.example .env.development
   ```

2. Update the placeholder values with their own credentials

3. **Never commit `.env.development`** (it's in `.gitignore`)

## Adding New Secrets

When adding a new secret requirement:

1. Add the placeholder value to `.env.development.example`
2. Add the secret to GitHub repository settings
3. Update the relevant CI workflow(s) to override the value
4. Document the secret in this file

## Security Notes

- ✅ `.env.development` is in `.gitignore` and should never be committed
- ✅ All sensitive values should come from GitHub secrets, not hardcoded in workflows
- ✅ The `.env.development.example` file should only contain placeholder or localhost values
- ⚠️ Fallback values in workflows are for CI convenience only - never use them in production
- ⚠️ Rotate secrets periodically following your organization's security policies
