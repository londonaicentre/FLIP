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

- **A security patch for v0.13.0** (#1612). Dependencies with open security advisories are upgraded: certifi and uv
  (critical) across the Python services and FL images, Pillow (critical/high) in flip-utils and the FL images, and
  vite and follow-redirects (high/medium) in flip-ui. No FLIP code or behaviour changes.

## :warning: Breaking Changes

- None. Pillow moves to 13.0, a major release; no FLIP code uses the APIs it removes. Researcher FL code that calls
  Pillow directly should check the [Pillow 13.0 release notes](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html).

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

- **Required:** recommended, not required. The release carries security fixes only; a v0.13.0 site keeps working
  with a v0.13.1 hub. Sites on v0.12.x or earlier must first follow the v0.13.0 notes (one coordinated window and a
  cohort-snapshot backfill).
- **Ordering:** any order. The hub and the sites can move independently.
- **Kubernetes trusts:** recommended, as for on-prem sites.
- **Refreshed kit needed:** no. The Hub-shared block is unchanged.
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}        # the compose files and the verb come from the checkout, not the images
  sudo -E make upgrade-onprem-trust KIT=<slot>           # defaults to the release the hub now reports
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> HUB=<env> TAG={{TAG}}`; EC2: `make -C deploy/providers/AWS upgrade-trust-ec2 KIT=<CODE> HUB=<env> TAG={{TAG}}`, both from a checkout at {{TAG}}. Runbook: *docs → System administrators → Upgrading a site*.

## :bug: Bug Fixes

- Security: certifi 2026.10.19 (GHSA-6q48-6c7w-9gw2) in every Python service and FL image; uv 0.13.4
  (GHSA-hq85-5cpj-mr62) in flip-utils and the Flower FL API; Pillow 13.0.0 (nine advisories) in flip-utils; vite
  8.1.6 and follow-redirects 1.16.1 in flip-ui (#1612).

## :white_check_mark: Release checks

<!-- These checks are release evidence and must be run for v0.13.1. Do not carry forward results
     from an earlier release. Record the host, date and outcome before merging the release PR. -->

Run at `99e6d3b35` (this PR's head before these notes; the notes are the only later change) on an RTX 5090
workstation, 21 October 2026.

Tutorial suite, on a GPU host:

- [x] FLARE — `make -C fl-tutorials run-all-tutorials`: 8/8 passed in 748 s
- [x] Flower — `make -C fl-tutorials run-all-tutorials FL_BACKEND=flower`: 5/5 passed in 261 s
- [x] Host, date, and results recorded: above

Full-platform smoke test, against a running deployment:

- [x] FLARE — `make e2e_smoke`: passed in 320 s, `RESULTS_UPLOADED`. It ran on the project created by the first FLARE
  attempt (cohort query, trust approval, 300/300 images pulled at both trusts). That attempt timed out at `INITIATED`
  because the first `make up` started flip-api with a stale internal service key hash, so the hub rejected every
  fl-server update (#1501, a dev bring-up bug that predates this release). `make restart-fl` recreated flip-api with
  the corrected hash, and both smokes then passed
- [x] Flower — `make e2e_smoke FL_BACKEND=flower`: passed in 495 s on the same project, `RESULTS_UPLOADED`
- [x] Host, date, deployment, and results recorded: a fresh isolated instance (`FLIP_INSTANCE=rel0131`, its own ports
  and env file, GSTT + KCH trusts) brought up from empty volumes, with every repo-built image built from this commit
  at `:rel0131` (hub and trust APIs, flip-ui, orthanc, omop-db, XNAT, both FL backends); none pulled from `:stag`/`:dev`.
  FL clients trained on CPU

Local checks:

- [x] `make unit_test`: all suites passed (flip-api 2220, flip-ui 1600, data-access-api 464, imaging-api 804,
  omop-db 115, trust-api 174, xnat 511)
- [x] `make integration_test`: flip-api 237, data-access-api 14, trust-api 8 passed

## :file_folder: PRs merged in this release

<!-- auto-populated by the release workflow -->

## :star: Acknowledgements

A big thank you to the following contributors for their work on this release:

<!-- auto-populated by the release workflow -->
