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

- **Security fixes reach production** (#1874, #1879; GHSA-cc6x-7f92-8g7v, GHSA-mmmg-jvf9-v9qp). The FL server no longer holds a hub-wide key: each FL job carries its own credential, valid only for its own model while it reports, and the hub's shared internal service key is retired. The FL API requires a key on every route but `/health`. Further fixes from the November security review are in this release too; their advisories follow.
- **XNAT upgrade** (#1819–#1823, #1882). PostgreSQL 12 → 16, Tomcat 9 on JDK 21, DQR 3.1.0 and Container Service 3.9.0, upgraded in place on Compose and Helm with a dump and restore that keeps the old cluster for a rollback. This is the main thing a site does on upgrade.
- **FL jobs as Kubernetes job pods** (#1750, #1751, #1742, #1856, #1858, #1755, #1672, #2001). Each NVFLARE job runs in its own pod with reserved CPU and memory and is removed when it ends. A job pod sees only its own project's studies, read-only, through the project's NVFLARE study, and can read them from XNAT's archive in place. A job can be submitted while a trust is offline.
- **Imaging follows the approved cohort** (#1861). A study that leaves a project's approved cohort stops being served at once and leaves XNAT after a grace period (24 hours by default). imaging-api refuses downloads outside the approved list.
- **More places to run a trust** (#1944, #1785, #1959, #1752). A FLIP node in Snowflake (Snowpark Container Services), an EC2 trust inside the hub's own account, and single-node k3s as a supported cluster. Each release publishes the trust Helm chart to GHCR as a versioned chart.
- **Easier for Trust Admins** (#1853, #2010, #1838, #1998, #2014, #2040). A Trust Admin downloads their trust kit from the UI. A trust's keys rotate without taking it offline: the hub accepts the old and the new key until the trust first uses the new one. Connection Status shows each trust's job mode and FL client version. A hub admin can delete a disabled user. Upgrading an on-prem site from an old release no longer hits the problems BDMS met going from v0.8.0 to v0.15.0, and a Helm upgrade no longer reports success with the FL client down.
- **Framework and platform updates** (#1994, #2002, #1961, #1917, #1859, #2032). Python 3.13 and torch 2.15 across the platform; NVFLARE 2.9.1 (mixes with 2.9.0, so sites upgrade in any order) and Flower 1.43. Hub emails go through SES from the hub's own domain. The hub's deploy files are grouped by component. A causal discovery tutorial on real ADNI data (#1484) and a more accessible UI (#1929, #1933, #1936).

## :warning: Breaking Changes

- **The FL API requires a key** (GHSA-mmmg-jvf9-v9qp). flip-api sends `FL_API_KEY` to every FL API; an old flip-api against a new FL API gets 401 on every FL call. On the hub, the images and the Terraform apply go out together, in one quiet FL window. A local stack gets the key from `make up`; a stack started by hand needs `make generate-fl-api-key` first.
- **The hub's shared internal service key is retired** (#1874). fl-server callbacks authenticate with the per-job credential alone. Hub operators: `INTERNAL_SERVICE_KEY`, `INTERNAL_SERVICE_KEY_HASH` and `INTERNAL_SERVICE_KEY_HEADER` are no longer read.
- **Hub deploy tree by component** (#2032, #2035). `deploy/providers/AWS` is now `deploy/aws` and the hub compose files are in `deploy/compose/`. `make -C deploy/providers/AWS …` still forwards for this one release. Hub operators: move the gitignored `build/` (packaged trust kits) and `.backups/` across and re-run `terraform init` in `deploy/aws` (the state key is unchanged). A raw `docker compose` against the hub files needs `--project-directory deploy`. `site.yml` is now `bastion.yml`.
- **Flower hub and Flower sites move to 1.43 together** (#1917). The SuperLink's health check is HTTP; its gRPC health server is gone. A Flower estate upgrades in one Deployment-Mode window.
- **Flower kits are re-issued** (#2044). Python 3.13 rejects the certificates the old `generate_creds.py` issued (no key identifiers), so a hub on this release starts no Flower run until its Flower kits are re-issued and sent to the sites. NVFLARE kits are not affected.
- **flip-utils backends are extras** (#1905). A plain `pip install flip-utils` no longer installs an FL backend: name `flip-utils[nvflare]` or `flip-utils[flower]`. `flip.utils.Utils.is_string_empty` is removed (#2038).

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

- **Required:** yes. The release carries the security fixes above, the XNAT database upgrade and the cohort controls (#1861, #1886).
- **Ordering:**
  - NVFLARE sites: any order. A v0.15.0 NVFLARE trust keeps working against a {{TAG}} hub, and NVFLARE 2.9.0 and 2.9.1 mix.
  - Kubernetes trusts that run NVFLARE jobs as pods: upgrade **before** the hub, so the site has its project studies when the hub starts submitting jobs into them (#1856).
  - Flower sites: with the hub, in one Deployment-Mode window, on a re-issued Flower kit (*Breaking Changes*).
  - Upgrading after the hub reports {{TAG}} lets the upgrade verb pick it by default; before that, pass `TAG={{TAG}}`.
- **Refreshed kit needed:** not for the upgrade itself (Flower aside). After the cut the hub's FLARE CA is rotated (GHSA-cc6x-7f92-8g7v), which cuts every NVFLARE site off until it installs the new FL kit its hub admin sends.
- **Before upgrading, check the kit:**
  - The readiness checklist now refuses `TRUST_LOG_LEVEL=DEBUG`. Set `INFO`, recreate the trust APIs, and rotate both trust keys (`make rotate-trust-keys KIT=<CODE>` on the hub) if the trust ever ran at `DEBUG`. It still refuses a template password, as in v0.15.0.
  - A site with a real PACS whose kit never set XNAT's AE title is renamed to `FLIP_XNAT` on upgrade (#1863). Set the title the PACS sends to in the kit first.
  - `COHORT_QUERY_THRESHOLD` below 10 is raised to 10 (#1886).
  - New, optional: `COHORT_RECONCILE_MINUTES` and `COHORT_REMOVAL_GRACE_HOURS` (default 24) set how fast imaging follows a cohort change (#1861). A long OMOP reload can make studies look removed: raise the grace period before one.
- **What the upgrade does:**
  - XNAT's database moves from PostgreSQL 12 to 16 by a dump and restore, and the old cluster is kept for a rollback. Once the site is confirmed: `make -C trust/xnat xnat-db-remove-old-cluster KIT=<CODE> HUB=<env> YES=1` (Helm: `make -C trust/deploy/helm xnat-db-remove-old-cluster YES=1`).
  - NVFLARE: the fl-client mounts its kit's `local/` and `startup/` read-only through a new `fl-client-init` step (#1906), so the FL client image must be this release's too.
- **Kubernetes sites:**
  - The OMOP vocabulary is staged onto the node, never fetched in the cluster (#1902): `make -C trust/deploy/helm download-vocab` or an Athena export, then `stage-vocab`.
  - A trust that runs NVFLARE jobs as pods: after **every** project approval, run `make -C trust/deploy/helm sync-project-studies KIT=<CODE> HUB=<env>`. Without it, every site aborts that project's jobs.
  - An account limited to the trust's namespace (#1986): the built-in `admin` role is not enough. `make preflight` reports `Cluster unreachable` on a healthy cluster, and a first deploy stops at `resourcequotas … is forbidden`. Have a cluster administrator create the namespace and grant the three extra rights listed under "Deploying with an account limited to one namespace" on the Kubernetes troubleshooting page: write ResourceQuotas and LimitRanges in the namespace, read Services in `kube-system`, read nodes and StorageClasses. A trust already running with those rights is not affected.
- **Snowflake trusts:** pass `HUB_EGRESS=true` on every `make up` and `make render`, not only at join. Without it the services lose their hub and FL access.
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}        # the compose files and the verb come from the checkout, not the images
  make upgrade-onprem-trust KIT=<CODE>                   # defaults to the release the hub reports
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> HUB=<env> TAG={{TAG}}`; an EC2 node: `make -C trust/deploy/terraform/aws/ec2 upgrade KIT=<CODE> HUB=<env> TAG={{TAG}}`, both from a checkout at {{TAG}}. Runbook: *docs → System administrators → Upgrading a site*.

## :seedling: New Features

- FL jobs on Kubernetes: job pods with reserved resources, removed when they end (#1750); submit despite an offline trust (#1751); studies streamed into the job's own directory (#1742); job pods see only their project's studies (#1856) and read them from XNAT's archive (#1858); the trust reports its job mode (#1755); the image cache drops studies unused for a week (#1672).
- A project's imaging follows its approved cohort (#1861).
- XNAT: PostgreSQL 16 (#1819), Tomcat 9 / JDK 21 (#1820), DQR 3.1.0 (#1822), Container Service 3.9.0 (#1823), nginx 1.30.6 and xnat-db tuning on Helm, Europe/London time zone (#1882), AE title `FLIP_XNAT` by default (#1863).
- Trust kits: download from the UI (#1853); staged key rotation with a grace period (#2010); NVFLARE kit mounted read-only (#1906); kit slots derived from S3 on both backends (#1722, #1943).
- New node shapes: Snowflake (#1944), an EC2 trust in the hub's account (#1785), k3s (#1959); the trust Helm chart on GHCR on every release (#1752); the OMOP vocabulary from a folder on the node (#1902).
- UI: Connection Status shows each trust's job mode and FL client version, and hub admins its jobs (#1838), with a pulse on every online trust (#1981); delete a disabled user (#1998); simpler typography, project type icons and a graphite dark mode (#1936); the public demo served from its own host, checked in CI (#2013).
- Hub: email through SES from the hub's own domain (#1859); the RDS Proxy log group in Terraform with retention (#1884); a develop apply catches up to the newest build (#1952); the Helm upgrade's exit code is deterministic (#1719); onboarding asks for the site's egress address when the FL endpoint times out (#1832).
- Platform: Python 3.13 (#1994), torch 2.15 on CUDA 13.2 (#2002), NVFLARE 2.9.1 (#1961), Flower 1.43 (#1917), flip-utils backend extras (#1905).
- Tutorials: biomarker causal discovery, federated bootstrapped FCI on ADNI, on both backends (#1484).

## :bug: Bug Fixes

- Trust upgrades: a Helm upgrade keeps the FL kit path and waits for a serving FL client, and the on-prem readiness gate passes a running site (#2040); the on-prem upgrade gaps a v0.8.0 site hit (#2014); on-prem kit tarballs ship private (#1965).
- Flower certificates pass Python 3.13's strict TLS check (#2044). A run picked up while the FL API restarts waits in the queue (#1963). The start-of-job image wipe keeps the job's own project (#1830, #2001).
- Cohort queries: the threshold is floored at 10 (#1886); the cache keys on the exact validated SQL (#1883); `make_date` is allowed (#1909).
- XNAT: Compose persists the prearchive (#1821); a failed admin ContainerManager grant is judged by the roles admin holds (#1955).
- Helm: `make status` runs its smoke tests against `KUBE_CONTEXT` (#1808); no stale `fl-server-net-1` host alias (#1922); imaging-api's Recreate strategy (#1951).
- Training: the NVFLARE EHR tutorial restarts its learning-rate decay every round (#1835); the NVFLARE results zip carries the whole app (#1907); Flower evaluation metrics plot per round (#1903); internal training errors stay out of the activity feed (#1662).
- UI: WCAG 2.2 AA contrast and focus fixes (#1929, #1933); a staging call with no trusts no longer blocks later staging (#1928); untouched fields show no validation error (#1939, #1855); the demo's Models list honours its filters (#1935).
- Hub and trust tooling: SES template permissions (#1841); EFS provisioning retries while its new role propagates (#1879); `register-trust` / `register-trusts` on a deployed hub (#1844, #1904); `up-trust-ec2` creates its slot network (#1846); FL client lists from the compose config (#1885); Flower composes require the host group (#1665); the trust Makefiles' throwaway containers pull from GHCR (#2036).
- Security: open Dependabot alerts resolved (#1868, #1979); unused Cognito actions dropped from the flip-api role (#1887); the dev fl-api debug port on loopback (#1876); redundant checks and silent fallbacks removed (#2038).

## :books: Documentation

- Troubleshooting entries for trust node problems met on upgrade and install (#1983); SES's 72-hour verification window and an end-to-end email check (#1956); the FL connection is WebSocket over HTTPS, with mutual TLS only on NVFLARE (#2058).
- Docs tidied across the hub, FL, trust and Read the Docs pages (#1873, #1916, #1918–#1921, #2039); current ADNI download steps (#2004).

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
