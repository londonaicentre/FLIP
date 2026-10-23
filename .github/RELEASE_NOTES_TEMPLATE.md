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

- **A security hotfix for XNAT** (#1648). XNAT before 1.10.2.1 lets someone who can reach a trust's XNAT web
  interface, without logging in, create an account with up to site-administrator rights. Every trust's XNAT moves
  from 1.10.0 to 1.10.2.1; please upgrade promptly.
- **The first hotfix release.** v0.13.2 is v0.13.1 plus this fix only, cut from a `hotfix/v0.13.2` branch: PRs to
  `main` may now come from `hotfix/*` branches as well as `develop` (*CONTRIBUTING.md → Hotfixes*). Everything
  merged to `develop` since v0.13.1 waits for v0.14.0.

## :warning: Breaking Changes

- None.

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

- **Required: yes, promptly.** The flaw needs only network access to XNAT. Until a site has upgraded, restrict who
  can reach its XNAT web port (`XNAT_WEB_PORT`, or the `xnat-web` service on Kubernetes). Releases before {{TAG}}
  are no longer supported.
- **Ordering:** any order. The hub runs no XNAT. Until the hub reports {{TAG}}, pass `TAG={{TAG}}` to the upgrade
  verb, which otherwise defaults to the release the hub reports.
- **Kubernetes trusts:** required, as for on-prem sites. XNAT's Container Service plugin moves to 3.8.2, which the
  new XNAT needs.
- **Refreshed kit needed:** no. The Hub-shared block is unchanged. Remove any `XNAT_TAG` (or `xnat.image.pin`) that
  holds XNAT back.
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}        # the compose files and the verb come from the checkout, not the images
  sudo -E make upgrade-onprem-trust KIT=<slot> TAG={{TAG}}
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> HUB=<env> TAG={{TAG}}`; EC2: `make -C deploy/providers/AWS upgrade-trust-ec2 KIT=<CODE> HUB=<env> TAG={{TAG}}`, both from a checkout at {{TAG}}. Runbook: *docs → System administrators → Upgrading a site*.

## :bug: Bug Fixes

- Security: XNAT 1.10.0 → 1.10.2.1, the XNAT security hotfix for unauthenticated account creation (no CVE
  published yet; [XNAT announcement](https://wiki.xnat.org/news/xnat-security-hotfix-releases-1-10-2-1-1-9-3-9-and-1-8-10-6)).
  The Container Service plugin moves 3.8.1 → 3.8.2, which the new XNAT requires; DQR, Batch Launch and the OHIF
  viewer are unchanged (#1648).

## :white_check_mark: Release checks

<!-- These checks are release evidence and must be run for v0.13.2. Do not carry forward results
     from an earlier release. Record the host, date and outcome before merging the release PR. -->

A hotfix runs the checks that exercise its fix (*CONTRIBUTING.md → Hotfixes*). The fix changes XNAT only; no tutorial
or FL code changed since v0.13.1, so the tutorial suite is not re-run.

- [x] Full-platform smoke test, FLARE — `make e2e_smoke` (imports DICOM through XNAT 1.10.2.1): passed against the
  staging hub with two trusts on `v0.13.2-rc.1`, one on Docker Compose and one on Kubernetes. Each pulled the x-ray
  cohort into its XNAT (300/300 and 289/300); training reached `RESULTS_UPLOADED`. The query was the tutorial's
  vocabulary-free variant, because the Kubernetes trust has no core vocabulary loaded.
- [ ] Full-platform smoke test, Flower — not re-run. The staging hub's only FL net runs NVFLARE, and the fix touches
  XNAT alone: importing studies into XNAT is the same for both backends, and no FL code changed since v0.13.1.
- [x] Kubernetes trust on XNAT 1.10.2.1 with Container Service 3.8.2 — `make -C trust/deploy/helm status` (plugin
  roster matches the values and XNAT loaded all four), `smoke-cstore` (a C-STORE from the PACS logged in
  `received.log`, no importer failure) and one dcm2niix conversion (container `Complete`, NIfTI resource written):
  all passed.
- [x] `make unit_test`: passed (flip-api 2220, flip-ui 1600, data-access-api 464, imaging-api 804, omop-db 115,
  trust-api 174, trust/xnat 511).
- [x] Host, date, deployment, and results recorded: 23 Oct 2026, both trusts upgraded in place from v0.12.0 with
  the release's upgrade verbs (`upgrade-onprem-trust`, `upgrade-trust-k8s`); unit tests on an RTX 5090 workstation.

## :file_folder: PRs merged in this release

<!-- auto-populated by the release workflow -->

## :star: Acknowledgements

A big thank you to the following contributors for their work on this release:

<!-- auto-populated by the release workflow -->
