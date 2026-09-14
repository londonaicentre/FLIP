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

# Trust Compose Stack

> **Deploys: trust only.** These files define the trust-side container stack. The Central Hub stack lives in
> [`deploy/`](../../deploy/README.md) at the repo root; infrastructure provisioning (AWS / on-prem / K8s)
> lives in [`deploy/providers/`](../../deploy/providers/README.md).

Do not run these files directly — they are driven by [`trust/Makefile`](../Makefile), which selects the right
combination for the environment, FL backend, GPU availability and kit slot. Start here instead:

```bash
make -C trust up-trust KIT=GSTT              # dev
make -C trust up-trust KIT=<CODE> PROD=true  # against a remote hub
```

## File matrix

`trust/Makefile` composes the final `docker compose` invocation from up to **four** of these — base +
backend overlay + GPU + `Trust_1` override, via `up-trust` (and identically `up-fl-clients-kit`). The debug
path is a separate three-file command that never picks up the GPU or `Trust_1` overlays, and
`compose.test.yml` is never combined with any of them. `<env>` is `development` or `production`, selected
by `PROD` (`true`/`stag` → `production`).

| File | Role | When applied |
| ---- | ---- | ------------ |
| `compose_trust.<env>.yml` | **Base stack** — `trust-api`, `imaging-api`, `data-access-api`, `orthanc`, `omop-db`, plus the Loki/Alloy/Grafana observability trio | Always (`COMMON_COMPOSE_FILE`) |
| `compose_trust.<env>.nvflare.yml` | FL client overlay for the NVFLARE backend | Always, backend-selected (`FL_BACKEND_COMPOSE_FILE`) |
| `compose_trust.<env>.flower.yml` | FL client overlay for the Flower backend | Always, backend-selected |
| `compose_trust.<env>.gpu.yml` | NVIDIA device reservation for the fl-client | Only when the kit sets `NUM_AVAILABLE_GPUS > 0` (`GPU_OVERRIDE`). Never applied by `up-trust-ec2` — the EC2 test host is GPU-less |
| `compose_trust-1_override.yml` | Publishes the trust APIs on host ports | Dev only, and only for the trust holding FL slot `Trust_1` (`TRUST_OVERRIDE`) — the override binds one fixed port set, so exactly one trust can claim it |
| `compose_trust.development.debug.override.yml` | debugpy ports + `DEBUG` env on the three APIs | `make debug-trust-api` / `debug-imaging-api` / `debug-data-access-api`. **Dev only** — there is no `production` variant, and the filename is hardcoded rather than `<env>`-interpolated, so these targets do not work under `PROD` |
| `compose.test.yml` | Integration-test stack (`omop-db-test` + `data-access-api-test`) | Testcontainers from pytest, and CI (`test_trust_{trust_api,data_access_api}.yml`). Not part of any `make up` path |

Dev pulls repo-built images from GHCR by default (`pull_policy: always`); `BUILD=true` rebuilds from the
`build:` blocks instead. Production is images-only, with no source bind-mounts.

## Why these live under `trust/`, not `deploy/`

A compose file belongs next to the source tree whose images it builds — the same rule that puts
`fl-services/<backend>/compose.dev.yml` and `trust/xnat/docker-compose-stack.yml` where they are. These files
were deliberately relocated here from `trust/` — one level down, into `trust/deploy/` — in commit
`72c6354b`. They have never lived under the repo-root `deploy/`.

**The relative paths inside them (`./trust-api`, `./observability/`, `./omop-db/volumes/`, …) resolve from
`trust/`, not from this directory.** That works because every caller passes `--project-directory`:

```bash
# trust/Makefile
docker compose --env-file <kit> --project-directory . -f deploy/compose_trust.<env>.yml ...

# trust/<service>/Makefile
docker compose --project-directory .. -f ../deploy/compose_trust.development.yml ...
```

If you ever see a build context that looks wrong by one level, **the fix is the `--project-directory` flag on
the caller, not rewriting `./X` to `../X` in these files.** Rewriting them would break every other caller.

`compose.test.yml` is the one exception: Testcontainers runs it with the context set to `trust/deploy/`, so
its paths are genuinely one level up (`../trust-api`, `../data-access-api`, `../observability`).

## Networks

**Neither the trust stack nor the hub stack creates these networks.** Both declare them `external: true`,
so both *fail* on a missing network rather than creating one. `make -C trust create-networks` is what
creates all of them — the repo-root `make create-networks` forwards to it:

| Network | Created by | Carries |
| ------- | ---------- | ------- |
| `${TRUST_NETWORK_NAME}` (`deploy_trust-network-<N>`) | `make -C trust create-networks` (`create-networks-trust-1` / `-2`) | this trust's own services |
| `deploy_central-hub-trust-apis-network` | `make -C trust create-networks` (`create-networks-core`) | trust-api ↔ flip-api |
| `deploy_fl-net-1`, `deploy_fl-net-2` | `make -C trust create-networks` (`create-networks-core`) | fl-client ↔ fl-server (FL data plane) |

So if you hit `network deploy_fl-net-1 declared as external, but could not be found`, the fix is
`make -C trust create-networks` — starting the hub stack cannot help, and a remote trust operator has no
hub compose on the host at all.

Every hub-shared network follows one rule: `<hub compose project>_<name>`, i.e. `deploy_…` by default and
`<instance>-deploy_…` when `FLIP_INSTANCE` is set. The `deploy_` was once a fossil — it is what Compose
generated back when the hub created these networks itself — but it now names the hub project that owns
them, which is also what Compose would still generate if they were not `external:`. Both sides pin the real
name with `name:`, because an external network is resolved by its literal name and is never scoped to the
project joining it, so the two sides must agree on a string. That is also why the instance prefix exists at
all: a trust is a separate Compose project and cannot derive the hub's network names from its own.

The two axes are numbered differently and are **not** parallel: `deploy_fl-net-<N>` is numbered by FL net,
`deploy_trust-network-<N>` by the trust's FL kit slot. A trust on slot 2 training on FL net 1 sits on
`deploy_trust-network-2` and `deploy_fl-net-1` at once.

Note the FL data plane carries **no hub API**: `flip-api` is deliberately absent from `deploy_fl-net-<N>`,
so an fl-client — which holds no Central Hub credential by design — has no route to it. The fl-server
reaches `flip-api` over the hub-internal network instead.

Bring the networks up before the stack — `up-trust` depends on `create-networks` for exactly this reason.
