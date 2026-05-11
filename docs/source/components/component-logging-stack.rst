###############
Logging Stack
###############

FLIP uses a structured logging stack at each Trust site to collect, store and
visualise application logs. The stack consists of three layers:

1. **log_config** -- a shared Python library that emits structured JSON logs.
2. **Grafana Alloy + Loki** -- Docker-native log collection and storage with
   30-day retention.
3. **Grafana** -- a web dashboard for querying and visualising logs.

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

Each FLIP API service writes single-line JSON to stdout. Docker captures this
output. Grafana Alloy discovers containers via the Docker socket, parses the
JSON and forwards the logs to Loki. Grafana queries Loki through a
pre-provisioned datasource.

*******************
Application Logging
*******************

Shared library: ``log_config``
================================

All trust-side APIs use the ``log_config`` library located at
``trust/observability/log_config/``. The library provides:

- **JSONFormatter** -- serialises every log record as a single-line JSON object
  containing ``timestamp``, ``level``, ``api``, ``logger``,
  ``message`` and any extra fields.
- **LoggingMiddleware** -- FastAPI/Starlette middleware that generates a
  ``request_id`` (from the ``X-Request-ID`` header or a UUID), logs
  ``request.started`` / ``request.completed`` / ``request.failed`` events and
  records ``method``, ``path``, ``status_code`` and ``duration_ms``.
- **request_context** -- a ``contextvars.ContextVar`` that carries per-request
  fields (e.g. ``request_id``) into every log emitted during that request.

Initialisation
--------------

Each API initialises logging in its ``utils/logger.py`` module using values
from the service's Pydantic ``Settings`` class:

.. code-block:: python

   from log_config import configure_logging, get_logger
   from trust_api.config import get_settings

   _settings = get_settings()

   configure_logging(
       api_name="trust-api",
       level=_settings.LOG_LEVEL,
   )

   logger = get_logger(__name__)

The relevant setting is:

.. list-table::
   :header-rows: 1

   * - Setting
     - Default
     - Description
   * - ``LOG_LEVEL``
     - ``INFO``
     - Python log level applied uniformly to all trust services. Set to
       ``DEBUG`` in development, ``INFO`` in staging/production.

This is set via environment variables or ``.env.*`` files and read through
each service's Pydantic ``Settings`` class.

Structured events
-----------------

Use the ``event`` extra field with dotted string names for consistent log
tagging across services. The ``LoggingMiddleware`` automatically tags request
lifecycle events (``request.started``, ``request.completed``,
``request.failed``).

.. code-block:: python

   logger.info("Project approved", extra={"event": "project.approved", "project_id": pid})

Log output format
-----------------

Every log line is a JSON object:

.. code-block:: json

   {
     "timestamp": "2025-06-15T10:23:45.123456Z",
     "level": "INFO",
     "api": "trust-api",
     "logger": "trust_api.routers.cohort",
     "message": "Project approved",
     "event": "project.approved",
     "project_id": "abc-123",
     "request_id": "d4e5f6a7-..."
   }

*************************
Infrastructure Components
*************************

Grafana Alloy
=============

Grafana Alloy discovers containers via the Docker socket and scrapes their
stdout logs. Configuration is at ``trust/observability/alloy/config.alloy`` (River syntax).
Alloy replaces the now end-of-life Promtail collector.

Key behaviours:

- Discovers containers every 5 seconds via ``discovery.docker``.
- Extracts Docker labels as log labels: ``container``, ``service``,
  ``project`` (via ``discovery.relabel``).
- Parses JSON log lines and promotes ``level``, ``api`` and ``event``
  to Loki labels for efficient querying (via ``loki.process`` with
  ``stage.json`` and ``stage.labels``). ``request_id`` is extracted
  from the JSON but not promoted to a label.

Loki
====

Loki is the log storage backend. Configuration is at
``trust/observability/loki/loki-config.yml``.

Key settings:

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

Grafana provides the web UI for log exploration. It is pre-provisioned with a
Loki datasource and a **Trust APIs** dashboard so no manual configuration is
required on first start.

Provisioning files are located at
``trust/observability/grafana/provisioning/``:

- ``datasources/loki.yml`` -- Loki datasource (uid: ``loki``)
- ``dashboards/dashboards.yml`` -- dashboard provider configuration
- ``dashboards/trust-apis.json`` -- Trust APIs overview dashboard

Default credentials and port:

- **URL**: ``http://<trust-host>:3000``
- **Admin password**: set via ``GRAFANA_ADMIN_PASSWORD`` environment variable

Trust APIs dashboard
--------------------

The provisioned **Trust APIs** dashboard (under the **Observability** folder)
provides an overview of all three trust API services. It includes:

- **Stat panels** -- request rate, error count, p95 latency, active APIs
- **Time series** -- request rate and error rate by API over time
- **p95 request duration** -- latency trends by API
- **Status code distribution** -- breakdown of HTTP response codes
- **Slowest requests** -- table of completed requests sorted by duration
- **Recent errors** -- filtered log view of ``ERROR``-level entries
- **All logs** -- full log stream with label filtering

An **API** dropdown at the top of the dashboard allows filtering by
``trust-api``, ``data-access-api``, ``imaging-api``, or all three.

*************
Configuration
*************

Environment variables
=====================

The following environment variables control the logging stack. Set them in the
appropriate ``.env.*`` file or pass them directly in the Docker Compose
override.

.. list-table::
   :header-rows: 1

   * - Variable
     - Service
     - Description
   * - ``TRUST_LOG_LEVEL``
     - All APIs (mapped to ``LOG_LEVEL`` inside each container)
     - Sets the Python log level uniformly across all trust services.
       Defaults to ``DEBUG`` in development, ``INFO`` in staging/production.
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

The logging infrastructure is defined in the trust-level Docker Compose files:

- ``trust/compose_trust.development.yml`` -- development overrides with
  configurable ports
- ``trust/compose_trust.production.yml`` -- production settings with persistent
  volumes and automatic restart

Three services are added:

1. **loki** (``grafana/loki:3.4.0``) -- log storage
2. **alloy** (``grafana/alloy:v1.9.0``) -- log collector (depends on loki)
3. **grafana** (``grafana/grafana:11.5.0``) -- dashboard (depends on loki)

In production, persistent volumes are mounted at:

- ``/opt/flip/volumes/loki`` -- Loki data
- ``/opt/flip/volumes/grafana`` -- Grafana data and configuration

**************************
Operations & Querying Logs
**************************

Accessing Grafana
=================

1. Open ``http://<trust-host>:3000`` in a browser.
2. Log in with the admin credentials.
3. Open the **Trust APIs** dashboard from the **Observability** folder for an
   overview of all API services, or navigate to **Explore** and select the
   **Loki** datasource for ad-hoc queries.

Example LogQL queries
=====================

All logs from a specific API:

.. code-block:: text

   {api="trust-api"}

Errors only:

.. code-block:: text

   {level="ERROR"}

Logs for a specific event:

.. code-block:: text

   {event="training.failed"}

Full-text search within a service:

.. code-block:: text

   {api="data-access-api"} |= "timeout"

Correlate logs by request ID:

.. code-block:: text

   {api=~"trust-api|data-access-api|imaging-api"} |= "d4e5f6a7-..."

***************
Troubleshooting
***************

Logs not appearing in Grafana
=============================

1. Check that the API containers are running: ``docker compose ps``.
2. Check Alloy can reach Loki: ``docker compose logs alloy``.
3. Verify Alloy has access to the Docker socket
   (``/var/run/docker.sock`` must be mounted).
4. Check Loki is healthy: ``curl http://localhost:3100/ready``.

High disk usage from Loki
=========================

Loki retains logs for 30 days. If disk space is a concern:

- Reduce ``retention_period`` in ``trust/observability/loki/loki-config.yml``.
- Check that the compactor is running (``compaction_interval: 10m``).
- Monitor the ``/opt/flip/volumes/loki`` directory size.

Changing log level at runtime
=============================

``LOG_LEVEL`` is read at service startup. To change the level, update the
environment variable and restart the affected container:

.. code-block:: bash

   docker compose restart trust-api
