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

# trust/deploy/scripts/

The trust kit lifecycle, site upgrade and node self-test scripts (moved here from the repo-root
`scripts/` by FLIP#1438). Run them through their `make` targets in the root and `trust/` Makefiles,
not directly; each entry below names its target. Their stdlib-only tests live in
[`tests/`](tests/) and run in CI from
[`.github/workflows/test_trust_kit_scripts.yml`](../../../.github/workflows/test_trust_kit_scripts.yml).

## Trust kit scripts

These scaffold, write, and keep in sync the `trust/.env.<CODE>.<env>` kit files that carry a
trust's identity, credentials, and Hub-shared configuration. Every write goes through
`trust_kit_lib.py` (below), so operator edits to a kit file (host-local ports, bind dirs) survive
a re-run.

- **`new_trust.py`** (`make new-trust TRUST_CODE=<CODE> TRUST_NAME="..." HUB=<stag|prod>`; leave
  `HUB` unset for development) — scaffolds `trust/.env.<CODE>.<env>` from the base template
  (`trust/.env.example`), prepending the trust's identity (`TRUST_NAME` / `TRUST_CODE` /
  `TRUST_REGION`). Refuses to overwrite an existing kit. The root `new-trust` target also refuses when that trust still has a kit under its old, pre-FLIP#1479 file name. The result is ready for
  registration to fill in credentials (`make -C deploy/aws register-trusts KIT=<CODE>
  HUB=<hub>` on a deployed hub, `make register-trust KIT=<CODE>` in development).
- **`trust_checks.py`** — the one result type (`Check`: pass, fail, warn, skipped, pending, or
  could not run) and the one JSON + markdown report writer behind every check of a trust node:
  the onboarding checklist here and the Kubernetes self-test (`trust/deploy/helm/scripts/selftest_k8s.py`).
  A run exits 0 when ready, 1 when a check failed or is pending, 2 when nothing failed but a
  check could not run.
- **`trust_endpoints.py`** — reader of [`../endpoints.toml`](../endpoints.toml), the one list of
  what a trust node connects out to: host, port, when it is needed and on which platform. The
  onboarding checklist probes the registry hosts from it, and `tests/test_trust_endpoints.py`
  fails when the network tables in `docs/source/deploy-flip/` name a host the file does not, or
  the other way round. Add a destination to the file and the docs together.
- **`trust_kit_lib.py`** — the single kit-file writer behind `distribute_trust_kits.py`,
  `sync_trust_kit.py`, and the AWS registration path. Merges a kit dict into a kit file while
  preserving operator edits: credentials (`TRUST_API_KEY` / `TRUST_INTERNAL_SERVICE_KEY`) are
  written only on a new registration and never clobbered on an idempotent re-run; metadata
  (`EXPECTED_TRUST_ID` / `FL_KIT_SLOT` / `FL_KIT_SLOT_NUMBER`) and the Hub-shared block are
  upserted unconditionally, the latter under a sentinel header added once on first write. Writes
  every kit file `0600`.
- **`distribute_trust_kits.py`** — writes a trust kit file from `register_trust`'s JSON output. It reads one kit (a JSON object or a one-element array) on stdin and writes it to `--target PATH` (required). Callers:
  - the dev `register-trust` Makefile pipe (`--target trust/.env.<CODE>.<env>`);
  - `deploy/aws/scripts/register-trusts.sh`, for stag/prod.
- **`sync_trust_kit.py`** (`make sync-trust-kit KIT=<CODE> HUB=<env>`) — refreshes only the
  Hub-shared block in `trust/.env.<CODE>.<env>` from the caller's environment (the root Makefile
  `include`s the right `.env.<env>` and exports it first). Credentials and operator edits are left
  untouched. Portable across dev/stag/prod — no docker compose exec, no ECS round-trip.
- **`install_kit.py`** (`make -C trust install-kit FILE=<path> [HUB=stag] [KIT=<CODE>] [DELETE=1]`) — installs
  a kit downloaded from the FLIP UI on a Docker Compose trust host. Decrypts it with `openssl`
  (the passphrase is prompted for or read from stdin, never an argument), checks every file
  against the kit's own checksums, then creates the kit file as `new_trust.py` does (also minting
  the XNAT credentials) or merges into the existing one through `write_kit`, and stages the FL
  files under the kit file's `FL_KIT_DIR`, owned by the FL client's uid. An update kit leaves the
  trust's keys alone; on an installed trust `DOCKER_TAG` / `DOCKER_FL_TAG` are left to the upgrade.
- **`onboard_onprem_trust.py`** (`make onboard-onprem-trust KIT=<CODE>`) — a readiness checklist
  for an on-prem trust host, run as a precheck by `up-onprem-trust`. It also writes the checklist
  as a report under `CHECKS_OUT` (default `build/checks/` at the repo root). Diagnoses what's missing
  before `make up-onprem-trust` will succeed: public IP, docker swarm state, kit file presence,
  Hub-shared block, kit credentials, `FL_KIT_DIR` contents, OMOP/Orthanc data dirs, GPU
  capacity (`NUM_AVAILABLE_GPUS` vs. what the host actually exposes), and — probed outbound from
  the operator's account (FLIP#1726) — the hub API's `/health`, a TCP connect to the FL endpoint
  the client dials, and the image registries. Each check renders ✅ / ❌ /
  ⏳ (pending on an earlier check) and exits non-zero if anything fails, so an operator gets
  concrete diagnostics instead of a cryptic compose or pydantic failure deeper in the stack.

## Site upgrades

- **`site_upgrade.py`** — the plan phase of a site release upgrade (FLIP#1204): it picks the target
  tag, runs the guards, and pins the tag in the kit. It is invoked by `make -C trust upgrade-trust` and
  by the EC2 and Helm equivalents (see
  [`AGENTS.md` "Site release upgrades"](../../../AGENTS.md#site-release-upgrades-flip1204)).
  - **Target tag**: defaults to what the hub reports on `/api/health`; `--tag vX.Y.Z` or
    `--tag sha-<short7>` overrides it. The script says when GitHub lists a newer release, and offers to
    stop and move to it only when the hub already runs it.
  - **Guards**:
    - image tag shape;
    - downgrade (override with `--force`);
    - release-tag checkout match (override with `--allow-checkout-drift`);
    - every image actually built at the tag (`docker manifest inspect`);
    - operator confirmation (skip with `--yes`).
  - **Pin**: if everything passes, it rewrites `DOCKER_TAG` / `DOCKER_FL_TAG` in the kit's Hub-shared
    block.
  - **Apply**: the script **stops before any container is touched**. The Makefile then re-includes the
    updated kit and does the pull / recreate: `_upgrade-trust-apply` on trust, and
    `make -C trust/deploy/terraform/aws/ec2 upgrade` and `make -C trust/deploy/helm upgrade-trust-k8s`
    for the other shapes. Running the script directly rewrites the kit but leaves the containers at the
    old tag, so use the Make targets.
  - **Operator entry point**: `make upgrade-onprem-trust KIT=<CODE>` wraps both phases. Before the plan
    phase it also runs `onboard-onprem-trust --gate` (the readiness checklist implemented by
    `onboard_onprem_trust.py`, above).
  - **Exit codes** (the Makefile's contract): `0` pinned, `2` no usable target, `3` refused downgrade,
    `4` not confirmed, `5` an image is missing at the target tag, `6` the checkout is not at the target
    release.
  - Runbook: [`admin-upgrading-sites.rst`](../../../docs/source/sys-admin/admin-upgrading-sites.rst).

## Node self-test

- **`node_checks.py`** — the checks of a running trust node, written once for every platform
  (FLIP#1449): services ready, FL kit delivered, the three APIs healthy, OMOP and Orthanc hold
  data, Orthanc requires auth, a C-STORE reaches XNAT, dcm2niix converts it, the FL client stays
  up, the hub heartbeat, and the two egress rows (FLIP#2077): the hub API and FL server answer
  from the containers that use them, and a sample of destinations no trust needs does not (one
  that answers is a warning for the site's IT team, never a failure). The last row is drift
  (FLIP#2078): what differs between the running node and what it was deployed from, found by
  each platform's own comparison (`kubectl diff` of the Helm manifest; `docker compose config
  --hash` and image IDs). Each asks its question from inside a container the node already runs,
  through a small interface (`Node`). `ROWS` is the list both platforms report, held by a test on
  each side.
- **`check_node.py`** (`make -C trust check-node KIT=<CODE>`, or `make check-onprem-trust
  KIT=<CODE>` from the repo root) — the Compose answer to that interface, through `docker`. It
  checks the stack that is running and starts nothing; `up-onprem-trust` and
  `upgrade-onprem-trust` end with it, and its result never changes theirs. On a kit with
  `TRUST_DATA_SEED=false` it sends no study. The Kubernetes answer is
  `trust/deploy/helm/scripts/selftest_k8s.py`.
- **`selftest_node.sh`** (`make -C trust selftest-node FL_BACKEND=<backend>`) — the trust-node
  self-test. It runs on the node itself, needs no hub ("hubless"): it brings a throwaway stack up
  on a generated kit, runs `check_node.py` against it, and tears it down. JSON + markdown report
  (FLIP#1390, FLIP#1406).
- **`selftest_kit.py`** — called by `selftest_node.sh` to write a hubless kit file for the
  self-test: a hub URL under `.invalid`, fresh random keys, slot `Trust_1`.
