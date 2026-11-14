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

# Trust observability stack

Structured logging, collection and visualisation for the three Trust APIs (`trust-api`,
`imaging-api`, `data-access-api`), deployed alongside them at every Trust. Alloy also ships the stdout
of every other container on the host (see [What is collected](#what-is-collected)). Full reference:
[`docs/source/components/component-logging-stack.rst`](../../docs/source/components/component-logging-stack.rst).

## Components

| Component | Config here | Port | Role |
| --------- | ----------- | ---- | ---- |
| `log_config` | [`log_config/`](log_config/) | — | Shared Python library the three APIs import (via a bind mount, not installed as a package) to emit structured JSON logs to stdout and tag request lifecycle events. |
| Grafana Alloy | [`alloy/config.alloy`](alloy/config.alloy) | `12345` (internal) | Discovers containers via the Docker socket, parses their JSON stdout, forwards to Loki. (Successor to the end-of-life Promtail.) |
| Loki | [`loki/loki-config.yml`](loki/loki-config.yml) | `3100` (`LOKI_PORT`) | Log storage, filesystem-backed. |
| Grafana | [`grafana/provisioning/`](grafana/provisioning/) | `3000` (`GRAFANA_PORT`) | Dashboards + ad-hoc LogQL queries, pre-provisioned with a Loki datasource and a **Trust APIs** dashboard. |

`loki`, `alloy` and `grafana` are part of the trust's **base compose stack**
(`trust/deploy/compose/compose_trust.<env>.yml`). They are always loaded; there is no enable/disable flag.
They run alongside `trust-api`, `imaging-api`, `data-access-api`, `orthanc` and `omop-db`
(see [`trust/deploy/README.md`](../deploy/README.md)).

- **Dev:** each API's compose service bind-mounts [`log_config/`](log_config/) at `/app/log_config`.
  The Loki, Alloy and Grafana configs are bind-mounted read-only from this directory.
- **Production:** the three configs are read from
  `${OBSERVABILITY_CONFIG_DIR:-/opt/flip/config/observability}`.
- **Data:** Loki and Grafana persist to `${LOKI_DATA_VOLUME:-trust-local-loki-data}` and
  `${GRAFANA_DATA_VOLUME:-trust-local-grafana-data}`. By default these are named volumes, compose-prefixed on
  disk (e.g. `trust<N>_trust-local-loki-data`). An Ansible-provisioned EC2 or on-prem kit overrides them to
  bind paths under `/opt/flip/volumes/{loki,grafana}`. The trust Ansible plays (`trust/deploy/ansible/`)
  create those directories with the right ownership.

## What is collected

Each trust API writes single-line JSON to stdout via `log_config.configure_logging()` — no log
files are read from disk. `LoggingMiddleware` tags every request with `request.started` /
`request.completed` / `request.failed` events, a `request_id` (from `X-Request-ID` or a generated
UUID), `method`, `path`, `status_code` and `duration_ms`.

Alloy (`discovery.docker`) then:

1. Discovers **every** container on the host's Docker daemon. `config.alloy` applies no filter, so
   `orthanc`, `omop-db`, the fl-client, XNAT and anything else running there are shipped too. A non-JSON
   line carries only the container labels.
2. Extracts the `container`, `service` and `project` Docker labels.
3. In its `loki.process` stage, parses each JSON line and promotes `level`, `api` and `event` to Loki
   labels. `request_id` stays in the log body and is not promoted to a label.

The provisioned **Trust APIs**
Grafana dashboard (`grafana/provisioning/dashboards/trust-apis.json`) reads these labels for
request-rate, error-rate, p95-latency, status-code and log-stream panels, filterable by
`trust-api` / `data-access-api` / `imaging-api`.

## Retention

Loki's `limits_config.retention_period` is `720h` (30 days), enforced by the compactor
(`compaction_interval: 10m`, `retention_delete_delay: 2h`). Reduce it in
[`loki/loki-config.yml`](loki/loki-config.yml) if disk usage from `trust-local-loki-data` (or the
dev `loki-data` volume) becomes a concern.

## Reaching Grafana

Grafana listens on `GRAFANA_PORT` (default `3000`). Like every trust-internal service, it has no
inbound port open to the internet. On an AWS node, tunnel it over SSM. If you changed `GRAFANA_PORT`,
use that value for `portNumber`:

```bash
aws ssm start-session --target <instance-id> --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=3000,localPortNumber=3000   # then open http://localhost:3000
```

On a local dev stack, `GRAFANA_PORT` (and `LOKI_PORT`) are published straight to the host by
`trust/deploy/compose/compose_trust.development.yml`, so `http://localhost:3000` works directly with no
tunnel needed.

## Credentials

Grafana's admin password is `GRAFANA_ADMIN_PASSWORD` (`GF_SECURITY_ADMIN_PASSWORD` inside the
container). It is a **Trust-local credential**: set it per trust in the kit file `trust/.env.<CODE>.<env>`.
It is never sent to or stored on the Central Hub.

The template is `trust/.env.example` (dev default `admin`). See the kit walkthrough in
[`trust/README.md`](../README.md#one-time-setup), "Fill in the Trust-local credentials block".

## Configuration

`TRUST_LOG_LEVEL` (mapped to `LOG_LEVEL` inside each API container) sets the Python log level for
all three trust services. The default is `INFO`; the example dev env file sets `DEBUG`. It is read once
at startup, so restart the affected container(s) after changing it.

## Troubleshooting

- Nothing in Grafana: check the API containers are up, then `docker compose logs alloy` for
  connectivity to Loki, and that `/var/run/docker.sock` is mounted into the `alloy` container
  (required for container discovery).
- Loki health: `curl http://localhost:3100/ready` on the node (`LOKI_PORT`, default `3100`). From your
  machine, open an SSM tunnel to port `3100` the same way as for Grafana above.
- Ad-hoc queries: open the **Loki** datasource under **Explore** in Grafana, e.g. `{api="trust-api"}`,
  `{level="ERROR"}`, or `{api=~"trust-api|data-access-api|imaging-api"} |= "<request-id>"` to
  correlate a request across services.
