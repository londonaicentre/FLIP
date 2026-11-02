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

- **Trusts on Azure** (#1406, #1420, #1421, #1489; issue #1390). A trust can run on an Azure VM that provisions itself, tests itself without a hub and then joins one, or on AKS through the `flip-trust` chart, a Terraform root and a runbook chapter.
- **MONAI Label in the OHIF viewer** (#945). Trust users can run MONAI Label models from XNAT's OHIF viewer, and export masks through a Container Service command.
- **Image-generation tutorials** (#1305; issue #1225). Brain-MRI latent diffusion as two ordinary jobs, an autoencoder and then a conditioned diffusion model, plus a chest X-ray diffusion tutorial. They replace the `diffusion_model` job type.
- **Kubernetes trusts are safer by default** (#1691, #1694, #1659). XNAT runs the plugins built into its image instead of downloading them at start, a first install changes XNAT's factory admin password to the trust's own, and an XNAT database created by an older chart now upgrades cleanly.
- **GPUs only while a job runs** (#1429; issue #1428). An opt-in chart mode runs each NVFLARE job in its own pod, so a Kubernetes trust holds a GPU only while training.

## :warning: Breaking Changes

- **`HUB=prod|stag` replaces `PROD`** (#1485). For hub operators: any `PROD=` stops make and names the `HUB=` to use. Hub env files are `.env.prod` / `.env.stag`, kits `trust/.env.<CODE>.prod` / `.stag`, and the deployed compose files `*.prod.yml`. `MANAGE_DNS` must be set in the hub env file. The Terraform workflows read `TF_HUB`. The self-contained single-account hub is removed: the Terraform root deploys onto a Landing Zone Accelerator estate only.
- **The `diffusion_model` job type is removed** (#1305). A stored model whose `config.json` names it fails like any unknown job type; use a `standard` job, as the new tutorials do. flip-utils drops `FlipDiffusionRecipe`.
- **`package-onprem-trust-kit` takes the trust's code** (#1486): `KIT=<CODE>`. The old `KIT=<slot> KIT_FILE=…` call stops.

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

- **Required:** recommended, promptly. The FL client images carry MONAI 1.6.1, which fixes a critical flaw where a crafted DICOM crashes MONAI Label or silently corrupts inference; the trust APIs carry the dnspython and msgpack security fixes (#1697).
- **Ordering:** any order: the FL frameworks and the hub–site protocol are unchanged. Upgrading after the hub reports {{TAG}} lets the upgrade verb pick it by default; before that, pass `TAG={{TAG}}`.
- **Refreshed kit needed:** no. A kit under an old name (`.env.<CODE>.production`, `.lza-prod`, or a bare `.env.<CODE>`) is still found for this release and the verb prints the rename; rename it to `trust/.env.<CODE>.prod` afterwards (#1485).
- **Kubernetes trusts:**
  - If your XNAT still uses the factory `admin` password, set your own `XNAT_ADMIN_PASSWORD` in the kit before upgrading and leave `XNAT_ADMIN_INITIAL_PASSWORD=admin`; the upgrade changes it (#1694).
  - If `XNAT_DATASOURCE_PASSWORD` and `XNAT_DATASOURCE_ADMIN_PASSWORD` were ever set to the same value, change one first, or xnat-db refuses to start (#1659).
  - The upgrade switches the fl-client to the `Recreate` strategy itself (#1429) and replaces `xnat-web` once (#1691).
  - From now on, a site that tracks a moving tag such as `stag` needs a `kubectl rollout restart` of the xnat-web Deployment to take a rebuilt XNAT image.
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}        # the compose files and the verb come from the checkout, not the images
  sudo -E make upgrade-onprem-trust KIT=<CODE>           # defaults to the release the hub reports
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> HUB=<env> TAG={{TAG}}`; an EC2 node: `make -C trust/deploy/terraform/aws/ec2 upgrade KIT=<CODE> HUB=<env> TAG={{TAG}}`, both from a checkout at {{TAG}}. Runbook: *docs → System administrators → Upgrading a site*.

## :seedling: New Features

- Azure trust nodes: a self-provisioning VM with a kit drop, a hubless self-test and the hub join, and AKS through the chart, a Terraform root and make targets (#1406, #1420, #1421, #1489).
- MONAI Label in XNAT's OHIF viewer, with a Container Service command to export masks (#945).
- Three image-generation tutorials in a new `image_generation` category, and the Flower tutorial tree in the NVFLARE tree's `<category>/<name>` layout (#1305, #1633). A shared tutorial template, with skills to create and audit tutorials (#1445).
- Kubernetes trusts: XNAT plugins from the image, first-install rotation of XNAT's admin password, and opt-in NVFLARE job pods that hold a GPU only while a job runs (#1691, #1694, #1429).
- The on-prem kit flow fits together: the packager takes the trust's code, AWS `register-trusts` mints the XNAT passwords, and `sync-trust-kit` replaces a hub `sha-` tag with its release (#1486).
- The hub's flip-ui is published after every Terraform apply, under a bucket-only role (#1619). The production RDS states its storage encryption explicitly, with a guard (#1597). A deterministic release pipeline: `make release-*` (#1610).

## :bug: Bug Fixes

- An administrator's password reset now reaches a user who never completed their first sign-in; before, Cognito sent nothing and the UI reported success (#1705).
- Security: MONAI 1.6.1, dnspython 2.9.0, msgpack 1.2.3 and qs 6.16.1, with floors so an unlocked install cannot take the vulnerable versions (#1697); plus earlier dependency fixes (#1650, #1670, #1674).
- Kubernetes: xnat-db's postStart works on a database created before the role split, and keeps passwords out of the Postgres log (#1659). `sync_k8s_kit.py` brings its own PyYAML (#1624).
- The FL images turn off torch 2.13's native Triton kernels, which crashed on GPU because the images have no C compiler (#1701).
- FL: researcher-safe refusals from the FL API, direct metric reporting no longer fatal, exact bundle contents checked across S3 pages, signed bundle URLs redacted from diagnostics (#1351, #1354, #1663, #1664). DQR studies without descriptive tags are accepted (#1660).
- A first `make up` no longer starts flip-api with a stale internal service key hash, so training reports back (#1625). The dev Flower SuperLink no longer rewrites the host's SSO token as root (#1620). The login page no longer logs pre-auth 401s, and the UI's Lighthouse and accessibility findings are fixed (#1631, #1594). `check-fl-provisioned.sh` works on macOS (#1627).

## :white_check_mark: Release checks

<!-- The gates that cannot run in CI: no GitHub-hosted runner has a GPU, and both the suite and the smoke test need real hardware and a full stack. Written by `make release-record` from `make release-gates` results — see *Pre-release checklist* in CONTRIBUTING.md. -->

Tutorial suite, on a GPU host:

- [x] FLARE — `make -C fl-tutorials run-all-tutorials`
- [x] Flower — `make -C fl-tutorials run-all-tutorials FL_BACKEND=flower`
- [x] Host and date recorded: NVIDIA GeForce RTX 5090 workstation, 29 October 2026

FLARE: all 10 tutorials passed in 24 min 8 s — arkplus_fine_tuning 1 min 39 s, xray_classification 41 s, 3d_spleen_segmentation_evaluation 1 min 4 s, arkplus_baseline_classification_evaluation 31 s, arkplus_multimodel_classification_evaluation 46 s, brain_mri_ldm_stage1_autoencoder 6 min 59 s, brain_mri_ldm_stage2_diffusion 2 min 35 s, xray_diffusion 3 min 31 s, 3d_spleen_segmentation 5 min 50 s, ehr_risk_prediction 31 s.
Flower: all 5 tutorials passed in 4 min 26 s — xray_classification 30 s, 3d_spleen_segmentation_evaluation 52 s, 3d_prostate_segmentation 43 s, 3d_spleen_segmentation 2 min 1 s, ehr_risk_prediction 21 s.

Full-platform smoke test, against a running deployment:

- [x] FLARE — `make e2e_smoke`
- [x] Flower — `make e2e_smoke FL_BACKEND=flower`

Run at `03b5a5bbd` on an isolated instance (`rel0140`) built from that commit — every repo-built image at `:rel0140`, checked by `make release-verify` — started from empty state: FLARE reached RESULTS_UPLOADED in 9 min 27 s; Flower reached RESULTS_UPLOADED in 4 min 0 s.

At the same commit: `make unit_test` passed, `make integration_test` passed.

## :file_folder: PRs merged in this release

<!-- auto-populated by the release workflow -->

## :star: Acknowledgements

A big thank you to the following contributors for their work on this release:

<!-- auto-populated by the release workflow -->
