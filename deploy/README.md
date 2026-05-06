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

#### Cognito MFA Administration

FLIP enforces TOTP (Time-based One-Time Password) MFA for every Cognito user. The enforcement lives in the **application layer**, not in the Cognito pool configuration — the pool's `mfa_configuration` is deliberately set to `OPTIONAL`. This section explains why, how administrators reset MFA for other users, and how an administrator who has lost their own authenticator can recover via the AWS CLI.

##### Why app-layer MFA (not pool `mfa_configuration = "ON"`)

Cognito exposes no admin API to delete a user's verified TOTP secret. With the pool set to `ON`, calling `AdminSetUserMFAPreference` with `Enabled=False` leaves the old secret registered in Cognito — at the next sign-in, Cognito still issues a `SOFTWARE_TOKEN_MFA` challenge and asks the user for a code their (lost) authenticator can no longer generate. The account becomes permanently locked out short of deleting and recreating the user.

With `mfa_configuration = "OPTIONAL"`, disabling the preference actually takes effect: Cognito signs the user in without a challenge. The application layer then catches the user — `flip-api` `verify_token` checks whether `SOFTWARE_TOKEN_MFA` is present in the user's `UserMFASettingList` and returns 403 if not, and the `flip-ui` router guard routes the user to the post-auth enrolment page where they mint a fresh TOTP secret. First-time users follow the same path: they sign in with their temporary password, the app sees `mfaEnabled=false`, and they are walked through enrolment before any protected route is reachable.

SMS MFA is intentionally disabled — it would introduce an SNS dependency and reintroduce SIM-swap risk. The rationale is documented inline at `deploy/providers/AWS/modules/cognito/main.tf` (around the `mfa_configuration` line) and the enforcement point lives in `flip-api/src/flip_api/auth/dependencies.py` (`verify_token`).

##### `ENFORCE_MFA` flag

The MFA gate is controlled by `flip-api`'s `ENFORCE_MFA` setting. The Settings default is `true` — when active, `verify_token` returns 403 for any signed-in user without an active TOTP and the UI's router guard redirects them through enrolment.

The dev override lives in `deploy/compose.development.yml` (`ENFORCE_MFA=false`) so local development doesn't force enrolment on a burner authenticator. **The flag is intentionally not exposed in `.env.development.example` or AWS Secrets Manager** — the dev compose override is the only place it should appear, and stag/prod leave it untouched so the gate stays on. Adding `ENFORCE_MFA` to a stag/prod environment file would silently disable MFA for the entire deployment.

The flag is mirrored to the UI via `/users/me/mfa/status` (`required: bool`) so the router guard knows when to skip the enrolment redirect.

##### Resetting MFA for another user

For users other than yourself, use the FLIP Admin UI. See the *Reset User MFA* subsection in [`docs/source/sys-admin/admin-project-and-user-management.rst`](../docs/source/sys-admin/admin-project-and-user-management.rst) for the step-by-step flow. The UI calls `POST /users/{user_id}/mfa/reset` on `flip-api`, which runs the same two Cognito operations documented below but under the FLIP permission model (requires `CAN_MANAGE_USERS`) and leaves an application-level audit trail.

##### Recovering an administrator account that has lost its authenticator

This runbook is for the case where **you** have lost access to your TOTP device and the UI flow above is therefore unavailable (you cannot sign in to reach the Admin Area). Another operator with AWS credentials runs these commands on your behalf. Direct AWS CLI access is required:

**Prerequisites:**

- AWS credentials for the account that hosts the Cognito user pool (the same SSO profile used for `make full-deploy`)
- IAM permissions for `cognito-idp:AdminSetUserMFAPreference` and `cognito-idp:AdminUserGlobalSignOut`

**Steps:**

1. Fetch the Cognito user pool ID from Terraform (or read it from the AWS Console):

   ```bash
   # From the stack the admin belongs to — prod/stag root or dev root
   cd deploy/providers/AWS         # or deploy/providers/AWS/dev
   tofu output -raw cognito_user_pool_id
   ```

2. Clear the locked-out administrator's TOTP preference:

   ```bash
   aws cognito-idp admin-set-user-mfa-preference \
     --user-pool-id "$USER_POOL_ID" \
     --username admin@example.com \
     --software-token-mfa-settings Enabled=false,PreferredMfa=false
   ```

3. Revoke the administrator's existing refresh tokens so no pre-reset session can keep operating:

   ```bash
   aws cognito-idp admin-user-global-sign-out \
     --user-pool-id "$USER_POOL_ID" \
     --username admin@example.com
   ```

4. The administrator now signs in with their existing password. Because `SOFTWARE_TOKEN_MFA` is no longer in their `UserMFASettingList`, the `flip-api` MFA gate and the `flip-ui` router guard funnel them through the post-auth enrolment page where they register a new authenticator. Their password does not need to be reset.

> **Note:** These two CLI commands have exactly the same server-side effect as clicking **Reset MFA** in the Admin UI — the UI endpoint (`reset_user_mfa` in `flip-api/src/flip_api/utils/cognito_helpers.py`) calls `admin_set_user_mfa_preference` followed by `admin_user_global_sign_out`. The CLI path exists only because it does not require a signed-in FLIP session.
>
> **Warning:** This path is an AWS-level escape hatch and is **not** audit-logged inside FLIP. Use it only for administrator self-recovery. For any user who is not currently locked out of FLIP itself, prefer the Admin UI flow so the reset is captured in the application logs.

## Service Authentication

FLIP uses three separate authentication mechanisms for service-to-service communication. Generate all keys
in one go with:

```bash
make generate-trust-api-keys
make generate-internal-service-key
make generate-trust-internal-service-keys
```

### Trust API Keys (trust-api → flip-api)

Each trust has a unique API key stored in the `TRUST_API_KEYS` JSON dict and sent in the `TRUST_API_KEY_HEADER` header.
The hub stores only SHA-256 hashes of these keys in `TRUST_API_KEY_HASHES`. Used for task polling, cohort result
submission, and heartbeat endpoints.

### Internal Service Key (fl-server → flip-api)

The fl-server on the Central Hub authenticates to flip-api using `INTERNAL_SERVICE_KEY` (a plain string) sent
in the `INTERNAL_SERVICE_KEY_HEADER` header. The hub validates it against `INTERNAL_SERVICE_KEY_HASH` (the
SHA-256 hash of the key). Used for model status updates, training metrics, and training log endpoints. This
is a **single, hub-internal** secret and is separate from the per-trust internal keys below.

### Trust-Internal Service Keys (trust-api / imaging-api / fl-client → imaging-api / data-access-api)

Inside each trust, every call from trust-api / imaging-api / fl-client to imaging-api or data-access-api
carries a shared-secret header. The header name comes from `TRUST_INTERNAL_SERVICE_KEY_HEADER` (default
`X-Trust-Internal-Service-Key`); the value is the per-trust plaintext key from `TRUST_INTERNAL_SERVICE_KEYS`
(a JSON dict keyed by trust name). Receivers compare the header against their own copy with a constant-time
compare. `/health` is intentionally exempt so liveness probes still work.

Each trust gets a distinct key — a leak in `Trust_1` cannot drive operations on `Trust_2`'s APIs. The hub
never sees these keys: they live only in trust-side env (extracted by `trust/Makefile` via `get_json_value`
at deploy time, exactly like `TRUST_API_KEYS`). See the **Trust-internal Service Authentication** section
in the repo-root [`CLAUDE.md`](../CLAUDE.md) for the full threat model.

FL clients (trust side) **do not** have Central Hub API credentials. Only the fl-server communicates with flip-api.
FL clients relay metrics and exceptions to the fl-server, which forwards them to the Central Hub.

The fl-server must reach flip-api via `FLIP_API_INTERNAL_URL` — a Docker-network URL such as
`http://flip-api:8000/api`. It must **not** go through the public `CENTRAL_HUB_API_URL` because the
CloudFront distribution in front of flip-api whitelists only `Authorization`, `Content-Type`, and
`Origin` and strips `X-Internal-Service-Key` at the edge, which would break this handshake. The
public `CENTRAL_HUB_API_URL` is reserved for flip-ui and trust-side (trust-api) consumers that live
outside the hub's Docker network.

| Variable | Where used | Purpose |
|---|---|---|
| `TRUST_API_KEY_HEADER` | flip-api, trust-api | Header name for trust auth |
| `TRUST_API_KEYS` | trust-api | JSON dict of trust name → plaintext key |
| `TRUST_API_KEY_HASHES` | flip-api | JSON dict of trust name → SHA-256 hash |
| `INTERNAL_SERVICE_KEY_HEADER` | flip-api, fl-server | Header name for internal service auth |
| `INTERNAL_SERVICE_KEY` | fl-server | Internal service plaintext key (plain string) |
| `INTERNAL_SERVICE_KEY_HASH` | flip-api | SHA-256 hash of internal service key (plain string) |
| `TRUST_INTERNAL_SERVICE_KEY_HEADER` | trust-api, imaging-api, data-access-api, fl-client | Header name for trust-internal service auth (default `X-Trust-Internal-Service-Key`) |
| `TRUST_INTERNAL_SERVICE_KEYS` | trust-side only | JSON dict of trust name → per-trust plaintext key. The hub never sees these. |
| `CENTRAL_HUB_API_URL` | flip-ui, trust-api | Public base URL of flip-api (in prod: CloudFront URL) |
| `FLIP_API_INTERNAL_URL` | fl-server | Docker-network URL of flip-api on the Central Hub (e.g. `http://flip-api:8000/api`) |

#### Note on the ECS migration

The Central Hub is moving from EC2 + docker-compose to ECS Fargate; the foundation Terraform (cluster,
EFS, IAM, parameter store, service discovery, VPC endpoints) has landed under `deploy/providers/AWS/`,
but task definitions and services are still being rolled out. `FLIP_API_INTERNAL_URL` names the intent
("flip-api's internal URL on the Central Hub"), not the mechanism, so the split survives the move. When
migrating, point it at whichever in-VPC, header-preserving endpoint flip-api exposes:

| ECS layout | `FLIP_API_INTERNAL_URL` |
|---|---|
| Sidecar (both containers in one task, awsvpc) | `http://localhost:8000/api` |
| Separate services + ECS Service Connect | `http://flip-api:8000/api` |
| Separate services + Cloud Map private DNS | `http://flip-api.<namespace>.local:8000/api` |
| Separate services + internal ALB | `http://<internal-alb-dns>/api` |

What it must not be: the public CloudFront URL. That's orthogonal to compute — CloudFront strips
`X-Internal-Service-Key` regardless of whether flip-api runs on EC2 or ECS. Internal ALBs preserve
all request headers by default, so that option works; CloudFront doesn't.

> **Note on troubleshooting**: AWS-deployment-specific failure modes (Terraform state drift, ECS
> service stuck in `PENDING`, CloudFront cache invalidation, RDS connectivity) are documented in
> [`providers/AWS/TROUBLESHOOTING.md`](providers/AWS/TROUBLESHOOTING.md).
