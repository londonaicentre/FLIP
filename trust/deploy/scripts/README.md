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

The trust kit lifecycle and node self-test scripts (moved here from the repo-root `scripts/` by
FLIP#1438). They are invoked through `make` targets — the root and `trust/` Makefiles — rather
than run directly; see each entry for the wrapping target. Their stdlib-only tests live in
[`tests/`](tests/) and run in CI from
[`.github/workflows/test_trust_kit_scripts.yml`](../../../.github/workflows/test_trust_kit_scripts.yml).

## Trust kit scripts

These scaffold, write, and keep in sync the `trust/.env.<CODE>.<env>` kit files that carry a
trust's identity, credentials, and Hub-shared configuration. `trust_kit_lib.py` is the shared
in-place-upsert implementation behind the others, so a kit file's operator edits (host-local
ports, bind dirs) always survive a re-run.

- **`new_trust.py`** (`make new-trust TRUST_CODE=<CODE> TRUST_NAME="..." HUB=<stag|prod>`; leave
  `HUB` unset for development) — scaffolds `trust/.env.<CODE>.<env>` from the base template
  (`trust/.env.example`), prepending the trust's identity (`TRUST_NAME` / `TRUST_CODE` /
  `TRUST_REGION`). Refuses to overwrite an existing kit (the root `new-trust` target also
  refuses beside a kit still under its pre-FLIP#1479 name). The result is ready for
  registration to fill in credentials (`make -C deploy/providers/AWS register-trusts KIT=<CODE>
  HUB=<hub>` on a deployed hub, `make register-trust KIT=<CODE>` in development).
- **`trust_kit_lib.py`** — the single kit-file writer behind `distribute_trust_kits.py`,
  `sync_trust_kit.py`, and the AWS registration path. Merges a kit dict into a kit file while
  preserving operator edits: credentials (`TRUST_API_KEY` / `TRUST_INTERNAL_SERVICE_KEY`) are
  written only on a new registration and never clobbered on an idempotent re-run; metadata
  (`EXPECTED_TRUST_ID` / `FL_KIT_SLOT` / `FL_KIT_SLOT_NUMBER`) and the Hub-shared block are
  upserted unconditionally, the latter under a sentinel header added once on first write. Writes
  every kit file `0600`.
- **`distribute_trust_kits.py`** — writes a trust kit file from `register_trust`'s JSON output:
  one kit (object or one-element array) on stdin to the required `--target PATH` — the dev
  `register-trust` Makefile pipe (`--target trust/.env.<CODE>.<env>`) and
  `deploy/providers/AWS/scripts/register-trusts.sh` for stag/prod alike.
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
  for an on-prem trust host, run as a precheck by `up-onprem-trust`. Diagnoses what's missing
  before `make up-onprem-trust` will succeed: public IP, docker swarm state, kit file presence,
  Hub-shared block, kit credentials, `FL_KIT_DIR` contents, OMOP/Orthanc data dirs, GPU
  capacity (`NUM_AVAILABLE_GPUS` vs. what the host actually exposes), and — probed outbound from
  the operator's account (FLIP#1726) — the hub API's `/health`, a TCP connect to the FL endpoint
  the client dials, and the image registries. Each check renders ✅ / ❌ /
  ⏳ (pending on an earlier check) and exits non-zero if anything fails, so an operator gets
  concrete diagnostics instead of a cryptic compose or pydantic failure deeper in the stack.

## Site upgrades

- **`site_upgrade.py`** (invoked by `make -C trust upgrade-trust`, and by the AWS/Helm twins —
  see `AGENTS.md` "Site release upgrades") — the **plan-and-guard-and-pin** phase of a site release
  upgrade (FLIP#1204). Resolves the target tag against what the hub reports on `/api/health`
  (defaults to that; `--tag vX.Y.Z` or `--tag sha-<short7>` overrides), says when GitHub lists a newer
  release (offering to stop and move to it only when the hub already runs it), runs the guards (image tag
  shape, downgrade → `--force`, release-tag checkout match → `--allow-checkout-drift`, every image
  actually built at the tag via `docker manifest inspect`, operator confirmation unless `--yes`),
  and — if everything passes — rewrites `DOCKER_TAG` / `DOCKER_FL_TAG` in the kit's Hub-shared
  block. The script **stops before any container is touched**; the Makefile then re-includes the
  updated kit and does the pull / recreate (`_upgrade-trust-apply` on trust; the EC2 twin and
  `make -C trust/deploy/helm upgrade-trust-k8s` do the same for the other shapes). The operator
  entry points wrap both phases together — `make upgrade-onprem-trust KIT=<CODE>` also runs
  `onboard-onprem-trust --gate` (the readiness checklist implemented by `onboard_onprem_trust.py`,
  above) before the plan phase. Exit codes (the Makefile's contract): 0 pinned, 2 no usable target,
  3 refused downgrade, 4 not confirmed, 5 an image is missing at the target tag, 6 the checkout is
  not at the target release. Running the script directly rewrites the kit but leaves the containers
  at the old tag — use the Make targets. Runbook: `docs/source/sys-admin/admin-upgrading-sites.rst`.

## Node self-test

- **`selftest_node.sh`** (`make -C trust selftest-node FL_BACKEND=<backend>`) — the hubless
  trust-node self-test checklist, run on the node itself, which writes a JSON + markdown report
  (FLIP#1390, FLIP#1406).
- **`selftest_kit.py`** — called by `selftest_node.sh` to write a hubless kit file for the
  self-test: a hub URL under `.invalid`, fresh random keys, slot `Trust_1`.
