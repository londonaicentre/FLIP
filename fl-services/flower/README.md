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
Hub drives. `fl-base` holds the shared base image the others build on.

Unlike NVFLARE (which mints per-participant *startup kits*), Flower provisioning generates
**TLS certificates** for secure SuperLink↔SuperNode↔CLI communication and **SuperNode
authentication key pairs** — the Flower equivalent of the per-trust identity. See the
upstream how-tos for the underlying mechanics:
[TLS connections](https://flower.ai/docs/framework/how-to-enable-tls-connections.html) and
[SuperNode authentication](https://flower.ai/docs/framework/how-to-authenticate-supernodes.html).

## Images: built in CI, or locally as `:dev`

The Flower images — `flower-fl-base`, `flower-superlink`, `flower-supernode`, `flower-fl-api`
— are built in CI by [`fl-docker-build-flower.yml`](../../.github/workflows/fl-docker-build-flower.yml)
whenever `fl-services/flower/**` or `flip-utils/**` change, and published to GHCR as `:<sha>`,
`:stag` (on `develop`) and `:prod` (on `main`). Deployments pull them via `DOCKER_FL_TAG`.

To iterate locally **before pushing**, build them tagged `:dev`:

```bash
make -C fl-services/flower build                  # flwr-base first, then superlink/supernode/fl-api
make -C fl-services/flower build LOCAL_DEV=true   # include dev-only deps
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

Flower ≥1.32 installs an app's declared dependencies **at run time**: when a run starts, the
SuperLink (ServerApp side) and each SuperNode (ClientApp side, opted in with
`--allow-runtime-dependency-installation` in the composes) run `uv sync` against the FAB's
`pyproject.toml` into an isolated per-run environment, which is prepended to `sys.path`.

Left alone, that would resolve `flip-utils` from **PyPI**, silently shadowing the in-repo copy baked
into the images ([#767](https://github.com/londonaicentre/FLIP/issues/767)). Two `[tool.uv]` tables
in the `fl-apps/flower/*` template pyprojects steer the per-run resolution instead:

- `[tool.uv.sources] flip-utils = { path = "/opt/flip-utils" }` — `fl-base` keeps the in-repo
  flip-utils **source** at `/opt/flip-utils` after installing it, and every run builds flip-utils
  from that path. uv never consults PyPI for a name with a source override, so the platform always
  runs the flip-utils matching its images.
- `torch`/`torchvision` are pinned to PyTorch's cu128 index (PyPI's default cu130 wheels need
  NVIDIA driver ≥580; FLIP hosts run 575.x).

All other dependencies resolve from PyPI per run, so SuperLink/SuperNode hosts need outbound HTTPS
to PyPI and `download.pytorch.org`.

**Testing an unpublished flip-utils** therefore needs no PyPI release and no version bump: rebuild
the images from your branch (`make build-fl FL_BACKEND=flower` — the source lands at
`/opt/flip-utils`) and restart the flower services onto the rebuilt `:dev` images
(`make restart-fl FL_BACKEND=flower DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev` for the hub stack).
Every flip-utils change ships via an image rebuild — the containers run whatever their image bakes,
so confirm the running container carries your change by inspecting the file you edited
(`docker exec <supernode> cat /opt/flip-utils/flip/<changed file>`) before trusting a run.

> ⚠️ The pin lives in the **fl-apps templates**, so it covers hub-stack runs (flip-api bundles every
> uploaded app with a template). The standalone stacks below submit apps straight from
> `fl-tutorials/flower/`, bypassing the templates — and a tutorial pyproject **cannot** carry the
> same pin, because `/opt/flip-utils` does not exist on a workstation and `uv sync` then fails
> outright (breaking local linting of the tutorial). The tutorials therefore **do not declare
> flip-utils at all**: with nothing to install, the per-run environment has no `flip`, and
> `import flip` falls through the prepended per-run path to the image's baked-in copy — the same
> flip-utils the platform runs.
>
> Declaring it is the trap: `uv sync` would resolve `flip-utils` from **PyPI** (currently 0.1.8,
> against the repo's 0.4.0) into the per-run environment, which is prepended to `sys.path` and so
> shadows the image copy. PyPI 0.1.8 has no `flip/flower/strategy.py`, so a declared dependency
> breaks `from flip.flower.strategy import FlipFedAvg` before training starts. Either way, a
> flip-utils change reaches the tutorials only via an image rebuild
> (`make build-fl FL_BACKEND=flower`).

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
└── keys/           supernode_credentials_<i>{,.pub}          # SuperNode auth key pairs (2 by default)
```

> ⚠️ These are development credentials. Configure validity, organization, the server SANs
> (`SERVER_SAN_IPS`), or the number of SuperNode key pairs by editing the globals at the top
> of [`provision/scripts/generate_creds.py`](provision/scripts/generate_creds.py).

### Provisioning command

```bash
make -C fl-services/flower provision NET_NUMBER=1   # → provision/creds/net-1/
make -C fl-services/flower provision NET_NUMBER=2   # second net for the default dev stack
```

`creds/` is the gitignored `FL_PROVISIONED_DIR` for Flower (see
[`deploy/fl_backend.mk`](../../deploy/fl_backend.mk)); the dev compose overlays and the standalone
[`compose.secure.yml`](compose.secure.yml) mount `certificates/` into `/certs` and `keys/` into
`/keys`.

### How SuperNodes are authenticated

The SuperLink only accepts SuperNodes whose **public** key it has been told about. Each
SuperNode's `.pub` key is registered with the SuperLink (and, in the hub, mapped to a trust name)
by [`register-supernode-keys.sh`](register-supernode-keys.sh) — it runs `flwr supernode register`
for every `/keys/*.pub`, skipping already-registered keys. This is the Flower analogue of claiming
a participant slot.

## Running a network standalone (no hub / trusts)

```bash
make -C fl-services/flower up                 # INSECURE stack (1 SuperLink + 2 SuperNodes + fl-api)
make -C fl-services/flower up-secure          # SECURE stack (TLS + SuperNode auth); needs `provision` first
make -C fl-services/flower submit APP=numpy   # submit a job to the running stack
make -C fl-services/flower down
```

The central-hub multi-net Flower topology is the separate
[`deploy/compose.development.flower.yml`](../../deploy/compose.development.flower.yml), driven by the
root `make up FL_BACKEND=flower`.

## Diagnosing a run that fails after submission

A Flower run whose ServerApp dies logs almost nothing where you would look for it: the SuperLink
reports `Started task` and `Finished task` for the run in the same breath and says no more, and no
container's `docker logs` carries the traceback. The run's own log lives in the SuperLink, reachable
only through the Control API.

The Central Hub polls for this (FLIP#1001): a job the FL API reports as failed drives the model to
`ERROR` and the tail of the run log is written to the model's activity feed, so the usual first stop
is the model page rather than a shell. To read the full stream by hand — or to inspect a run
submitted outside the hub, e.g. via `make submit` — exec into the net's FL API container:

```bash
docker exec -it flip-fl-api-net-1 uvx flwr log <run-id> local --show     # hub multi-net stack
docker compose -f fl-services/flower/compose.dev.yml exec fl-api \
    uvx flwr log <run-id> local --show                                   # standalone dev stack
```

`--show` prints the stored log and exits; the `flwr log` default (`--stream`) follows it forever.
Run ids come from `uvx flwr list local` in the same container, or from the `run-id` the submit
returned. A ServerApp that died at import time ends with a traceback and `ERROR: Exit Code: 607`.
