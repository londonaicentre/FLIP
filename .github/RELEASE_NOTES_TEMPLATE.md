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

- **A trust inside a TRE** (#1732, #1724). An on-prem trust installs and runs without sudo, behind a proxy, as the operator's own uid (`FLIP_UID`/`FLIP_GID`), and pulls every image, third-party ones included, from one registry: GHCR, with Grafana, Loki, Alloy and the socket proxy mirrored there and pinned by digest. This is what the KCL CREATE TRE runs on.
- **AWS trusts in their own account** (#1632, #1636, #1717; issues #1438, #1439). A trust deploys on AWS from its own Terraform root, as one EC2 node (an existing subnet by default, no trust-owned Elastic IP, SSM only) or on EKS through the `flip-trust` chart. The hub's Terraform no longer creates a trust.
- **Flower 1.42 behind a Fleet-only proxy** (#1531; issue #1517). Trusts reach the hub's SuperLink only through a new proxy that passes the Fleet API alone, so Flower's unauthenticated Control and Runtime APIs are never exposed to a trust. The Flower images now install flip-utils from its lock file, hash-checked, as the NVFLARE ones do (#1802).
- **Hardening from the security review** (#1744, #1803, #1804, #1805; tracker #1743). Kits get minted passwords instead of template ones, trust and hub ports bind to loopback by default, the hub's containers run on a read-only root filesystem, its AWS resources pass Security Hub's FSBP baseline, and its web listener is on TLS. Two published advisories are fixed (GHSA-ghjq, GHSA-9fx9).
- **A real trust needs no sample data** (#1689, #1686). A new kit setting, `TRUST_DATA_SEED=false`, makes a first install fetch nothing from Hugging Face or PyPI; one outbound-traffic table now covers every node shape.

## :warning: Breaking Changes

- **Flower hub and Flower sites move to 1.42 together** (#1531). SuperNodes older than 1.40 cannot connect to the new proxy, so a Flower estate upgrades in one Deployment-Mode window. Trust kits are unchanged. NVFLARE estates are not affected.
- **The hub's web listener is on TLS** (#1805). For hub operators: the edge's origin must be HTTPS before, or straight after, this applies. On a Landing Zone Accelerator estate that is the platform repo's origin change (aicentre-lza-iac #127 and #128 for staging).
- **The hub's Terraform creates no trust** (#1717). `deploy/providers/AWS` drops `module.trust_ec2` and the `deploy-trust`, `seed-trust-data`, `stage-fl-kit`, `upgrade-trust-ec2`, `forward-trust` and `check-trust-ec2-enabled` targets; an AWS trust deploys from `trust/deploy/terraform/aws/ec2` or `…/eks`. The apply removes the hub's unused trust security group, role, profile, log group and key pair; `DEPLOY_TRUST_EC2` is no longer read.
- **Trust deployment files moved under `trust/deploy/`** (#1629): composes in `trust/deploy/compose/`, kit and node scripts in `trust/deploy/scripts/`, Azure in `trust/deploy/terraform/azure/`. Make targets are unchanged. An existing Azure bootstrap's local state has to be moved (see the Azure README).
- **Compose ports bind to `127.0.0.1` by default** (#1744), except the FL server's port. Anyone who reached Grafana, Orthanc or a database by the host's LAN address uses an SSH tunnel, or sets `TRUST_BIND_ADDRESS=0.0.0.0` in the kit (`HUB_BIND_ADDRESS` in the hub env file).

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

- **Required:** recommended. The release carries the security review's hardening and dependency fixes (#1744, #1747, #1800). A Flower site must upgrade in the same window as its hub (see *Breaking Changes*).
- **Ordering:** NVFLARE sites, any order: NVFLARE and the hub–site protocol are unchanged. Flower sites, with the hub, in one Deployment-Mode window. Upgrading after the hub reports {{TAG}} lets the upgrade verb pick it by default; before that, pass `TAG={{TAG}}`.
- **Refreshed kit needed:** no. Before upgrading, check three kit settings:
  - A `.prod` / `.stag` kit that still carries a template password (`ORTHANC_PASSWORD`, `OMOP_POSTGRES_PASSWORD`, `DATA_ACCESS_POSTGRES_PASSWORD`, `XNAT_ADMIN_PASSWORD`, `XNAT_SERVICE_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`) now fails the readiness checklist, which names the keys to rotate (#1744).
  - Ports move to loopback on the upgrade: set `TRUST_BIND_ADDRESS=0.0.0.0` only on a host whose own firewall decides who gets in (#1744).
  - Optional: `TRUST_DATA_SEED=false` on a real trust, so it never loads the sample data (#1689); `FLIP_UID` / `FLIP_GID` to run the containers as the operator (#1732).
- **Outbound access:** every image now comes from GHCR (`ghcr.io`, `pkg-containers.githubusercontent.com`); Docker Hub is no longer needed (#1724).
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}        # the compose files and the verb come from the checkout, not the images
  make upgrade-onprem-trust KIT=<CODE>                   # defaults to the release the hub reports
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> HUB=<env> TAG={{TAG}}`; an EC2 node: `make -C trust/deploy/terraform/aws/ec2 upgrade KIT=<CODE> HUB=<env> TAG={{TAG}}`, both from a checkout at {{TAG}}. Runbook: *docs → System administrators → Upgrading a site*.

## :seedling: New Features

- On-prem trusts without sudo: operator access, proxy setup, containers as the operator's uid, and every image from one GHCR registry, mirrored and pinned by digest (#1732, #1724).
- AWS trust nodes in their own account: an EC2 root (existing subnet or its own VPC, no trust-owned Elastic IP) and an EKS root for the chart (#1632, #1636); the deployment tree split by component, with the hub's Terraform hub-only (#1629, #1717).
- Flower 1.42, with a Fleet-only proxy in front of the SuperLink (#1531).
- Trust keys rotate in place: `make rotate-trust-keys KIT=<CODE>` reissues a trust's API and internal service keys without changing its identity or kit slot (#1601).
- `TRUST_DATA_SEED=false` skips the sample data on every node shape (#1689). The readiness checklist probes the hub, the FL endpoint and the image registries from the trust host (#1727).
- Hub hardening: minted kit passwords and loopback ports (#1744), read-only root filesystems on ECS (#1803), Security Hub FSBP controls (#1804), the web listener on TLS (#1805).
- FLARE job processes start faster: the images ship their Python bytecode (#1783).
- MONAI bundle on-ramp in flip-utils: an overlay schema and a skeleton generator (#1389).
- A browse-only public demo at `/demo/` with five example projects (#1748, #1759). Staging stops after its end-to-end check and a hold window (#1621, #1728).

## :bug: Bug Fixes

- Flower tutorials learn again: the default differential-privacy clipping of 1.0 had stopped the spleen tutorial learning (#1758).
- One outbound-traffic table for every node, and the EC2 CloudWatch agent from the regional bucket (#1686).
- FL kit changes roll the hub's FL services before the Flower key registration, and a stale kit fails with a named fix (#1741). The Helm fl-client requests its GPU from the device plugin (#1767).
- Security: dependency upgrades for the open Dependabot alerts (#1747, #1800); the Flower images built from flip-utils' lock (#1802).
- `update-env` no longer writes `DB_HOST` (#1781); flip-ui typechecks in CI (#1772); the EC2 trust's ssh_config example names the right profile (#1730).

## :books: Documentation

- "Is your trust ready for FLIP?" for non-technical readers (#1735); what to ask a trust's network team about the FL port (#1794); trust platform pages with AKS and AWS (#1786).
- Tutorial READMEs: at-a-glance blocks, flow diagrams and six wrong commands fixed (#1693). Docs GIFs pinned by sha256 (#1776); weekly drift sweeps (#1734, #1799).

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
