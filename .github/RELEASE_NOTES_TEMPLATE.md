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

# :package: {{PROJECT}} {{VERSION}} Release Notes

## :sparkles: Highlights

- **The web cutover to the LZA estate becomes a switch you choose, not a DNS change** (#749, via #1296) — `RELEASE_WEB_ALIAS` drops the public web name from an account's CloudFront distribution so another estate's edge can serve it. CloudFront picks a distribution by `Host` header and prefers an exact alias over a wildcard, so pointing DNS at the new edge moves nothing for the web on its own; this flag is the moment it moves. **Off everywhere by default** — nothing changes until it is set on a GitHub environment.
- **Terraform CI can drive the LZA workload accounts** (#1199, via #1273) — `aws-stag` / `aws-prod` select the estate they apply with a `TF_PROD` variable (`stag`, `true`, `lza-stag` or `lza`) and fall back to the self-contained pair when it is unset, which is where they stay for now.
- **The CI keypair parameter is declared, and `ci/` explains a missing OIDC provider** (#1199, via #1299) — `/flip/ci/host_aws_public_key` becomes a Terraform resource instead of a make target, and `make -C ci plan` stops with who should declare the account's GitHub OIDC provider rather than Terraform's bare "no matching OpenID Connect Provider found".
- **XNAT on Kubernetes trusts accepts DICOM again** (#1228, via #1231) — `xnat-web` could not roll onto its single-attach volume, so an upgraded pod never went Ready and the old one kept serving plugin jars built for a different XNAT core, aborting every C-STORE while C-ECHO still passed.
- **Cohort query plots use the width they are given** (#1293, via #1294).

## :warning: Breaking Changes

- **`make seed-ci-keypair-param` is gone** (#1299). The parameter it published is now `aws_ssm_parameter.ci_host_aws_public_key` in the main root: in a new account the first laptop apply creates it; in an account that already has it, the first apply adopts it (`overwrite = true`, rewriting the bytes CI has just read — a no-op).
- **On Kubernetes trusts, upgrading restarts XNAT rather than rolling it** (#1231). `xnat-web` now uses the `Recreate` strategy — a singleton on a `ReadWriteOnce` volume cannot roll — so XNAT is unavailable while the new pod starts. Upgrade outside an image pull.

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

<!-- Fill the first three lines in for EVERY release; they are not boilerplate. "Ordering" is where a
     flag-day is announced: a payload-cipher change or an FL-framework bump means hub AND sites in one
     Deployment-Mode window, and a site left behind fails every task until it moves. -->

- **Required:** no, if you are on v0.7.0 or later — nothing in this release changes the hub↔site payload contract. **Recommended for Kubernetes trusts** whose XNAT refuses C-STORE while C-ECHO passes (#1231). **Yes, and as a flag day, if you are on v0.6.x or earlier**: you cross v0.7.0's AES-256-GCM change on the way here, and that has no CBC fallback.
- **Ordering:** hub first, sites at their own pace — *unless* you are coming from v0.6.x, in which case hub and sites move together in one Deployment-Mode window and a site left behind answers every task `Invalid payload: failed authentication`.
- **Refreshed kit needed:** no — the Hub-shared block is unchanged. (If the hub's AES key or FL kit date changed with your hub deploy, re-sync: `make sync-trust-kit KIT=<CODE> PROD=<env>` → `make -C deploy/providers/AWS package-onprem-trust-kit KIT=<CODE>`.)
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}        # the compose files and the verb come from the checkout, not the images
  sudo -E make upgrade-onprem-trust KIT=<slot>           # defaults to the release the hub runs; TAG={{TAG}} pins it before the hub moves
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> PROD=<env> TAG={{TAG}}`; EC2: `make -C deploy/providers/AWS upgrade-trust-ec2 KIT=<CODE> PROD=<env> TAG={{TAG}}` — both from a checkout at {{TAG}}. Runbook: *docs → System administrators → Upgrading a site*. Sites on v0.7.0 or earlier do not have the command until they check out the tag.

## :seedling: New Features

- The public web alias released on a switch of its own, `RELEASE_WEB_ALIAS`, carried through the CI manifest and all three Terraform workflows (#1296).
- Terraform CI for the LZA workload accounts, with the estate selected by `TF_PROD` and a class check refusing a prod-grade estate on a staging ref (#1273).
- The CI keypair parameter declared in Terraform, and a `check-oidc-provider` preflight on `make -C ci plan` (#1299).
- Cohort query plots in a responsive auto-fill grid instead of a fixed two-column layout (#1294).

## :bug: Bug Fixes

- **Kubernetes trusts**: XNAT aborting every C-STORE because `xnat-web` could not roll onto its `ReadWriteOnce` volume and kept serving stale DQR / Container Service plugin jars; the chart now recreates the pod, and a Helm timeout keeps a slow `xnat-init` hook from leaving the release failed (#1228, via #1231).

## :white_check_mark: Release checks

<!-- The gates that cannot run in CI: no GitHub-hosted runner has a GPU, and both the suite and the smoke test need real hardware and a full stack. Tick these on the release branch before the tag is cut — this section is the record that the published examples and the platform path were run, and it is shared by both release trains. See *Pre-release checklist* in CONTRIBUTING.md. -->

Tutorial suite, on a GPU host:

- [ ] NVFLARE — `make -C fl-tutorials run-all-tutorials`
- [ ] Flower — `make -C fl-tutorials run-all-tutorials FL_BACKEND=flower`
- [ ] Host and date recorded: <!-- e.g. "RTX 5090 workstation, 24 September 2026" -->

Full-platform smoke test, against a running deployment:

- [ ] NVFLARE — `make e2e_smoke`
- [ ] Flower — `make e2e_smoke FL_BACKEND=flower`

## :file_folder: PRs merged in this release

<!-- auto-populated by the release workflow -->

## :star: Acknowledgements

A big thank you to the following contributors for their work on this release:

<!-- auto-populated by the release workflow -->

---
