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

# AGENTS.md — AWS Deployment

## Scope

Applies to `deploy/providers/AWS/`.

## Key Areas

- Terraform/OpenTofu manages VPC, subnets, ECS Fargate, EC2 trust hosts, RDS, ALB/NLB, CloudFront, S3, Cognito, SES,
  IAM, Secrets Manager, SSM, and backend state.
- Persistent resources such as Cognito, Secrets, and S3 are intentionally preserved by destroy flows.
- Trust host access is via AWS SSM Session Manager. Do not introduce port 22 access.

## Commands

Run from `deploy/providers/AWS/`:

```bash
make aws-login
make init
make plan PROD=stag
make apply PROD=stag
make full-deploy PROD=stag
make full-deploy PROD=true
make deploy-centralhub
make deploy-ui PROD=stag
make deploy-trust
make status
make ssh-config
make forward-trust
make destroy
```

## Terraform And State

- Remote state uses S3 plus DynamoDB locking.
- Prefer `moved { ... }` blocks for Terraform address refactors when possible; do not instruct operators to perform
  manual `terraform state mv` if moved blocks already cover the migration.
- Keep `prevent_destroy` expectations in mind for Cognito, buckets, secrets, and other persistent resources.
- Run/read `verify_deploy_readiness.py` and existing tests when changing deployment preflight behavior.

## UI Deployment

- Staging/prod `flip-ui` is static S3 content behind CloudFront.
- `make deploy-ui` builds the UI, generates runtime `window.js` from the target environment, syncs to S3, and
  invalidates CloudFront.
- CloudFront `/api/*` forwards to the ALB. Be careful with forwarded headers, especially auth headers.
