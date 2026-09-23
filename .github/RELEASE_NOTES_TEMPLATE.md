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

- **Authenticated encryption for every hub↔trust payload** (#1180) — AES-256-GCM in a versioned, key-id'd envelope, with the payload's purpose bound into the authentication tag, replacing unauthenticated AES-CBC. A tampered payload, a wrong key or a re-targeted task now fails closed instead of decrypting to plausible plaintext. **There is no CBC fallback** — see Breaking Changes.
- **LZA-managed AWS estates** (#749, via #979, #1182, #1192) — `PROD=lza` / `PROD=lza-stag` deployment modes for Landing Zone Accelerator workload accounts, the web leg served from the internal NLB with static targets (no relay Lambda), and an LZA variant of the Central Hub diagrams and deploy pages.
- **The trust node, in its shapes** (#1213, via #1214) — the Helm chart and the on-prem Ansible play move under `trust/deploy/` beside the compose files, leaving `deploy/providers/` as infrastructure provisioning only.
- **OHIF viewer ships as a default XNAT plugin** (#1243, via #1244) in every deployment mode — compose, EC2, on-prem and Kubernetes — with the plugin roster pinned across `ensure_plugins.sh` and the Helm chart.
- **FL apps are offline by contract** (#1206, #1208, via #1207, #1209) — nothing under `fl-apps/` or a tutorial's app directory may fetch weights at run time, weights ship as scanned uploads staged by `make -C fl-tutorials download-weights`, and every `torch.load` is pinned to `weights_only=True`. An AST guard in CI enforces both.
- **Flower tutorials run on the flwr simulator** (#1159) with app code identical to the platform's — `make -C fl-tutorials sim-tutorial TUTORIAL=… FL_BACKEND=flower`, no containers, site identity from the run config.
- **Federated EHR risk prediction** (#1068) — a T2DM MLP over OMOP tabular data on Synthea open data, on both backends, exercising the tabular-only (no-imaging) project path end to end.
- **XNAT invites, not emailed passwords** (PT-079, via #977) — new trust users get a set-password link instead of a credential in an email, with enclave branding on the templates.
- **September security review landed** (#1202) — enforcing CSP, an authenticated `GET /api/trust/health`, a real `BUNDLE_URL_ALLOWED_HOSTS` with resolved bundle hosts, a sign-out that discards the page's cache, a fork-PR image-build guard and a narrowed omop-db grant.
- **CI proportional to the change** (#1223, #1254) — the service suites run only for the paths they cover on PRs into `develop`, with the full suite on PRs into `main`; and detect-secrets can now actually fail the build (#1215, via #1216).

## :warning: Breaking Changes

- **AES-256-GCM is a flag day** (#1180). There is no compatibility with the pre-#1179 CBC format, so the hub and **every** trust registered to it must upgrade together: enable Deployment Mode, wait for FL to quiesce, then redeploy the hub and all trust services. `AES_KEY_BASE64` must decode to exactly **32 bytes** — a 16- or 24-byte key is now rejected rather than silently running AES-128/192 — and any mismatch surfaces as `Invalid payload: failed authentication` on every task.
- **`MIN_CLIENTS` is removed** (#1230, via #1233 and #1235). The FL quorum is a property of a job, not of the federation: fl-api already writes the project's trust count into every job. Delete the line from kit and env files — a leftover is ignored. On stag/prod, dropping it from the fl-server task definitions is a task-definition revision, so the Terraform apply trips the FL quiesce gate (#770).
- **Trust deployment paths moved** (#1214): `deploy/providers/kubernetes/` → `trust/deploy/helm/`, and `deploy/providers/local/site_local_trust.yml` → `trust/deploy/ansible/onprem.yml`. The `make` verbs (`deploy-trust-k8s`, `provision-local-trust`) are unchanged; anything referencing the old paths directly — operator scripts, CI filters, local checkouts — needs updating.
- **`GET /api/trust/health` now requires authentication** (#1202). It previously returned every trust's id, name and online flag to any anonymous caller behind CloudFront. No FLIP consumer changes: the UI already polls it beside the authenticated `/trust`.
- **flip-ui's Content-Security-Policy is enforcing, not report-only** (#417, via #1202), and gains `base-uri 'none'` + `form-action 'self'`. A deployment that relied on report-only tolerance will now see violations blocked.
- **FL apps may not download at run time** (#1206, #1208). `pretrained=True`, torchvision weight enums, `torch.hub.load`, `from_pretrained("org/x")`, `load_state_dict_from_url`, MONAI bundle downloads and any `torch.load` without `weights_only=True` fail the guard inside `fl-apps/` and `fl-tutorials/**/app*/`. Operator-provided app templates need the same treatment; weights ship as uploaded files instead.

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

<!-- Fill the first three lines in for EVERY release; they are not boilerplate. "Ordering" is where a
     flag-day is announced: a payload-cipher change or an FL-framework bump means hub AND sites in one
     Deployment-Mode window, and a site left behind fails every task until it moves. -->

- **Required:** <yes | no> — <what in this release changes the hub↔site contract, or "nothing; upgrade at your convenience">.
- **Ordering:** <hub first, sites at their own pace | sites first | hub and sites together in one maintenance window (flag-day: <why>)>.
- **Refreshed kit needed:** <no — the Hub-shared block is unchanged | yes — <which value changed>>. (If the hub's AES key or FL kit date changed with your hub deploy, re-sync: `make sync-trust-kit KIT=<CODE> PROD=<env>` → `make -C deploy/providers/AWS package-onprem-trust-kit KIT=<CODE>`.)
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}        # the compose files and the verb come from the checkout, not the images
  sudo -E make upgrade-onprem-trust KIT=<slot>           # defaults to the release the hub runs; TAG={{TAG}} pins it before the hub moves
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> PROD=<env> TAG={{TAG}}`; EC2: `make -C deploy/providers/AWS upgrade-trust-ec2 KIT=<CODE> PROD=<env> TAG={{TAG}}` — both from a checkout at {{TAG}}. Runbook: *docs → System administrators → Upgrading a site*. Sites on v0.7.0 or earlier do not have the command until they check out the tag.

## :seedling: New Features

- LZA deployment modes and the internal-NLB web edge (#979, #1182); trust deployment layout under `trust/deploy/` (#1214); the OHIF viewer as a default XNAT plugin (#1244); XNAT set-password invites with enclave branding (#977).
- Flower simulator parity for the tutorials (#1159); the federated EHR T2DM risk-prediction tutorial on both backends (#1068); hash-checked, offline weight staging via `make -C fl-tutorials download-weights` (#1207, #1209).
- The project page's three cards fill the viewport (#1169).
- Path-gated service test suites on PRs into `develop` (#1223, #1254) and enforced repo-wide secret scanning (#1216).

## :bug: Bug Fixes

- **Security**: blind SSRF into the admin-authenticated XNAT API via `accession_id` (#1137); the September hardening set in #1202 — enforcing CSP (#417), authenticated trust health, the session cache surviving sign-out into the next account's tab (#995), bundle hosts resolved and held to the allow-list (#905), a fork-PR image-build guard (#882), an exception-text sweep (#906) and a narrowed omop-db read grant (#904); run-directory checkpoints loaded with `weights_only=True` (#1245, via #1246).
- **Trust / imaging**: multi-series cohorts queued and counted each study once per row instead of once per series (#1124); a rotated `xnat-db` password never reaching an already-initialised volume (#1072); `xnat-web` located by swarm label rather than a name substring (#1210, via #1211).
- **FL / tutorials**: the latent-diffusion tutorial aborting when a finished controller answered a sibling's round (#1178); the Flower simulator returning 0 whatever became of the run, and silently reusing a SuperLink left behind by another checkout (#1249, via #1250); `MIN_CLIENTS` rendering as `""` on a fresh clone (#1230, via #1233).
- **CI**: imaging-api's env-file step that nothing read (#1255).

## :file_folder: PRs merged in this release

<!-- auto-populated by the release workflow -->

## :star: Acknowledgements

A big thank you to the following contributors for their work on this release:

<!-- auto-populated by the release workflow -->

---
