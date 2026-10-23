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
image. The trust-deployment `fl-client-net-*` services (`trust/deploy/compose_trust.{production,development}.flower.yml`)
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
plus `flip-utils[full]`, which `fl-base` installs from `/opt/flip-utils`. That environment carries
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
that does run `uv sync` against a template inside an FL image (per-run install re-enabled; the
`/opt/flip-utils` path exists nowhere else, so a template cannot be synced on a workstation):
`[tool.uv.sources] flip-utils = { path = "/opt/flip-utils" }` stops uv resolving flip-utils from
**PyPI**, which would shadow the in-repo copy baked into the images
([#767](https://github.com/londonaicentre/FLIP/issues/767)), and `torch`/`torchvision` are pinned
to PyTorch's cu130 index (NVIDIA driver ≥580).

**Testing an unpublished flip-utils** therefore needs no PyPI release and no version bump: rebuild
the images from your branch (`make build-fl FL_BACKEND=flower` — the source lands at
`/opt/flip-utils`) and restart the flower services onto the rebuilt `:dev` images
(`make restart-fl FL_BACKEND=flower DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev` for the hub stack).
Every flip-utils change ships via an image rebuild — the containers run whatever their image bakes,
so confirm the running container carries your change by inspecting the file you edited
(`docker exec <supernode> cat /opt/flip-utils/flip/<changed file>`) before trusting a run.

> ⚠️ The tutorials under `fl-tutorials/flower/` **do not declare flip-utils at all**. A tutorial
> pyproject cannot carry the template's `/opt/flip-utils` pin (the path does not exist on a
> workstation, so a local `uv sync` would fail), and declaring flip-utils without the pin would make
> any `uv sync` resolve it from **PyPI**, a release that can drift from the in-repo `flip-utils/`.
> That includes a local `flwr run` (`sim-tutorial`), whose SuperLink still runtime-installs an app's
> declared dependencies into a per-run environment prepended to `sys.path`. Undeclared, `import flip`
> resolves to the environment running the app and never to PyPI: the image's baked-in copy on the
> compose stacks, where a flip-utils change arrives only via an image rebuild
> (`make build-fl FL_BACKEND=flower`), and the checkout's `flip-utils/` under `sim-tutorial`.

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

How many key pairs: `make provision` mints one per entry in this environment's `FL_KIT_SLOT_NAMES`,
falling back to 2 where no pool is named (a dev env file). A trust staged into slot *N* fetches
`keys/supernode_credentials_N`, so a kit that stops short of the pool leaves the later slots unable
to start — and there is no add-a-node path, unlike FLARE's `provision --add_client`: a re-run
regenerates the CA and invalidates every node already holding credentials. Override with
`NUM_SUPERNODES=<n>` (or `FLOWER_NUM_SUPERNODES=<n>` when calling the script directly).

> ⚠️ These are development credentials. Configure validity, organization or the server SANs
> (`SERVER_SAN_IPS`) by editing the globals at the top of
> [`provision/scripts/generate_creds.py`](provision/scripts/generate_creds.py); extra DNS SANs for a
> hub deployment go in `FLOWER_EXTRA_SERVER_SANS` without editing anything.

### Provisioning command

```bash
make -C fl-services/flower provision NET_NUMBER=1   # → provision/creds/net-1/
make -C fl-services/flower provision NET_NUMBER=2   # second net for the default dev stack

# A stag/prod kit: PROD selects the env file the slot pool is read from, and the
# SuperLink cert needs the hub's own names.
FLOWER_EXTRA_SERVER_SANS="fl-server-net-1.flip.local,fl.stag.flip.aicentre.co.uk" \
  make -C fl-services/flower provision NET_NUMBER=1 PROD=stag
```

`make generate-tls-certificates-net-1` / `-net-2` are direct shortcuts for `provision
NET_NUMBER=1` / `=2` — same script, same `NUM_SUPERNODES` derivation, just without having to pass
`NET_NUMBER` — handy when a Makefile or script targets one net by name.

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
keys. This is the Flower analogue of claiming a participant slot.

## The fleet proxy: what a trust can reach (FLIP#1517)

Since flwr 1.40 the SuperLink serves **every** API from one HTTP app on one port (8000): health,
the **Control API** (`/v1/control/*` — start, stop and list runs, register and unregister nodes),
the **Fleet API** (`/v1/fleet/*` — what a SuperNode calls) and the **Runtime API**
(`/v1/runtime/*` — the ServerApp's own routes for messaging SuperNodes). Open-source Flower
does not authenticate the Control API at all, and authenticates Runtime only behind a flag that
subprocess isolation (FLIP's mode) ignores; SuperNode auth signs Fleet requests alone. So a trust that could reach port 8000
could submit its own ServerApp, stop another project's run or forge instructions to other trusts'
SuperNodes — and there is no flag to serve Fleet on its own listener.

Trusts therefore never reach port 8000. They reach `flower-fleet-proxy`, an nginx that listens on
the trust-facing port 9092 with the SuperLink's own server certificate (so trust kits, SANs and
`FL_SERVER_PORT` are unchanged), forwards only the 12 Fleet routes in
[`fleet-proxy/fleet-routes.map`](fleet-proxy/fleet-routes.map) — POST only, matched on the method
and raw request URI — to the SuperLink over TLS, and answers everything else 404. When it cannot
reach or verify the SuperLink it answers 503, which the SuperNode retries, and its health check
(`fleet-proxy/healthcheck.sh`, also on the ECS sidecar) fails, since the NLB's TCP check alone would
still pass. The access log's `upstream=` field tells the two apart.

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

```bash
make -C fl-services/flower up                 # INSECURE stack (1 SuperLink + 2 SuperNodes + fl-api)
make -C fl-services/flower up-secure          # SECURE stack (TLS + SuperNode auth); needs `provision` first
make -C fl-services/flower submit APP=xray_classification   # submit a job to the running stack
make -C fl-services/flower down
```

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
