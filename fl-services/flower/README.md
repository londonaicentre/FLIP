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

# Flower Federated Learning services

This folder contains the Flower FL service images and the per-network provisioning that
secures them. A Flower network is a long-running **SuperLink** (server) that several
long-running **SuperNode**s (clients) connect to, plus a thin `fl-api` that the Central
Hub drives. `fl-base` holds the shared base image the others build on. Trusts reach the
SuperLink only through the **fleet proxy** (`fleet-proxy/`, below).

Unlike FLARE (which mints per-participant *startup kits*), Flower provisioning generates
**TLS certificates** for secure SuperLink↔SuperNode↔CLI communication and **SuperNode
authentication key pairs** — the Flower equivalent of the per-trust identity. See the
upstream how-tos for the underlying mechanics:
[TLS connections](https://flower.ai/docs/framework/how-to-enable-tls-connections.html) and
[SuperNode authentication](https://flower.ai/docs/framework/how-to-authenticate-supernodes.html).

## Images: built in CI, or locally as `:dev`

The Flower images — `flower-fl-base`, `flower-superlink`, `flower-supernode`, `flower-fl-api`,
`flower-fleet-proxy` — are built in CI by [`fl-docker-build-flower.yml`](../../.github/workflows/fl-docker-build-flower.yml)
whenever `fl-services/flower/**` or `flip-utils/**` change, and published to GHCR as `:<sha>`,
`:stag` (on `develop`) and `:prod` (on `main`). Deployments pull them via `DOCKER_FL_TAG`.

To iterate locally **before pushing**, build them tagged `:dev`:

```bash
make -C fl-services/flower build                  # flwr-base first, then superlink/supernode/fl-api/fleet-proxy
make -C fl-services/flower build LOCAL_DEV=true   # include dev-only deps
make -C fl-services/flower build DOCKER_FL_TAG=rc  # tag the whole chain :rc instead — on a shared host :dev is daemon-wide
make up FL_BACKEND=flower DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev   # run the hub stack on local images
```

`DOCKER_FL_REGISTRY=` empties the registry so Docker resolves the local `flower-*:dev` images
(see [`deploy/fl_backend.mk`](../../deploy/fl_backend.mk)).

### Containers run as a non-root user (GHSA-8465), with capabilities hardened

`flower-supernode` and `flower-fl-base` inherit the non-root `app` user from the upstream `flwr/base`
image. The trust-deployment `fl-client-net-*` services (`trust/deploy/compose/compose_trust.{prod,development}.flower.yml`)
also set `security_opt: [no-new-privileges:true]` and `cap_drop: [ALL]`, matching every other trust-side
service — the `flower-supernode` entrypoint does no chmod/chown at all, so no capability needs adding
back in either environment. See [`deploy/README.md`](../../deploy/README.md#linux-capability-restrictions)
for the full per-service capability table.

## Runtime dependency installation (and where flip-utils comes from)

Flower ≥1.32 can install an app's declared dependencies **at run time**: the app process runs
`uv sync` against the FAB's `pyproject.toml` into an isolated per-run environment, which is
prepended to `sys.path`. **FLIP turns this off on both sides** (FLIP#1418): the SuperLink sets
`FLWR_DISABLE_RUNTIME_DEPENDENCY_INSTALLATION=1` (every compose file, and the ECS task on every
estate), and the SuperNodes do not pass `--allow-runtime-dependency-installation`, which is
Flower's default. The ServerApp and the ClientApps run in the image's own environment: `flwr/base`
plus `flip-utils[flower,full]`, which `fl-base` installs from `/opt/flip-utils`. That environment carries
every package the template app code imports. Nothing checks it against the templates'
`[project].dependencies`, whose version floors and extras (`pandas>=3.0.0`, `flwr[simulation]`) no
longer apply at run time, so a package the image lacks fails as an import error when the app starts
on the SuperLink or at a trust, not at build time. A dependency an app needs ships by adding it to
flip-utils' `full` extra (what `fl-base` installs) and rebuilding the FL images.

Two failures made the per-run install unusable, in flwr 1.36 through 1.39:

- **A cold sync outlives the task heartbeat.** The first run after a container starts syncs torch and
  MONAI from scratch (about two to three minutes); Flower's task liveness window is a 30 s heartbeat
  with a patience of 2, so the task token expires mid-install (`Runtime task-token authentication
  failed`). On the SuperLink the run never issues a round; on a SuperNode round 1 reports
  `ClientApp stopped responding`.
- **SuperNodes share one per-run env and delete it.** Flower names the environment after the run
  id, so every ClientApp launch in a run (one per round and per evaluation) uses the same directory,
  and each launch's exit handler `shutil.rmtree`s it. A later launch then syncs into a directory
  being removed: `uv` falls through to the base interpreter and fails with `Permission denied`
  removing setuptools' `_distutils_hack`, or the ClientApp loses its environment mid-round.

With nothing installed at run time, no FL host needs a route to PyPI or `download.pytorch.org`
during a run, which is what the LZA estate's sealed egress (FLIP#749) already required.

The `[tool.uv]` tables in the `fl-apps/flower/*` template pyprojects stay as a guard for anything
that does run `uv sync` against a template inside an FL image (for example, with per-run install
re-enabled). A template cannot be synced on a workstation, because `/opt/flip-utils` exists only in
the images. The tables do two things:

- `[tool.uv.sources] flip-utils = { path = "/opt/flip-utils" }` stops uv resolving flip-utils from
  **PyPI**, which would shadow the in-repo copy baked into the images
  ([#767](https://github.com/londonaicentre/FLIP/issues/767)).
- `torch`/`torchvision` are pinned to PyTorch's cu130 index (NVIDIA driver ≥580).

### Testing an unpublished flip-utils

No PyPI release or version bump is needed. The containers run whatever their image bakes, so:

1. Rebuild the images from your branch (the source lands at `/opt/flip-utils`):

   ```bash
   make build-fl FL_BACKEND=flower
   ```

2. Restart the Flower services onto the rebuilt `:dev` images (hub stack):

   ```bash
   make restart-fl FL_BACKEND=flower DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev
   ```

3. Before trusting a run, confirm the running container carries your change:

   ```bash
   docker exec <supernode> cat /opt/flip-utils/flip/<changed file>
   ```

> ⚠️ The tutorials under `fl-tutorials/flower/` **do not declare flip-utils at all**, on purpose:
>
> - They cannot carry the template's `/opt/flip-utils` pin. That path does not exist on a
>   workstation, so a local `uv sync` would fail.
> - Declaring flip-utils without the pin would make any `uv sync` resolve it from **PyPI**, a
>   release that can drift from the in-repo `flip-utils/`. That includes a local `flwr run`
>   (`sim-tutorial`), whose SuperLink still runtime-installs an app's declared dependencies into a
>   per-run environment prepended to `sys.path`.
>
> Undeclared, `import flip` never resolves to PyPI. It resolves to the environment running the app:
>
> - on the compose stacks, the image's baked-in copy (a flip-utils change arrives only via an image
>   rebuild, `make build-fl FL_BACKEND=flower`);
> - under `sim-tutorial`, the checkout's `flip-utils/`.

## Step-by-step provisioning

Provisioning lives under [`provision/`](provision/): the [`scripts/`](provision/scripts/) that
generate the credentials, and the gitignored `creds/` output they write.

### What gets generated

`make -C fl-services/flower provision NET_NUMBER=<N>` runs
[`provision/scripts/generate-tls-certificates.sh`](provision/scripts/generate-tls-certificates.sh),
which drives the vendored `generate_creds.py` to produce, per network:

```
provision/creds/net-<N>/
├── certificates/   ca.crt, ca.key, server.pem, server.key   # TLS for SuperLink/SuperNode/CLI
└── keys/           supernode_credentials_<i>{,.pub}          # SuperNode auth key pairs, one per kit slot
```

`make provision` mints one key pair per entry in this environment's `FL_KIT_SLOT_NAMES`, or 2 when
the env file names no pool (a stag/prod one, whose keys are the pool). Override with
`NUM_SUPERNODES=<n>` (or `FLOWER_NUM_SUPERNODES=<n>` when calling the script directly).

A trust staged into slot *N* fetches `keys/supernode_credentials_N`, so a kit that stops short of the
pool leaves the later slots unable to start. Never re-run `provision` to grow a deployed kit: it
regenerates the CA and every key, and invalidates every node already holding credentials. Add keys
instead ([below](#adding-supernode-keys-to-a-deployed-hub)).

> ⚠️ These are development credentials. Configure validity, organization or the server SANs
> (`SERVER_SAN_IPS`) by editing the globals at the top of
> [`provision/scripts/generate_creds.py`](provision/scripts/generate_creds.py); extra DNS SANs for a
> hub deployment go in `FLOWER_EXTRA_SERVER_SANS` without editing anything.

### Provisioning command

```bash
make -C fl-services/flower provision NET_NUMBER=1   # → provision/creds/net-1/
make -C fl-services/flower provision NET_NUMBER=2   # second net for the default dev stack

# A stag/prod kit: HUB selects the env file the slot pool is read from, and the
# SuperLink cert needs the hub's own names.
FLOWER_EXTRA_SERVER_SANS="fl-server-net-1.flip.local,fl.stag.flip.aicentre.co.uk" \
  make -C fl-services/flower provision NET_NUMBER=1 HUB=stag
```

`make -C fl-services/flower generate-tls-certificates-net-1` and `-net-2` are shortcuts for
`provision NET_NUMBER=1` and `=2` (same script, same `NUM_SUPERNODES` derivation), for a Makefile or
script that targets one net by name.

`creds/` is the gitignored `FL_PROVISIONED_DIR` for Flower (see
[`deploy/fl_backend.mk`](../../deploy/fl_backend.mk)); the dev compose overlays and the standalone
[`compose.secure.yml`](compose.secure.yml) mount `certificates/` into `/certs` and `keys/` into
`/keys`.

### How SuperNodes are authenticated

The SuperLink only accepts SuperNodes whose **public** key it has been told about. Each
SuperNode's `.pub` key is registered with the SuperLink (and, in the hub, mapped to a trust name)
by [`register-supernode-keys.sh`](register-supernode-keys.sh) — it runs `flwr supernode register`
for every `/keys/*.pub`, naming each node after its trust (`--name`, flwr ≥ 1.38, so the ServerApp
sees the trust behind every node id through `Grid.get_nodes()`), skipping already-registered
keys. This is the Flower analogue of claiming a participant slot. With `TRUST_NAMES` unset each key
names its own slot, `supernode_credentials_<N>.pub` being `Trust_<N>` (the ECS hub and the prod
compose); the dev compose stacks pass `TRUST_NAMES`, paired with the keys by position.

### Adding SuperNode keys to a deployed hub

A SuperNode key pair is not signed by the CA, so more can be minted at any time without touching the
CA, the server certificate or any key already handed out. This is the Flower analogue of FLARE's
`provision --add_client`:

```bash
make -C fl-services/flower provision-add-supernode KEY_NUMBER=<n> OUT_DIR=<dir>   # → <dir>/keys/supernode_credentials_<n>{,.pub}
```

On a stag/prod hub, do not call it by hand. One command mints the next `N` key pairs and makes them
claimable, with no Terraform apply and no SuperLink restart (FLIP#1942):

```bash
make -C deploy/providers/AWS add-fl-kits N=<n> HUB=<stag|prod>
```

**The keys are the pool.** On a Flower hub flip-api's slot pool is every slot `Trust_<N>` whose pair
`keys/supernode_credentials_<N>` and `.pub` is complete on every net under
`s3://<AICENTRE_BUCKET_NAME>/fl-flower-participant-kits/<FLOWER_KIT_DATE>/` (its `FLOWER_KITS_S3_URI`),
read at every registration. The registration one-shot on ECS names each node the same way, from the
key's file name, so there is no separate slot list to keep in step (`FL_KIT_SLOT_NAMES` is a dev-only
setting, which the dev compose stacks pass as `TRUST_NAMES`). `add-fl-kits` first checks that
`FLOWER_KIT_DATE` is the date the hub runs, since it copies that date's public keys onto the hub.

`add-fl-kits` (`deploy/providers/AWS/scripts/add_fl_kits_flower.sh`) works in this order, and the
order is what keeps a slot from being claimed before its SuperNode would be admitted:

1. Numbers the new keys past every key in S3 and every name already minted, and records the new
   names in `s3://<AICENTRE_BUCKET_NAME>/fl-flower-kit-state/<FLOWER_KIT_DATE>/minted_slot_names`.
   Flower has no CA registry of issued identities, so this record is what stops a name being reused
   after its key is deleted from S3. No ECS task role reads that prefix. The record is per kit date:
   a new kit date starts numbering from its own keys alone, so provision its kit with
   `NUM_SUPERNODES` at least the highest slot the hub has assigned (on stag/prod it defaults to 2).
   Otherwise the trusts holding the higher slots have no key under it, and the first names
   `add-fl-kits` mints there are theirs: the run calls them claimable, but they add no free slot.
2. Mints the key pairs into a temporary directory and uploads the **public** keys (additive
   `aws s3 cp`).
3. Runs the `efs-provision-certs` task definition with the key sync alone as its command, which
   copies the public keys to the hub's EFS beside the existing ones, then the
   `flower-register-supernode-keys` task definition as it is, which registers every key with the
   running SuperLink and maps the node ids on the fl-api. Keys already registered are skipped, and a
   running job is not disturbed.
4. Uploads the **private** keys, only once that task's exit code and its log both say every key was
   registered. Until then flip-api leaves the slot out, with a warning naming the missing key.

If a run stops before step 4, the private keys are gone with it: delete the public keys it names
from S3 and run it again for the slots still needed. The numbering skips the spent names. If it
stops during step 4, those public keys are already on the hub's EFS and registered with the
SuperLink, and deleting them from S3 removes neither; without their private keys no SuperNode can
use them, and the abort message says so.

To withdraw a slot that no trust holds, delete its private key from S3: flip-api stops handing it
out at the next registration. Keep the public key: it is what keeps the number from being minted again
(a key provisioned with the kit is only in the minted-names record once an `add-fl-kits` run has seen
it), and flip-api then logs the slot as not claimable at every registration, which is expected. `upload-creds-to-s3` is for a **new** kit date only, since its
`--delete` removes every key added under a live one.

**Registrations do not survive the SuperLink.** It runs without `--database`, so it holds registered
SuperNodes in memory. Terraform runs the registration one-shot again whenever it registers a new
SuperLink task definition, from the keys on EFS (`add-fl-kits` adds to them and never removes one).
A SuperLink restarted any other way (`make deploy-centralhub` or `rollback-centralhub` (new task-definition revisions made outside Terraform), staging power-on, a forced deployment, a crash or a Fargate retirement) comes back with no registered SuperNode until the
one-shot runs again: `terraform taint 'null_resource.flower_register_supernode_keys[0]'` and apply.
An `add-fl-kits` run also registers every key again, but it always mints at least one new slot.
FLIP#1947 tracks closing this.

## The fleet proxy: what a trust can reach (FLIP#1517)

Since flwr 1.40 the SuperLink serves **every** API from one HTTP app on one port (8000): health,
the **Control API** (`/v1/control/*` — start, stop and list runs, register and unregister nodes),
the **Fleet API** (`/v1/fleet/*` — what a SuperNode calls) and the **Runtime API**
(`/v1/runtime/*` — the ServerApp's own routes for messaging SuperNodes). Open-source Flower
does not authenticate the Control API at all, and authenticates Runtime only behind a flag that
subprocess isolation (FLIP's mode) ignores; SuperNode auth signs Fleet requests alone. So a trust that could reach port 8000
could submit its own ServerApp, stop another project's run or forge instructions to other trusts'
SuperNodes — and there is no flag to serve Fleet on its own listener.

Trusts therefore never reach port 8000. They reach `flower-fleet-proxy`, an nginx that:

- listens on the trust-facing port 9092 with the SuperLink's own server certificate, so trust kits,
  SANs and `FL_SERVER_PORT` are unchanged;
- forwards only the 12 Fleet routes in [`fleet-proxy/fleet-routes.map`](fleet-proxy/fleet-routes.map)
  (POST only, matched on the method and raw request URI) to the SuperLink over TLS, and answers
  everything else 404;
- answers 503 when it cannot reach or verify the SuperLink, which the SuperNode retries. Its health
  check (`fleet-proxy/healthcheck.sh`, also on the ECS sidecar) then fails, since the NLB's TCP
  check alone would still pass. The access log's `upstream=` field tells the two apart;
- refuses a request body over `FLEET_PROXY_MAX_BODY_SIZE` (default `64m`, against flwr's 5 MiB
  array chunks) with 413, which the SuperNode does not retry. Raise it alongside any
  `FLWR_PRIVATE_MAX_ARRAY_CHUNK_SIZE` override.

| Where | Shape |
|---|---|
| ECS task | sidecar in the SuperLink's network namespace; upstream `127.0.0.1:8000` (the image defaults) |
| prod compose harness | its own container publishing `FL_SERVER_PORT`, upstream `fl-server-net-1:8000` — compose restarts containers one at a time, and a proxy sharing a restarted SuperLink's namespace would keep the dead one |
| hub dev compose | `fl-fleet-proxy-net-<N>`, the only hub service on `fl-net-<N>`, answering there to `fl-server-net-<N>`; the SuperLink stays on `default` |
| `compose.secure.yml` | `fleet-proxy` on both networks, answering to `superlink` on `flwr-trust-net` where the SuperNodes are |
| `compose.dev.yml` (insecure) | none: no trusts, so the SuperNodes dial `superlink:8000` |

`fl-api-flower/tests/test_fleet_proxy_allowlist.py` pins the allowlist to the Fleet routes of the
flwr that service locks, and to every path the SuperNode client sends, so a Flower bump that adds,
renames or drops a Fleet route fails CI instead of breaking SuperNodes mid-round.

## Running a network standalone (no hub / trusts)

> **Fetch the dataset before `up`** if you will submit the default `APP=xray_classification`.
> Otherwise it trains on empty placeholder mounts with no sign that the data is missing; see
> the steps below the command list.

```bash
make -C fl-services/flower up                 # INSECURE stack (1 SuperLink + 2 SuperNodes + fl-api)
make -C fl-services/flower up-secure          # SECURE stack (TLS + SuperNode auth); needs `provision` first
make -C fl-services/flower submit APP=xray_classification   # submit a job to the running stack
make -C fl-services/flower down
```

`APP=` is the tutorial's bare name: the stack mounts `fl-tutorials/flower/` at `/app/src`, and the fl-api
resolves the name under `/app/src/<category>/<name>` and expects exactly one match.

**Fetch the dataset before `up`.** The default `APP=xray_classification` is not self-contained: it
reads images and a dataframe from the SuperNodes' bind mounts, and `up` only creates *placeholder*
mounts (`DEV_IMAGES_DIR` defaults to `/tmp/flwr-dev-images`, `DEV_DATAFRAME` to
`/tmp/flwr-dev-dataframe.csv` — see [`compose.dev.yml`](compose.dev.yml)). Submitting against those
trains on an empty dataset with no signal that the data is missing. So download the data first, then
point both variables at it when bringing the net up:

```bash
make -C fl-tutorials download-xray-data FL_BACKEND=flower   # → fl-tutorials/data/xrays_mini_300/
make -C fl-services/flower up \
  DEV_IMAGES_DIR=$(pwd)/fl-tutorials/data/xrays_mini_300/accession-resources \
  DEV_DATAFRAME=$(pwd)/fl-tutorials/data/xrays_mini_300/dataframe.csv
```

(Run from the repo root; `data/` is gitignored. `up-secure` takes the same two variables.)

The central-hub multi-net Flower topology is the separate
[`deploy/compose.development.flower.yml`](../../deploy/compose.development.flower.yml), driven by the
root `make up FL_BACKEND=flower`.
