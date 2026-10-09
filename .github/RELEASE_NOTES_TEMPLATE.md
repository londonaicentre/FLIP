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

- **The development stack needs no AWS account** (#1274, #1337, #1374, #1385, #1403) — the dev hub signs users in through a local Keycloak instead of Cognito and stores files in a local S3-compatible RustFS store instead of S3, and the dev trusts download the XNAT WAR and plugins from upstream, checked against pinned SHA-256s, instead of from an S3 bucket. A new contributor can run `make up` without AWS SSO. flip-api picks its identity provider with `AUTH_BACKEND`: `keycloak` is the only value a dev hub accepts and `cognito` the only value a production hub accepts, and production defaults to it, so a deployed hub needs no new configuration. A CI smoke boots the hub, logs in through Keycloak and round-trips a file through the object store without AWS.
- **Flower 1.38** (#1272) — the Flower backend moves from 1.36 to 1.38. Each SuperNode is now registered with its trust's name, which the ServerApp can see. The SuperLink's Control API moves from gRPC on 9093 to HTTP on 8000, Flower's default.
- **Terraform CI for Landing Zone (LZA) accounts** (#1300, #1357, #1358, #1364) — a least-privilege CI bootstrap module, unattended plan/apply/drift for `PROD=lza` and `PROD=lza-stag`, and a Terraform-managed Ark+ demo-assets bucket.

## :warning: Breaking Changes

- **Flower hub and Flower sites upgrade together** (#1272). The hub's SuperLink and every trust's SuperNode move from Flower 1.36 to 1.38 in the same Deployment-Mode window. NVFLARE hubs and sites are unaffected.
- **The Flower SuperLink Control API is on port 8000 over HTTP** (#1272). Anything outside FLIP that called the gRPC Control API on 9093 must move. The host-side published ports (`FLOWER_SUPERLINK_NET_{1,2}_PORT`) are unchanged.

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

<!-- Fill the first three lines in for EVERY release; they are not boilerplate. "Ordering" is where a
     flag-day is announced: a payload-cipher change or an FL-framework bump means hub AND sites in one
     Deployment-Mode window, and a site left behind fails every task until it moves. -->

- **Required:** **yes for Flower sites**, which must move to Flower 1.38 with the hub. NVFLARE sites: recommended, not required. The release fixes imaging-api reporting a failed XNAT upload as a success (#1397) and quotes XNAT URLs (#1352, #1399). **Yes, as a flag day, from v0.6.x or earlier** (v0.7.0's AES-256-GCM change has no CBC fallback).
- **Ordering:** Flower: hub and sites together, in one Deployment-Mode window. NVFLARE: sites at their own pace, before or after the hub.
- **Refreshed kit needed:** no. The Hub-shared block is unchanged.
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}        # the compose files and the verb come from the checkout, not the images
  sudo -E make upgrade-onprem-trust KIT=<slot>           # defaults to the release the hub runs; TAG={{TAG}} pins it before the hub moves
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> PROD=<env> TAG={{TAG}}`; EC2: `make -C deploy/providers/AWS upgrade-trust-ec2 KIT=<CODE> PROD=<env> TAG={{TAG}}`, both from a checkout at {{TAG}}. Runbook: *docs → System administrators → Upgrading a site*.

## :seedling: New Features

- `AUTH_BACKEND` and an `IdentityProvider` interface in flip-api (`flip_api/auth/identity/`), with Keycloak and Cognito providers behind one OIDC verifier. flip-ui follows the same setting (#1274).
- `trust/xnat/artifacts.manifest` lists the XNAT WAR and plugins with their SHA-256s and upstream URLs, and is used by the dev cache, `make build` and CI. `make -C trust prepare-artifacts` fetches and verifies them; `ARTIFACTS_DIR=<dir>` copies them from a local directory instead, for an offline host (#1374).
- A dev object store (RustFS) behind the unchanged boto3 code, with presigned-URL audiences, an origin allow-list, and `make clean-object-store` (#1337).
- Flower SuperNodes are registered with `--name <trust>`. Key registration waits for the SuperLink Control API instead of racing it on a cold start (#1272).
- imaging-api builds every XNAT URL through one segment-quoting helper (#1352, #1399).
- tflint runs over the AWS Terraform in CI (#1394). CI generates throwaway test credentials per run, so PRs from forks pass (#1383).

## :bug: Bug Fixes

<!-- Update this section if a fix lands before the cut. -->

- **Disable User works on AWS hubs.** It failed with AccessDenied because the flip-api task role lacked `cognito-idp:AdminDisableUser`, `AdminEnableUser` and `AdminSetUserMFAPreference` (#1369). Disabling or enabling a user on a hub with no trusts no longer returns a 500 (#1346).
- **imaging-api no longer reports a rejected XNAT file upload as a success.** A PUT that XNAT refused was logged as uploaded and returned in the success list. It now raises with XNAT's status code (#1397).
- The `FlipSG` tag on security groups no longer flips on every Terraform apply, which had caused a permanent plan diff and gaps in the drift alarm (#1393).
- dcm2niix runs as the XNAT uid, and the dev `xnat-reset` skips `sudo` when the operator already owns the tree (#1317).
- A fresh dev stack comes up: four bring-up failures fixed, including the Keycloak health check (#1385).
- `make central-hub` creates `central-hub-trust-apis-network` on a fresh host (#1347). The on-prem readiness checklist resolves the kit through Make (#1355).
- The dev env example no longer pads `AWS_REGION` with trailing spaces, which made every presign return 500 on a fresh dev hub. `make restart-fl` creates the object-store dir before stopping any FL client (#1403).
- Every tracked shell script works on macOS bash 3.2 (#1395).
- Dependencies with open security advisories are upgraded (#1388).

## :white_check_mark: Release checks

<!-- The gates that cannot run in CI: no GitHub-hosted runner has a GPU, and both the suite and the smoke test need real hardware and a full stack. Tick these on the release branch before the tag is cut — this section is the record that the published examples and the platform path were run, and it is shared by both release trains. See *Pre-release checklist* in CONTRIBUTING.md. -->

Tutorial suite, on a GPU host:

- [ ] NVFLARE — `make -C fl-tutorials run-all-tutorials`
- [ ] Flower — `make -C fl-tutorials run-all-tutorials FL_BACKEND=flower`
- [ ] Host and date recorded: <!-- e.g. "RTX 5090 workstation, 24 September 2026" -->

Full-platform smoke test, against a running deployment:

- [ ] NVFLARE — `make e2e_smoke`
- [ ] Flower — `make e2e_smoke FL_BACKEND=flower`

<!-- Record host, date, trusts and image tags here once both pass. -->

## :file_folder: PRs merged in this release

<!-- auto-populated by the release workflow -->

## :star: Acknowledgements

A big thank you to the following contributors for their work on this release:

<!-- auto-populated by the release workflow -->

---
