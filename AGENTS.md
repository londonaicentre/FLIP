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

# AGENTS.md — FLIP

## Scope

These instructions apply to the whole repository. More specific `AGENTS.md` files in subdirectories override or extend
this file for work in that part of the monorepo.

## Repository Map

- `flip-api/`: Central Hub FastAPI backend.
- `flip-ui/`: Vue 3, TypeScript, Vite frontend.
- `trust/`: Trust-side services and local trust stack.
- `deploy/`: Docker Compose, local provisioning, and AWS deployment entry points.
- `deploy/providers/AWS/`: Terraform/OpenTofu, Ansible, S3/CloudFront UI deployment, ECS/EC2 infrastructure.
- `docs/`: Sphinx documentation.

## Monorepo Guidance

- Prefer the nearest scoped `AGENTS.md` before making service-specific changes.
- Prefer existing Make targets over raw commands when a target exists; they encode the expected environment and flags.
- Keep changes scoped to the affected service or deployment area. Do not refactor unrelated modules while addressing a
  local issue.
- Add or update tests for behavior changes. For cross-service changes, verify each affected service.
- Review documentation impact after changing APIs, env vars, deployment behavior, user workflows, or new dependencies.
- Do not commit generated runtime config such as `flip-ui/public/js/window.js`.
- Do not hardcode environment-specific values, secrets, image tags, Cognito IDs, API URLs, or AWS account details.
- Production and trust production Compose files should use `${DOCKER_TAG}` for application image tags unless a reviewer
  explicitly asks for a temporary pin.

## Common Commands

Run from the repository root unless noted:

```bash
make unit_test             # Unit tests across services
make tests                 # flip-ui + flip-api tests
make up                    # Start the full local stack
make up-no-trust           # Start central hub only
make down                  # Stop services
make build                 # Build Docker images
make create-networks       # Create required Docker networks
```

## Style And Workflow

- Python services use Python 3.12+, FastAPI, SQLModel, Pydantic, `uv`, Ruff, mypy, and pytest.
- The frontend uses Vue 3, TypeScript, Vite, Pinia, npm, ESLint, Vitest, and Cypress.
- Python line length is 120. Use Google-style docstrings for non-trivial public functions.
- Markdown and source files should keep the Apache 2.0 copyright header pattern already used in the repo.
- Commits are normally signed off by the human author with `git commit -s`; do not add an AI co-author trailer unless
  explicitly requested.

## Security And Deployment Cautions

- Never commit secrets or credentials. Keep `.env.*` files and generated runtime files out of commits unless the file is
  an example/template already tracked by the repo.
- Do not bypass TLS verification.
- Trust services communicate outbound to the Central Hub; do not introduce inbound trust-host requirements casually.
- Central Hub internal service auth uses headers that CloudFront intentionally does not forward. Use internal Docker or
  service URLs for internal service traffic, not the public CloudFront URL.
