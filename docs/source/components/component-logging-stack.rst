###############
Logging Stack
###############

Every Trust runs a logging stack that collects, stores and displays its containers' logs, under Docker Compose
or from the Kubernetes Helm chart. The logs never leave the Trust. This page is for Trust operators and engineers
who need to find out what a Trust's services did.

The stack has three layers:

1. **Structured logs** — each Trust API writes one JSON object per log line.
2. **Grafana Alloy + Loki** — Alloy collects every container's logs and Loki stores them for 30 days. Only the
   Trust APIs' JSON lines get the ``level``, ``api`` and ``event`` labels.
3. **Grafana** — a web dashboard for querying and viewing the logs.

.. contents:: On this page
   :local:
   :depth: 2

************
Architecture
************

.. code-block:: text

   ┌─────────────┐  ┌──────────────────┐  ┌─────────────┐
   │  trust-api   │  │  data-access-api  │  │  imaging-api │
   │  (JSON logs  │  │  (JSON logs       │  │  (JSON logs  │
   │   to stdout) │  │   to stdout)      │  │   to stdout) │
   └──────┬───────┘  └────────┬──────────┘  └──────┬───────┘
          │                   │                     │
          └───────────┬───────┘─────────────────────┘
                      ▼
              ┌───────────────┐
              │ Grafana Alloy │  Scrapes Docker logs via socket
              │ (port 12345)  │  Parses JSON, extracts labels
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │     Loki      │  Stores logs with labels
              │  (port 3100)  │  30-day retention
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │    Grafana    │  Query & visualise
              │  (port 3000)  │
              └───────────────┘

Each API writes its logs to standard output, where Docker captures them. Alloy finds the containers through the
Docker socket, parses the JSON and sends the logs to Loki. Grafana reads from Loki through a pre-configured data
source.

*******************
Application Logging
*******************

Log output format
=================

Every log line is a JSON object:

.. code-block:: json

   {
     "timestamp": "2025-06-15T10:23:45.123456+00:00",
     "level": "INFO",
     "api": "imaging-api",
     "logger": "log_config.middleware",
     "message": "Request completed",
     "request_id": "d4e5f6a7-...",
     "event": "request.completed",
     "method": "POST",
     "path": "/download/images/net-1",
     "status_code": 200,
     "duration_ms": 412
   }

Every HTTP request is logged automatically:

- a ``request_id`` is taken from the ``X-Request-ID`` header, or generated, and added to every log line written
  while handling that request;
- start, completion and failure are logged as the events ``request.started``, ``request.completed`` and
  ``request.failed``, with the method, path, status code and duration.

Structured events
=================

Tag a log line with an ``event`` field, using a dotted name, so that it can be filtered the same way in every
service:

.. code-block:: python

   logger.info("Project approved", extra={"event": "project.approved", "project_id": pid})

.. note::

   **For maintainers:** the three APIs share the ``log_config`` library in ``trust/observability/log_config/``.
   Each service sets it up once at start-up with its own name and its ``LOG_LEVEL`` setting. See the
   :gh:`observability README <trust/observability/README.md>`.

*************************
Infrastructure Components
*************************

Grafana Alloy
=============

Alloy collects container logs through the Docker socket. It replaces Promtail, which is end-of-life. It:

- looks for new containers every 5 seconds;
- labels each log with its Docker ``container``, ``service`` and ``project``;
- parses JSON log lines and turns ``level``, ``api`` and ``event`` into Loki labels, so queries on them are
  fast. ``request_id`` stays in the log body and is not a label.

Its configuration is ``trust/observability/alloy/config.alloy``.

On Kubernetes, Alloy reads the pod log files under ``/var/log/pods`` on the node instead, and labels each log
with its ``namespace``, ``pod``, ``container`` and ``service`` (the namespace). The JSON parsing is the same.

Loki
====

Loki stores the logs on the local filesystem. Its configuration is ``trust/observability/loki/loki-config.yml``.

.. list-table::
   :header-rows: 1

   * - Setting
     - Value
   * - Retention period
     - 720 hours (30 days)
   * - Schema version
     - v13 (TSDB)
   * - Storage backend
     - Local filesystem
   * - Index rotation
     - 24 hours
   * - Compaction interval
     - 10 minutes

Grafana
=======

Grafana is the web UI for exploring logs. It starts with a Loki data source and a **Trust APIs** dashboard
already set up, so no manual configuration is needed. Under Compose it is at ``http://<trust-host>:3000`` (or
``GRAFANA_PORT``). Sign in as ``admin`` with ``GRAFANA_ADMIN_PASSWORD`` from the Trust's kit file.

On Kubernetes, Grafana has no external address; port-forward it from a machine with cluster access:

.. code-block:: shell

   kubectl -n <namespace> port-forward svc/grafana 3000:3000

Trust APIs dashboard
--------------------

The **Trust APIs** dashboard, in the **Observability** folder, gives an overview of the three Trust APIs:

- **Stat panels** — request rate, error count, p95 latency, active APIs
- **Time series** — request rate and error rate by API over time
- **p95 request duration** — latency trends by API
- **Status code distribution** — breakdown of HTTP response codes
- **Slowest requests** — completed requests sorted by duration
- **Recent errors** — ``ERROR``-level log entries
- **All logs** — the full log stream, filterable by label

Use the **API** dropdown at the top to show ``trust-api``, ``data-access-api``, ``imaging-api``, or all three.

*************
Configuration
*************

Environment variables
=====================

Set these in the Trust's kit file (``trust/.env.<CODE>.<env>``):

.. list-table::
   :header-rows: 1

   * - Variable
     - Service
     - Description
   * - ``TRUST_LOG_LEVEL``
     - All three APIs (as ``LOG_LEVEL`` inside each container)
     - Python log level for the Trust APIs. The kit template sets ``DEBUG``. On Kubernetes the chart's
       ``logLevel`` value sets it instead (default ``INFO``).
   * - ``GRAFANA_PORT``
     - Grafana
     - Host port for the Grafana UI (default ``3000``)
   * - ``GRAFANA_ADMIN_PASSWORD``
     - Grafana
     - Admin password for Grafana
   * - ``LOKI_PORT``
     - Loki
     - Host port for the Loki API (default ``3100``)

Docker Compose services
=======================

The stack is part of the Trust's Compose files: ``trust/deploy/compose_trust.development.yml`` (configurable
ports) and ``trust/deploy/compose_trust.prod.yml`` (persistent data, automatic restart). It adds three
services, with the same images in the Helm chart (``observability.enabled`` turns all three off):

.. list-table::
   :header-rows: 1

   * - Service
     - Image
     - Role
   * - ``loki``
     - ``grafana/loki:3.4.0``
     - Log storage
   * - ``alloy``
     - ``grafana/alloy:v1.9.0``
     - Log collector (starts after Loki)
   * - ``grafana``
     - ``grafana/grafana:11.5.0``
     - Dashboard (starts after Loki)

In production, Loki and Grafana keep their data in persistent volumes. On hosts provisioned with Ansible (AWS
EC2 or on-prem), these are ``/opt/flip/volumes/loki`` and ``/opt/flip/volumes/grafana``.

****************************
Querying logs and operations
****************************

Accessing Grafana
=================

1. Open Grafana in a browser: ``http://<trust-host>:3000`` under Compose, or the port-forward above on
   Kubernetes.
2. Sign in as ``admin``.
3. Open the **Trust APIs** dashboard in the **Observability** folder for an overview, or go to **Explore** and
   choose the **Loki** data source for your own queries.

Example LogQL queries
=====================

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - To find
     - Query
   * - All logs from one API
     - ``{api="trust-api"}``
   * - Errors only
     - ``{level="ERROR"}``
   * - Requests that raised an unhandled error
     - ``{event="request.failed"}``
   * - Text within one service
     - ``{api="data-access-api"} |= "timeout"``
   * - One request across services
     - ``{api=~"trust-api|data-access-api|imaging-api"} |= "d4e5f6a7-..."``

***************
Troubleshooting
***************

Logs not appearing in Grafana
=============================

On a Compose Trust host, where ``<N>`` is the Trust's slot number:

1. Check that the API containers are running:

   .. code-block:: shell

      sudo docker ps --filter name=trust<N>-

2. Check that Alloy can reach Loki:

   .. code-block:: shell

      sudo docker logs trust<N>-alloy-1

3. Check that the Docker socket (``/var/run/docker.sock``) is mounted into the Alloy container. Alloy cannot
   find containers without it.
4. Check that Loki is healthy:

   .. code-block:: shell

      curl http://localhost:3100/ready

High disk usage from Loki
=========================

Loki keeps logs for 30 days. If disk space is tight:

- lower ``retention_period`` in ``trust/observability/loki/loki-config.yml``;
- check that the compactor is running (it runs every 10 minutes);
- watch the size of Loki's data volume (``/opt/flip/volumes/loki`` on Ansible-provisioned hosts).

Changing the log level
======================

The log level is read only when a container is created. Update ``TRUST_LOG_LEVEL`` in the trust's env file,
then recreate the affected container with the same compose files and env file the stack was started with:

.. code-block:: shell

   docker compose up -d --force-recreate trust-api

.. note::

   ``docker compose restart`` is not enough: it restarts the existing container with its old environment.
