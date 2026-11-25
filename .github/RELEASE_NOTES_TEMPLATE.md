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

- **The trust Helm chart is on GHCR** (#1752). v0.16.0's release stopped at its first chart publish, so this hotfix carries the fix and publishes `oci://ghcr.io/londonaicentre/charts/flip-trust:0.16.1`. Nothing else changes: every image is built from the same code as v0.16.0. For everything that v0.16 brings, read the [v0.16.0 release notes](https://github.com/londonaicentre/FLIP/releases/tag/v0.16.0).

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

- **Required:** a site on v0.15 or older upgrades straight to {{TAG}}, following the [v0.16.0 Site upgrade](https://github.com/londonaicentre/FLIP/releases/tag/v0.16.0) section; it applies unchanged. A site already on v0.16.0 need not upgrade.
- **Refreshed kit needed:** no.
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}
  make upgrade-onprem-trust KIT=<CODE>
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> HUB=<env> TAG={{TAG}}`, from a checkout at {{TAG}}.

## :bug: Bug Fixes

- The release publishes the trust Helm chart under the name in its `Chart.yaml` (`flip-trust`), not its folder's (`helm`): v0.16.0's job pushed a `helm-0.16.0.tgz` that `helm package` never wrote, and stopped before its release notes, its production apply and its GitHub Release (both finished by hand).

## :white_check_mark: Release checks

<!-- The gates that cannot run in CI: no GitHub-hosted runner has a GPU, and both the suite and the smoke test need real hardware and a full stack. Written by `make release-record` from `make release-gates` results — see *Pre-release checklist* in CONTRIBUTING.md. -->

Tutorial suite, on a GPU host:

- [ ] FLARE — `make -C fl-tutorials run-all-tutorials`
- [ ] Flower — `make -C fl-tutorials run-all-tutorials FL_BACKEND=flower`
- [ ] Host and date recorded: —, —

Full-platform smoke test, against a running deployment:

- [ ] FLARE — `make e2e_smoke`
- [ ] Flower — `make e2e_smoke FL_BACKEND=flower`

Not rerun for this hotfix: {{TAG}} changes only how the release workflow packages the Helm chart, and its images are built from the same code as v0.16.0, whose every gate passed at `047b0e545` (see its *Release checks*). The fix itself: `trust/deploy/helm/tests/test_package_chart.py` (27 passed, the new name test failing against v0.16.0's script), and a real `helm package` through the script writing `flip-trust-0.16.1.tgz`.

## :file_folder: PRs merged in this release

<!-- auto-populated by the release workflow -->

## :star: Acknowledgements

A big thank you to the following contributors for their work on this release:

<!-- auto-populated by the release workflow -->
