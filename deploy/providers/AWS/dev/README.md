# Dev-account Terraform root

This directory holds the Terraform stack for the **dev AWS account**. It deploys only the services that cannot reasonably run on a developer workstation:

- **Cognito** — user pool, hosted-UI domain, app client, seed admin/researcher users
- **S3** — the three FLIP application buckets (model-file uploads, FL results, app bundles)

Everything else (VPC, EC2, RDS, ALB, NLB, Route53, ACM, IAM, CloudWatch) is intentionally **not** part of this stack; local development runs those services via Docker Compose in this repository's `deploy/` root compose files. SES is not part of it either, and is not run locally at all — see below. The prod/stag stack at `deploy/providers/AWS/` is the source of truth for every non-dev environment.

**No SES.** Development sends no email: flip-api defaults to `EMAIL_BACKEND=console`, which logs the would-be message instead of calling SES (FLIP#919). The dev account's SES sender identity and three templates were destroyed as part of that change, and this root no longer instantiates `modules/ses` — only prod/stag do. Re-adding SES to dev would mean re-verifying the sender address by email. Cognito's own invite and password-reset emails are unaffected; the user pool uses Cognito's default sender, not SES.

The Cognito and S3 resource definitions are shared with the prod stack via the modules under `deploy/providers/AWS/modules/{cognito,flip_s3_bucket}/`, so any change to either service lands in both environments from the same code.

## Prerequisites

1. An AWS SSO profile for the FLIP dev AWS account configured via `aws configure sso`. By default the Makefile expects `AWS_PROFILE=dev` and refuses to run if it sees the prod or stag profile. Add a short alias to `~/.aws/config`:

   ```ini
   [profile dev]
   sso_session = FLIP
   sso_account_id = <dev-sso-account-id>
   sso_role_name = <sso-role-name>
   region = <aws-region>
   output = json
   ```

   Replace each `<…>` with the value from the FLIP AWS account directory.

   If your local profile names differ, override `DEV_AWS_PROFILE` (and/or `PROD_AWS_PROFILE` / `STAG_AWS_PROFILE` for the refusal guard) in `.env.development` or on the make command line.
2. A Terraform state bucket reachable from the dev account. The state **key** is hard-coded to `flip/dev/terraform.tfstate`; the bucket name comes from `FLIP_TFSTATE_BUCKET_NAME` in `.env.development`. If you need to create/bootstrap that bucket, run `make create-backend` from this directory.
3. A populated `.env.development` at the repo root. This is the same file the local Docker Compose dev stack uses — no second env file to maintain.

### Variables read from `.env.development`

| Variable | Used for |
| --- | --- |
| `AWS_PROFILE` | dev SSO profile; also guarded against prod/stag account IDs |
| `AWS_REGION` | region for the dev Cognito + S3 resources |
| `flip_cognito_admin_email` | mailbox for the seed admin user — it receives the Cognito invite. Required; the Makefile fails at parse time if it is unset or still a `<placeholder>`. An exported `TF_VAR_flip_cognito_admin_email` is accepted instead, and this name wins when both are set |
| `ADMIN_USER_PASSWORD` | initial password for the seed admin (and researcher, if set) |
| `FLIP_TFSTATE_BUCKET_NAME` | S3 bucket for `flip/dev/terraform.tfstate` |
| `FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME`, `FLIP_FL_RESULTS_BUCKET_NAME`, `FLIP_APP_BUNDLES_BUCKET_NAME` | the three application buckets; each guarded at parse time |
| `flip_cognito_researcher_email` (optional) | set to create a second seed user; leave unset to skip |

`.env.development.example` already declares most of these; add whatever's missing. The file is git-ignored.

## Usage

All commands are run from this directory. The dev Makefile loads `.env.development`, guards against the prod/stag AWS accounts, and talks to the dev-only Cognito + S3 Terraform root.

```bash
cd deploy/providers/AWS/dev
make create-backend  # one-time, if the backend bucket needs bootstrapping
make init            # one-time, or after backend config changes
make plan            # preview
make apply           # deploy
make status          # terraform state list
make destroy         # refused while prevent_destroy is set
```

### Browser-usable UI ports

flip-api derives its CORS allowlist from the app client's callback URLs, and Cognito matches those
exactly (no wildcard, no range), so a UI port is usable in a browser only if it is registered here.
Rather than an apply per port, `var.dev_ui_ports` (default 44350–44359) is expanded into one
`http://localhost:<port>` origin each and added to both the callback list and the buckets' CORS
origins — the same list for both, rather than an S3 wildcard, so a bucket is never reachable from an
origin the API would refuse (FLIP#1227). Pick a port from the list as `UI_PORT` and nothing here
needs to change.

Do not reach for the other registered entry, `https://localhost:443`. It matches only a UI actually
served over TLS, and the dev container is not: `npm run dev` is `vite --host 0.0.0.0 --port 443`
with no `server.https` and no cert material, so a browser on the repo's default `UI_PORT` sends
`Origin: http://localhost:443`, which nothing registers.

The generated entries are origins, not live redirect targets. `modules/cognito` leaves
`allowed_oauth_flows_user_pool_client` unset (provider default false), so the hosted UI cannot
redirect to any of them and flip-api reads the list purely as origins (`flip_api/utils/cors.py`).
Enabling that client flag on the dev pool means revisiting this list first — the module's `implicit`
flow would otherwise hand tokens to whichever local process won the race for one of these ports.

On the shared dev host the ports are taken by convention, one per developer. This table is the
canonical claim list: anything else describing the convention — including the `CONTRIBUTING.md`
"Shared dev host" section FLIP#1227 asks for — links here rather than restating it.

| Port | Developer |
| --- | --- |
| 44357 | yl |
| 44356 | at24 |
| 44355 | next |

A port outside the list, or a host other than `localhost`, needs an apply — see "Changing the
browser CORS allowlist" in [`../README.md`](../README.md).

## First-time setup

The dev resources are Terraform-managed from day one. There is no import workflow — the stack creates every resource it needs.

### Joining an already-bootstrapped dev account

If a colleague has already run `make apply` against the shared dev account, there is nothing for you to create:

```bash
cd deploy/providers/AWS/dev
make init          # pulls providers, wires up the shared S3 backend
make plan          # should report "No changes"
terraform output   # Cognito pool ID, app client ID, domain, bucket names
```

Copy the outputs into your `.env.development` (`CognitoUserPoolId`, `CognitoAppClientId`, `CognitoDomain`) and you are ready to run the local Docker Compose stack against real Cognito.

### Bootstrapping a fresh dev account

Use this the first time the dev account is provisioned, or after a clean-slate reset:

```bash
cd deploy/providers/AWS/dev
make create-backend  # creates the S3 state bucket (idempotent; safe to re-run)
make init
make plan            # should only propose additions
make apply
```

Post-apply:

1. **Cognito seed users** — Cognito emails the admin (and researcher, if configured) an invite with a temporary password. First sign-in forces a password change.
2. **Read the outputs** — `terraform output` gives the IDs you need for `.env.development`.

### Rebuilding from scratch

To wipe the dev stack and start over: `terraform destroy` is refused by the `prevent_destroy` lifecycle blocks on the shared `cognito` and `flip_s3_bucket` modules (those blocks exist to protect the prod pool and buckets, which use the same modules). Instead:

1. Delete the resources manually in the AWS console / CLI (user pool domain, user pool, and the three application buckets).
2. `terraform state rm` each resource from the dev state, or delete the state object at `s3://$FLIP_TFSTATE_BUCKET_NAME/flip/dev/terraform.tfstate` for a full reset.
3. Re-run the **Bootstrapping a fresh dev account** flow above.

## Day-to-day use

```bash
cd deploy/providers/AWS/dev
make plan       # preview changes
make apply      # deploy
make status     # list resources under terraform management
```

## Relationship to the prod/stag stack

- `deploy/providers/AWS/` — prod + stag (VPC, EC2, RDS, ALB, NLB, ACM, IAM, and Cognito + S3 + SES via modules)
- `deploy/providers/AWS/dev/` — **this directory**, only Cognito + S3 via the same modules (no SES)
- `deploy/providers/AWS/modules/{cognito,flip_s3_bucket}/` — single source of truth for both envs

A change to the Cognito config (token lifetimes, MFA mode, email templates) goes in the module and rolls out to both environments via each stack's own `apply`. A change that should land in only one environment goes in that stack's root (via module input variables).
