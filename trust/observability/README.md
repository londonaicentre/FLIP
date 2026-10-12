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
`imaging-api`, `data-access-api`), deployed alongside them at every Trust. Full reference:
[`docs/source/components/component-logging-stack.rst`](../../docs/source/components/component-logging-stack.rst).

## Components

| Component | Config here | Port | Role |
| --------- | ----------- | ---- | ---- |
| `log_config` | [`log_config/`](log_config/) | — | Shared Python library the three APIs import (via a bind mount, not installed as a package) to emit structured JSON logs to stdout and tag request lifecycle events. |
| Grafana Alloy | [`alloy/config.alloy`](alloy/config.alloy) | `12345` (internal) | Discovers containers via the Docker socket, parses their JSON stdout, forwards to Loki. Replaces the now end-of-life Promtail. |
| Loki | [`loki/loki-config.yml`](loki/loki-config.yml) | `3100` (`LOKI_PORT`) | Log storage, filesystem-backed. |
| Grafana | [`grafana/provisioning/`](grafana/provisioning/) | `3000` (`GRAFANA_PORT`) | Dashboards + ad-hoc LogQL queries, pre-provisioned with a Loki datasource and a **Trust APIs** dashboard. |

These three services (`loki`, `alloy`, `grafana`) are part of the trust's **base compose stack**
(`trust/deploy/compose_trust.<env>.yml`, always loaded — there is no separate enable/disable flag)
alongside `trust-api`, `imaging-api`, `data-access-api`, `orthanc` and `omop-db`
(`trust/deploy/README.md`). In dev, each API's compose service bind-mounts
[`log_config/`](log_config/) at `/app/log_config`; the observability trio's own configs are bind-
mounted read-only the same way. In production the same three configs are read from
`${OBSERVABILITY_CONFIG_DIR:-/opt/flip/config/observability}` instead, and Loki/Grafana data
persist in `${LOKI_DATA_VOLUME:-trust-local-loki-data}` / `${GRAFANA_DATA_VOLUME:-trust-local-grafana-data}`
— named volumes by default (compose-prefixed on disk, e.g. `trust<N>_trust-local-loki-data`), which
an Ansible-provisioned EC2/on-prem kit overrides to bind paths under `/opt/flip/volumes/{loki,grafana}`,
directories `deploy/providers/AWS/site.yml` creates with the right ownership.

## What is collected

Each trust API writes single-line JSON to stdout via `log_config.configure_logging()` — no log
files are read from disk. `LoggingMiddleware` tags every request with `request.started` /
`request.completed` / `request.failed` events, a `request_id` (from `X-Request-ID` or a generated
UUID), `method`, `path`, `status_code` and `duration_ms`. Alloy discovers **every** container on the
host's Docker daemon via `discovery.docker` — `config.alloy` applies no filter, so `orthanc`,
`omop-db`, the fl-client, XNAT and anything else running there ship too, a non-JSON line carrying
only the container labels — extracts `container` / `service` / `project` Docker labels, and its
`loki.process` stage parses each JSON line and promotes `level`, `api` and `event` to Loki labels
(`request_id` is kept in the log body but not promoted to a label). The provisioned **Trust APIs**
Grafana dashboard (`grafana/provisioning/dashboards/trust-apis.json`) reads these labels for
request-rate, error-rate, p95-latency, status-code and log-stream panels, filterable by
`trust-api` / `data-access-api` / `imaging-api`.

## Retention

Loki's `limits_config.retention_period` is `720h` (30 days), enforced by the compactor
(`compaction_interval: 10m`, `retention_delete_delay: 2h`). Reduce it in
[`loki/loki-config.yml`](loki/loki-config.yml) if disk usage from `trust-local-loki-data` (or the
dev `loki-data` volume) becomes a concern.

## Reaching Grafana

Grafana listens on `GRAFANA_PORT` (default `3000`); like every trust-internal service it has no
inbound port opened to the internet — reach it the same way as the other Trust UIs, over SSM.
[`deploy/providers/AWS/scripts/forward-trust-all.sh`](../../deploy/providers/AWS/scripts/forward-trust-all.sh)
opens an `aws ssm start-session --document-name AWS-StartPortForwardingSession` tunnel for Grafana
(port `3000`) alongside XNAT, Orthanc, and the three API Swagger UIs, in one command:

```bash
make -C deploy/providers/AWS forward-trust   # then open http://localhost:3000
```

(The make target, not the script directly: its first line is `terraform output -raw
TrustEc2InstanceId`, which only resolves from `deploy/providers/AWS/`.)

On a local dev stack, `GRAFANA_PORT` (and `LOKI_PORT`) are published straight to the host by
`trust/deploy/compose_trust.development.yml`, so `http://localhost:3000` works directly with no
tunnel needed.

## Credentials

Grafana's admin password is `GRAFANA_ADMIN_PASSWORD` (`GF_SECURITY_ADMIN_PASSWORD` inside the
container). It is a **Trust-local credential** — set per trust in its kit file
`trust/.env.<CODE>.<env>` (`trust/.env.example` is the template, dev default `admin`; see
`trust/README.md`'s kit walkthrough, "Fill in the Trust-local credentials block") and never sent
to or stored on the Central Hub.

## Configuration

`TRUST_LOG_LEVEL` (mapped to `LOG_LEVEL` inside each API container) sets the Python log level
uniformly across all three trust services — Pydantic `Settings` default `INFO`, overridden to
`DEBUG` in the example dev env file. Changing it requires restarting the affected container(s); it
is read once at process startup.

## Troubleshooting

- Nothing in Grafana: check the API containers are up, then `docker compose logs alloy` for
  connectivity to Loki, and that `/var/run/docker.sock` is mounted into the `alloy` container
  (required for container discovery).
- Loki health: `curl http://localhost:3100/ready` (or over the SSM tunnel).
- Ad-hoc queries: open the **Loki** datasource under **Explore** in Grafana, e.g. `{api="trust-api"}`,
  `{level="ERROR"}`, or `{api=~"trust-api|data-access-api|imaging-api"} |= "<request-id>"` to
  correlate a request across services.
