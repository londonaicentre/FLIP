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

- **The Ark+ chest X-ray cohorts are sample data** (#2087). The two synthetic cohorts the Ark+ tutorials and the preprint use are published as `arkplus_project` in trust-data `20261125`, which this release pins: `make -C trust seed KIT=<CODE> PROJECTS=arkplus_project` loads them like any other project. Each partition is one site, `site1` (UK cohort) and `site2` (Thai cohort), made by different generators, so unlike the other projects the two halves deliberately differ. The three Ark+ cohort queries select the training or the evaluation rows.
- **FL on port 443 for new trusts** (#2095). Production's FL edge also listens on 443. Kits now follow the hub's FL port (`FL_EDGE_PORT`), and a trust that must keep another port sets `FL_EDGE_PORT_OVERRIDE` in its own kit file, which no kit refresh overwrites. Nothing moves until the hub changes its port; existing trusts keep 8002 until then.
- **The EHR tutorial's data loads on any trust** (#2108). `make -C trust load-synthea-ehr KIT=<CODE>` now uses the kit's OMOP login and port and the trust's half, so it works on a site whose trust-local passwords were changed.

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

- **Required:** no, for a site on v0.16.0 or v0.16.1, unless it wants the Ark+ cohorts or the EHR data below. A site on v0.15 or older upgrades straight to {{TAG}}, following the [v0.16.0 Site upgrade](https://github.com/londonaicentre/FLIP/releases/tag/v0.16.0) section; it applies unchanged.
- **Refreshed kit needed:** no.
- **What the upgrade changes on the node:** NVFLARE's `fl-client-init` sets the kit's server port from `FL_EDGE_PORT` (no change while the hub keeps 8002); Grafana and XNAT's JavaMelody no longer call home (#2089).
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}
  make upgrade-onprem-trust KIT=<CODE>
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> HUB=<env> TAG={{TAG}}`, from a checkout at {{TAG}}.
- **New sample data, after the upgrade (optional):**
  - Ark+: `make -C trust seed KIT=<CODE> PROJECTS=arkplus_project` (4.3 GB download). A trust that holds these cohorts from the earlier unmerged loader first runs `make -C trust remove-stray-studies KIT=<CODE> PROJECTS=arkplus_project DRY_RUN=1`, then without `DRY_RUN`, or every study would be in OMOP twice.
  - EHR: `make -C trust load-synthea-ehr KIT=<CODE>`, after any seed (a `CLEAN=all` seed removes its persons).

## :bug: Bug Fixes

- A CSV's all-digit `accession_id` keeps its leading zeros when the canonical tables are built and imported; read as numbers, 476 of the 4,728 Ark+ studies would never have matched their DICOM (#2087).
- `load-synthea-ehr` with a kit no longer logs in with the dev template's credentials, loads the trust's own half instead of half 1 everywhere, and refuses a trust whose kit turns the sample data off (#2108).
- The FL API checks a job credential's file name before it removes the earlier credentials, so a refused name leaves them in place (#2033).
- Each checkout's Flower simulator keeps its own SuperLink state, so a SuperLink another checkout left running can no longer run its jobs (#2070).

## :book: Documentation

- The trust node's outbound table is measured row by row (#2089).

## :white_check_mark: Release checks

<!-- The gates that cannot run in CI: no GitHub-hosted runner has a GPU, and both the suite and the smoke test need real hardware and a full stack. Written by `make release-record` from `make release-gates` results — see *Pre-release checklist* in CONTRIBUTING.md. -->

Tutorial suite, on a GPU host:

- [ ] FLARE — `make -C fl-tutorials run-all-tutorials`
- [ ] Flower — `make -C fl-tutorials run-all-tutorials FL_BACKEND=flower`
- [ ] Host and date recorded: —, —

Full-platform smoke test, against a running deployment:

- [ ] FLARE — `make e2e_smoke`
- [ ] Flower — `make e2e_smoke FL_BACKEND=flower`

## :file_folder: PRs merged in this release

<!-- auto-populated by the release workflow -->

## :star: Acknowledgements

A big thank you to the following contributors for their work on this release:

<!-- auto-populated by the release workflow -->
