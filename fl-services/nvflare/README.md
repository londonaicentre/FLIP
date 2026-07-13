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

# NVFLARE Federated Learning Services

This folder contains base code to create NVIDIA FLARE federated learning networks, each containing a set of clients, a server and an API. `fl-api-base` and `fl-base` are base services used by the provisioning command to build upon. `fl-base` can be used to test applications locally, but is a single container that does not constitute a fully working FL network.

## Layout

```text
fl-services/nvflare/
├── fl-base/          # base image for the server + clients      (flare-fl-base)
├── fl-server/        # FLARE server image                       (flare-fl-server)
├── fl-client/        # FLARE client image                       (flare-fl-client)
├── fl-api-base/      # FastAPI admin-API image — see its README (flare-fl-api)
├── provision/        # project YAMLs, scripts/, and the gitignored workspace-{dev,stag,prod}/ output
├── compose.dev.yml   # build + standalone-run definitions for the four FL images
├── Makefile          # this backend's build / provision / up / down / submit
└── README.md
```

## Anatomy of a network

A provisioned **network** (`net-N`) is a self-contained FLARE deployment. Each net runs:

- **`fl-server`** (image `flare-fl-server`) — the single FLARE server that coordinates rounds and aggregates client updates.
- **`fl-api`** (image `flare-fl-api`) — a FastAPI service wrapping the FLARE admin `Session`. The Central Hub drives the net **only** through this API (submit/abort jobs, poll server/client status). See [`fl-api-base/README.md`](./fl-api-base/README.md).
- **`fl-client-1 … fl-client-N`** (image `flare-fl-client`) — one client per participating trust, each holding that trust's signed startup kit.

`fl-base` and `fl-api-base` are the **base build contexts** the provisioning step layers on top of: `fl-base` backs the server and clients, `fl-api-base` backs the API. `fl-base` alone runs as a single container for local app testing, but is not a working FL network.

Provisioning mints, per participant, a signed **startup kit** under `provision/workspace-dev/net-N/services/<participant>/` — a `startup/` folder (`root_CA.pem`, `signature.json`, `fed_*.json`, `*.crt`/`*.key`, `start.sh`/`sub_start.sh`/`stop_fl.sh`) and a `local/` folder. The shared root CA that signs these kits is what binds a net together — see [Step-by-step provisioning](#step-by-step-provisioning) below.

## Images: built in CI, or locally as `:dev`

The four FL images — `flare-fl-base`, `flare-fl-server`, `flare-fl-client`, `flare-fl-api` — are built
in CI by [`fl-docker-build-nvflare.yml`](../../.github/workflows/fl-docker-build-nvflare.yml) whenever
`fl-services/nvflare/**` or `flip-utils/**` change, and published to GHCR as `:<sha>`, `:stag` (on `develop`)
and `:prod` (on `main`).
Deployments pull them via `DOCKER_FL_TAG`, so a change to fl-api code or the `flip` package flows into the
next deployment on that tag.

To iterate locally **before pushing**, build them tagged `:dev` from the repo root:

```bash
make build-fl                  # builds flare-fl-{base,server,client,api}:dev (base first, then derived)
make build-fl LOCAL_DEV=true   # include dev-only deps (e.g. plotting for the diffusion app)
```

Then run the stack on those local images instead of GHCR:

```bash
make up DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev
```

`DOCKER_FL_REGISTRY=` empties the registry so Docker resolves the local `flare-fl-*:dev` images (see
[`deploy/fl_backend.mk`](../../deploy/fl_backend.mk)). Note `make build` does **not** build these — the deploy
compose pulls FL images by tag, so their build definitions live only in [`compose.dev.yml`](./compose.dev.yml).

## Step-by-step provisioning

### Project yml file

The per-environment project files under [`provision/`](./provision/)
(`net-1_project_dev.yml`, `net-2_project_dev.yml`, `net-1_project_stag.yml`, `net-1_project_prod.yml`)
define the services available within a network. Modify the relevant one if you want to:

- Incorporate further services (e.g. >2 clients)
- Modify GPU resources for the services
- Modify default ports [etc.]

### Net-specific yml file

You can run `make -C fl-services/nvflare provision NET_NUMBER=${NET_NUMBER}` to create a network: this will create an instance of the
services defined in `net-${NET_NUMBER}_project_dev.yml`, substituting the naming by `net-${NET_NUMBER}`.
You can also pass `FL_PORT` if you do not want to use the default (which will be the same for each created net).

### Provisioning command

`make -C fl-services/nvflare provision` wraps the `nvflare provision` CLI. It runs from the
`fl-services/nvflare/fl-api-base` uv project (which declares `nvflare`), so it resolves even though
the repo-root `flip` project has no dependencies. End to end, the target:

1. **Generates the kits.** `nvflare provision` reads the net-specific yml and writes each
   participant's startup kit under `provision/workspace-dev/net-${NET_NUMBER}/prod_XX/`, with default
   names. Each kit has a `local/` and a `startup/` folder; `startup/` holds the start/stop scripts
   (`start.sh`, `sub_start.sh`, `stop_fl.sh`), per-service config (`fed_[service_name].json`), and the
   signature + certificate files. Those certs link the participants together and make the kits
   **non-reusable** across nets.
2. **Moves them into place.** The make target relocates every service into
   `provision/workspace-dev/net-${NET_NUMBER}/services/`.
3. **Adds the runtime code.** Files the `nvflare provision` CLI doesn't emit but the services still
   need (e.g. the Admin API's Python files) are copied in from `fl-base` (server + clients) and
   `fl-api-base` (API).

Once your network is provisioned, you can test it works by bringing the net up standalone (no hub/trusts)
on the local `:dev` images — build them first with `make build-fl` (see [Images](#images-built-in-ci-or-locally-as-dev)):

```sh
make -C fl-services/nvflare up NET_NUMBER=<NET_NUMBER>
```

### Onboarding a new client onto an existing network

The **default** is over-provisioning: stag/prod networks are minted up front with spare
client slots. `make -C fl-services/nvflare provision-stag` / `provision-prod` mint
`Trust_1 .. Trust_N` (default 50 / 500 — see
[FLIP#626](https://github.com/londonaicentre/FLIP/issues/626)). Onboarding a new trust
then just **claims the next unclaimed `Trust_N` kit** via `register_trust` on the hub —
no provisioning step at all, provided the slot name is already in the hub's pool (see
[Activating the new slot on the hub](#activating-the-new-slot-on-the-hub)).

Don't assume the spare pool exists — nets provisioned before FLIP#626 (including the
current prod net-1) carry **no** spare slots. Check what is actually in S3 first:

```sh
aws s3 ls s3://${AICENTRE_BUCKET_NAME}/fl-flare-participant-kits/${FLARE_KIT_DATE}/net-<N>/services/
```

When the spare pool runs dry (or never existed) — or for an ad-hoc dev add — add a
single client **without disrupting the running federation** using the official
`nvflare provision --add_client` flag:

```sh
make -C fl-services/nvflare provision-add-client      NET_NUMBER=1 CLIENT_NAME=Trust_3    # dev
make -C fl-services/nvflare provision-add-client-stag NET_NUMBER=1 CLIENT_NAME=Trust_51   # stag workspace
make -C fl-services/nvflare provision-add-client-prod NET_NUMBER=1 CLIENT_NAME=Trust_501  # prod workspace
```

This reuses the network's existing root CA (loaded from the preserved
`workspace-<env>/net-N/state/`) to sign **only** the new client's kit, and leaves every
already-onboarded participant's kit byte-identical — no re-provision, no CA rotation, no
fleet-wide redeploy. The new kit lands in `workspace-<env>/net-N/services/<CLIENT_NAME>/`.

**CA-state custody.** The preserved `state/` is the hard prerequisite: it holds the root
CA that signs the new kit. The workspace is gitignored, so it exists only in the checkout
that provisioned (or last added to) the net. `upload-kits-to-s3` mirrors it to S3
alongside the kits, so to add a client from a fresh checkout, restore the CA state and
the server kit (the two things `add-client.sh` checks for) first:

```sh
aws s3 cp s3://${AICENTRE_BUCKET_NAME}/fl-flare-participant-kits/${FLARE_KIT_DATE}/net-<N>/state/ \
  provision/workspace-<env>/net-<N>/state/ --recursive
aws s3 cp s3://${AICENTRE_BUCKET_NAME}/fl-flare-participant-kits/${FLARE_KIT_DATE}/net-<N>/services/fl-server-net-<N>/ \
  provision/workspace-<env>/net-<N>/services/fl-server-net-<N>/ --recursive
```

**Pushing the new kit to S3 (stag/prod).** Upload **additively** — only the new kit, plus
a refresh of `state/cert.json` so the mirrored CA registry records the new identity for
the next add-from-a-fresh-checkout:

```sh
aws s3 cp provision/workspace-<env>/net-<N>/services/<CLIENT_NAME>/ \
  s3://${AICENTRE_BUCKET_NAME}/fl-flare-participant-kits/${FLARE_KIT_DATE}/net-<N>/services/<CLIENT_NAME>/ --recursive
aws s3 cp provision/workspace-<env>/net-<N>/state/cert.json \
  s3://${AICENTRE_BUCKET_NAME}/fl-flare-participant-kits/${FLARE_KIT_DATE}/net-<N>/state/cert.json
```

> ⚠️ Do **not** push a single added kit with `upload-kits-to-s3`. That target runs
> `aws s3 sync --delete` over the **whole** `net-<N>/` prefix, so unless your local
> workspace is a complete, byte-identical copy of everything in S3 (a fresh checkout
> is not), it will **delete or overwrite the live kits of already-onboarded trusts**.
> Reserve it for full (re-)provisions in a maintenance window.

> ⚠️ Do **not** add a client by appending it to the project YAML and re-running
> `make provision*`. Those targets `rm -rf` the workspace first (including `state/`), so
> NVFLARE mints a fresh root CA and regenerates **every** participant's certs — forcing a
> redeploy of every already-onboarded trust. `provision-add-client` preserves `state/`
> and avoids that.
>
> To **grow the pool** itself you have two routes. **No disruption (preferred):** loop
> `provision-add-client*` — each add reuses the existing root CA, so no already-onboarded
> trust is touched; only the new slots are minted. **Bulk, but disruptive:** bump
> `STAG_NUM_CLIENTS` / `PROD_NUM_CLIENTS` and re-provision out of band — one command, but it
> wipes `state/`, rotates the root CA, and so **forces a redeploy of every already-onboarded
> trust** (the same fleet-wide churn called out above). Only take this route in a maintenance
> window where redeploying the whole fleet is acceptable, then re-upload:
> `make -C fl-services/nvflare provision-prod PROD_NUM_CLIENTS=1000 && make -C fl-services/nvflare upload-kits-to-s3 PROD=true`.

### Activating the new slot on the hub

A kit in S3 is not yet claimable. `register_trust` claims rows from the hub's
`fl_kit_slot` DB pool, which is seeded from the `FL_KIT_SLOT_NAMES` env var **only at
flip-api boot** (`flip_api/db/seed/fl_kit_slots.py` — additive; it never deletes or
re-assigns rows, so existing trust↔slot bindings are safe across restarts). An exhausted
pool surfaces at registration as `NoFreeKitSlotError: "No FL kit slots available…"`.
To activate the new slot:

- **Dev**: add the name to `FL_KIT_SLOT_NAMES` in `.env.development` and restart flip-api.
- **Stag/prod (ECS)**: add the name to `FL_KIT_SLOT_NAMES` in `.env.stag` /
  `.env.production` (this keeps future Terraform renders in sync — `locals.tf` bakes the
  value into the flip-api task definition via `TF_VAR_FL_KIT_SLOT_NAMES`), then register a
  new flip-api task-definition revision carrying the updated value and point the service
  at it with `aws ecs update-service`. Note `make deploy-centralhub` will **not** propagate
  it — it clones the current task definition and swaps only the image tag — and a full
  `make apply` is the wrong tool for an env-only change. Avoid bouncing flip-api while an
  FL run is live (in-flight metrics/results uploads can fail during the swap) — check the
  `/ecs/fl-server-net-<N>` logs for activity first.

On restart the seed inserts the new row, and `make register-trust KIT=<CODE>` (see the
[root README](../../README.md#trust-registration)) claims it — registration writes the
claimed `FL_KIT_SLOT` / `FL_KIT_SLOT_NUMBER` into the trust's kit file, and the
trust-host deploy pulls that kit from the S3 path above.

## Standalone targets

This backend's [`Makefile`](./Makefile) owns its own `build`/`provision`/`up`/`down`/`submit`. To run
**one provisioned net** on its own — no Central Hub, no trusts — on the local `:dev` images:

```sh
make -C fl-services/nvflare up NET_NUMBER=<N>     # fl-server + fl-api + 2 clients
make -C fl-services/nvflare down                  # tear it down
```

`make -C fl-services/nvflare submit` is **intentionally not wired** for NVFLARE: job submission goes
through the provisioned FLARE admin API, not a simple HTTP POST (unlike Flower's `submit`). To run a
job standalone, use the simulator harness instead:

```sh
make -C fl-tutorials run-tutorial TUTORIAL=<name>
```
