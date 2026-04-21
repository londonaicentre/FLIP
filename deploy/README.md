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

# Pre-configurations needed for FLIP Deployment

## Supported PostgreSQL Versions

FLIP uses AWS RDS PostgreSQL with the following version support policy:

- **Current Version**: PostgreSQL 17 (EOL: November 2029) ✓
- **Minimum Version**: PostgreSQL 15
- **Deprecated**: PostgreSQL 13 (EOL: November 2025) ❌ EXPIRED

**Version Lifecycle:**

| Version | EOL | Status |
| ------- | --- | ------ |
| PostgreSQL 13 | November 2025 | ❌ EXPIRED — do not use |
| PostgreSQL 14 | October 2026 | ❌ EXPIRED — do not use |
| PostgreSQL 15 | October 2027 | ⚠️ Deprecating soon |
| PostgreSQL 16 | October 2028 | ✓ Supported |
| PostgreSQL 17 | November 2029 | ✓ Current (Terraform default) |

**Upgrade Policy**: Plan PostgreSQL major version upgrades with a 6-month lead time before EOL. AWS charges premium rates for EOL versions. To change the version, update the `postgres_version` variable in `deploy/providers/AWS/variables.tf`.

## Deployment Architecture

### Prerequisites

#### Step 1: Authenticate with GitHub Container Registry

Log in to GHCR to pull pre-built images from CI/CD:

Create a token with `read:packages` scope from GitHub settings.
We recommend following [GitHub's guide](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-with-a-personal-access-token-classic).

```bash
echo <GITHUB_PAT> | docker login ghcr.io -u <GITHUB_USERNAME> --password-stdin
```

> **Note**: You need a GitHub Personal Access Token (PAT) with `read:packages` permission.

#### Step 2: Configure AWS CLI SSO

Set up AWS CLI with SSO for authentication:

```bash
aws configure sso
```

if you have already configured SSO, you can then login with:

```bash
aws sso login
```

#### Step 3: Get SSH key configured

Generate an SSH key pair for EC2 instance access:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/host-aws -C "YOUR_EMAIL@example.com"
```

This key will automatically be uploaded to AWS during deployment and can be found in the AWS console under AWS EC2 > Network & Security > Key Pairs.

See `TF_VAR_flip_keypair` and `TF_VAR_ec2_public_key_path` in the Terraform environment configuration if you need to customize the key name or path.

### Final configuration

#### Verify AWS SES email address

The SES email address will have received a verification link you need to click. Then, to check the email has been verified, log in to the AWS console, navigate to the SES service, and check the Configuration > Identities section.

#### Cognito Email Configuration

FLIP uses AWS Cognito for user authentication and includes branded email templates for temporary password invitations and password reset flows. The email templates are deployed as part of the Terraform infrastructure.

**Email Templates:**

1. **Temporary Password Email** (`admin_create_user_config.invite_message_template`)
   - Sent when administrators invite users to FLIP
   - Contains: Username and temporary password
   - Uses Cognito placeholders:
     - `{username}` — User's Cognito username
     - `{####}` — 6-character temporary password

2. **Password Reset Email** (`verification_message_template`)
   - Sent when users request password reset
   - Includes both verification code and reset link options
   - Uses Cognito placeholders:
     - `{####}` — 6-character verification code
     - `{##...##}` — Dynamically generated password reset link token

**SES Email Verification Requirement:**

Before deploying Cognito email templates, the SES email address must be verified:

```bash
cd deploy/providers/AWS

# For initial deployment or if verification has expired:
# 1. Delete the existing SES identity in AWS Console (if expired):
#    - Navigate to AWS SES > Configuration > Identities
#    - Delete the FLIP email identity
# 2. Re-verify the email address
#    - Run: make plan apply
#    - Check your email inbox for AWS SES verification link
#    - Click the link to confirm the email address
# 3. Verify the configuration:
#    - Return to AWS SES > Identities
#    - Confirm the email status shows "Verified"
```

**Testing Email Delivery:**

After deployment, test the email configuration by creating a test user:

```bash
# Get the Cognito user pool ID
USER_POOL_ID=$(cd deploy/providers/AWS && terraform output -raw cognito_user_pool_id)

# Create test user (suppress automatic email)
aws cognito-idp admin-create-user \
  --user-pool-id "$USER_POOL_ID" \
  --username testuser@example.com \
  --message-action SUPPRESS

# Verify the temporary password email is received with correct formatting
# (Check inbox for email from FLIP with temporary credentials)

# Test password reset flow
# 1. Login as testuser with temporary password
# 2. Change password to permanent one
# 3. Logout and request password reset
# 4. Verify password reset email arrives with verification code or reset link

# Verify email rendering across clients:
# - Gmail
# - Outlook
# - Apple Mail
# - Mobile email clients
```

**Manual Verification Checklist:**

- [ ] SES email identity shows "Verified" status in AWS Console
- [ ] Temporary password email includes username and temporary password
- [ ] Password reset email includes 6-digit verification code or reset link
- [ ] Email templates render correctly in Gmail, Outlook, Apple Mail
- [ ] Links in email templates resolve to correct environment subdomain (e.g., `https://flip-staging.example.com`)
- [ ] SMS fallback messages deliver (if SMS is enabled in Cognito)

## Service Authentication

FLIP uses two separate authentication mechanisms for service-to-hub communication:

### Trust API Keys (trust-api → flip-api)

Each trust has a unique API key stored in the `TRUST_API_KEYS` JSON dict and sent in the `TRUST_API_KEY_HEADER` header.
The hub stores only SHA-256 hashes of these keys in `TRUST_API_KEY_HASHES`. Used for task polling, cohort result
submission, and heartbeat endpoints.

Generate keys with `make generate-trust-api-keys` and `make generate-internal-service-key`.

### Internal Service Key (fl-server → flip-api)

The fl-server on the Central Hub authenticates to flip-api using `INTERNAL_SERVICE_KEY` sent in the
`INTERNAL_SERVICE_KEY_HEADER` header. The hub validates it against
`INTERNAL_SERVICE_KEY_HASH`. Used for model status updates, training metrics, and training log endpoints.

FL clients (trust side) **do not** have Central Hub API credentials. Only the fl-server communicates with flip-api.
FL clients relay metrics and exceptions to the fl-server, which forwards them to the Central Hub.

| Variable | Where used | Purpose |
|---|---|---|
| `TRUST_API_KEY_HEADER` | flip-api, trust-api | Header name for trust auth |
| `TRUST_API_KEYS` | trust-api | JSON dict of trust name → plaintext key |
| `TRUST_API_KEY_HASHES` | flip-api | JSON dict of trust name → SHA-256 hash |
| `INTERNAL_SERVICE_KEY_HEADER` | flip-api, fl-server | Header name for internal service auth |
| `INTERNAL_SERVICE_KEY` | fl-server | Internal service plaintext key |
| `INTERNAL_SERVICE_KEY_HASH` | flip-api | SHA-256 hash of internal service key |
