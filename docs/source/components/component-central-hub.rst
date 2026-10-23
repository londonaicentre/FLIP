.. _flip-central-hub:

###########
Central Hub
###########

The Central Hub is the cloud-hosted half of FLIP. This page describes its services and how the Trusts and FL
servers talk to it. It is for engineers deploying, operating or integrating with the hub.

The hub runs:

- the **web UI** that researchers and administrators use;
- the **central API**, which owns projects, users, cohort queries and models;
- the **hub side of every FL net**;
- the managed cloud services that hold the hub's state.

It is the only internet-facing part of the platform, and every Trust connects to it outbound. It holds **no
clinical data**: cohort results arrive as aggregate statistics, and training leaves behind model weights and
metrics, never patient rows.

.. contents:: On this page
   :local:
   :depth: 2

********
Services
********

flip-ui
=======

A Vue 3 single-page application. It sends every request to ``flip-api`` with the user's access token and signs
the user out if the token is rejected. In development it runs as a dev-server container. In a deployed hub it is
static files in S3 served through CloudFront; there is no UI container.

flip-api
========

A FastAPI application serving everything under ``/api``. Its route groups map onto the platform's domains:

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Prefix
     - Purpose
   * - ``/projects``
     - Project lifecycle: create, edit, stage, approve or decline per Trust, unstage, delete; imaging status and
       the cohort each Trust froze at approval.
   * - ``/cohort``
     - Submit a cohort query to the project's Trusts and read back the aggregated results.
   * - ``/model``, ``/models``
     - Models under a project, their job types, metrics, logs and per-Trust status.
   * - ``/files``
     - Model-file upload (presigned POST), scan status, listing, download, and federated results download.
   * - ``/fl``
     - Training start and stop, net status, and the deployment-mode quiesce report.
   * - ``/users``, ``/roles``, ``/site``
     - Users, access requests, MFA status and roles; site banner and deployment mode.
   * - ``/trust``, ``/admin/trusts``
     - Trust roster, connection health and registration; a Trust's project decisions, for its Trust Admin.
   * - ``/step``
     - Multi-step workflows the UI drives: approve a project and start imaging at its Trusts, submit a cohort
       query to them, register a user, and retrieve a model.
   * - ``/tasks`` and callbacks
     - The endpoints Trusts and FL servers call — see `Talking to the Trusts`_ and `Callbacks from the FL server`_.

``GET /api/health`` is unauthenticated and reports the hub's release. Interactive API docs (``/api/docs``) are
served in local development only; staging and production turn them off.

FL nets, hub side
=================

Each FL net adds an FL API and an FL server to the hub (``fl-api-net-N`` and ``fl-server-net-N``). ``flip-api``
submits jobs through the FL API; the Trusts' FL clients connect to the FL server. Both are described on
:doc:`component-fl-nets`.

PostgreSQL
==========

The hub's own database. It holds projects, cohort queries and their results, models, jobs, the FL scheduler
state, the Trust roster, the task queue for Trusts, and roles and permissions. The roster stores **hashes** of
the Trust API keys, never the keys. Database migrations run at every start-up (see `Start-up`_).

**********
Deployment
**********

The hub runs on AWS, provisioned with Terraform/OpenTofu, in one of two modes:

- **self-contained** in a single AWS account;
- on a `Landing Zone Accelerator <https://aws.amazon.com/solutions/implementations/landing-zone-accelerator-on-aws/>`_
  estate, where a platform team owns the network and the edge.

The services on this page are the same in both modes; only where they sit and how traffic reaches them differs.

.. seealso::

   :ref:`deploy-central-hub` has the architecture diagrams and runbooks, one page per mode
   (:doc:`/deploy-flip/deploy-central-hub-aws` and :doc:`/deploy-flip/deploy-central-hub-aws-lza`). Security
   controls are on :ref:`security`.

********
Start-up
********

When the ``flip-api`` container starts, it runs these steps in order and **stops at the first failure** rather
than serve a half-ready hub:

1. Wait for PostgreSQL to accept connections.
2. Apply the database migrations shipped with the code. If a migration fails, the container exits.
3. Seed the essential data: roles and permissions, the well-known users, the FL kit slot pool, the site banner
   and configuration, the FL nets (from ``NET_ENDPOINTS`` and ``FL_BACKEND``), and one scheduler entry per net.
4. Start the API server, which also loads the browser CORS allowlist from the identity provider's registered
   redirect URLs and starts the background scheduler.

********************
Background scheduler
********************

``flip-api`` runs these jobs on a fixed interval. Each interval is a setting, in minutes.

.. list-table::
   :header-rows: 1
   :widths: 30 15 55

   * - Job
     - Default
     - What it does
   * - FL job pickup
     - 1
     - Assigns the oldest queued training job to a free net, bundles it and submits it (see
       :ref:`flip-fl-nets`). Skipped while deployment mode is on.
   * - FL job watchdog
     - 1
     - Fails a running job whose FL server has ended or lost it without reporting a result, and frees its net.
   * - FL API keep-alive
     - 2
     - Polls each net's server status so the FL API's admin session does not time out.
   * - Imaging re-import
     - 30
     - Asks Trusts to retry studies their PACS did not deliver, up to a per-project cap.
   * - Stale Trust-task recovery
     - 10
     - Returns a task a Trust took but did not report on within 30 minutes to ``PENDING``, and fails it after
       three retries. Also retries hub-side post-processing that failed.
   * - Malware-scan reconcile
     - 1
     - Re-checks uploads left ``SCANNING`` by a restart mid-scan.

A Trust's online/offline status is not a scheduled job. The hub works it out on each request from the Trust's
last heartbeat, with a 30-second timeout.

*********************
Talking to the Trusts
*********************

The hub **never connects to a Trust**. Work for a Trust is queued as a task on the hub, and the Trust's
``trust-api`` collects it. The Trust calls four endpoints:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Endpoint
     - Purpose
   * - ``GET /api/tasks/pending``
     - Fetch up to 50 pending tasks for this Trust; each is marked ``IN_PROGRESS`` when handed out.
   * - ``POST /api/tasks/{task_id}/result``
     - Report a task's outcome, marking it ``COMPLETED`` or ``FAILED``. A successful ``create_imaging`` result
       also makes the hub record the XNAT project and email credentials to its users; a successful
       ``persist_cohort`` result records the cohort the Trust froze at approval.
   * - ``POST /api/trust/heartbeat``
     - Sent on every poll, with the Trust's health snapshot when it has one.
   * - ``POST /api/cohort/results``
     - A cohort query's aggregate statistics from one Trust. The hub saves them and recomputes the combined
       statistics each time a Trust answers.

The task types are ``cohort_query``, ``persist_cohort``, ``create_imaging``, ``delete_imaging``,
``get_imaging_status``, ``reimport_studies`` and ``update_user_profile``. Task payloads are encrypted with the
shared AES key, on top of TLS.

Every call is authenticated with the Trust's **API key**, which the hub checks against its stored hashes. The key
is the Trust's identity: the hub tells the Trust which Trust it thinks it is, and the Trust refuses to run if
that does not match the Trust its kit file expects. The Trust side of this exchange is on
:doc:`component-trust-apis`.

****************************
Callbacks from the FL server
****************************

During training the FL server reports to the hub through three endpoints:

- ``POST /api/model/{id}/logs``
- ``POST /api/model/{id}/metrics``
- ``PUT /api/model/{id}/status/{status}``

These calls carry the hub's **internal service key**, not a Trust key. They must use the hub's internal URL
(``FLIP_API_INTERNAL_URL``), never the public address, because the public edge strips that key's header.

FL clients hold no hub credential. Anything a client needs the hub to know goes through the FL server.

********************************
Authentication and authorisation
********************************

Users sign in through an **OIDC identity provider**, chosen by ``AUTH_BACKEND``:

- **AWS Cognito** in staging and production (the only value production accepts);
- **Keycloak** in local development (the only value development accepts).

``flip-api`` checks every access token's signature, expiry, issuer, token type and audience; ID tokens are
rejected. It then confirms the user has TOTP (MFA) enrolled, so an administrator's MFA reset takes effect
immediately. This check is on unless ``ENFORCE_MFA`` is set to ``false``, which local development does because its
sign-in cannot complete a TOTP challenge. User administration (invite, enable, disable, delete, MFA state) goes
through the same provider.

Authorisation is **role-based**. A user's roles are tied to their identity-provider account, and per-resource
checks decide who may see or change a project, model or cohort query. The role matrix is on :ref:`rbac-roles`.

Three other keys each secure one connection:

.. list-table::
   :header-rows: 1
   :widths: 35 30 35

   * - Key
     - Connection
     - Where it lives
   * - Trust API key
     - Trust → hub
     - The Trust's kit file; hashed on the hub
   * - Internal service key
     - FL server → hub
     - The hub only
   * - Trust-internal service key
     - Between services inside a Trust
     - The Trust only; never reaches the hub

See :ref:`trust-internal-service-authentication` for the last one.

***********
Model files
***********

A researcher's upload **never passes through the hub**:

1. ``flip-api`` checks the file extension and returns a presigned upload limited to ``MAX_MODEL_FILE_BYTES``.
2. The browser uploads straight to a staging area in object storage.
3. The hub scans the file, which shows as ``SCANNING`` meanwhile:

   - Pickle-based files get a structural scan. A dangerous file is deleted and marked ``INFECTED``. A scan that
     errors or times out fails closed as ``ERROR``.
   - Python files get an advisory Bandit scan. Its findings are shown but never block the file.

4. Clean files move to the scanned area.

Downloads, listings and the FL app bundler read only from the scanned area, so **an unscanned file cannot reach
a Trust**.

*****
Email
*****

FLIP's own emails (access-request notifications, XNAT credentials) are sent according to ``EMAIL_BACKEND``:

- ``ses`` in staging and production;
- ``console`` in development (the only value accepted there). Messages are written to the log with secrets
  redacted, so a local stack needs no SES setup.

The identity provider sends its own invitations and password-reset codes. In staging and production, Cognito
sends them. The development Keycloak has no mail server, so a new user gets the shared dev password
(``ADMIN_USER_PASSWORD``) as a temporary password and changes it at first sign-in.

***************
Further reading
***************

- :gh:`flip-api/README.md` and :gh:`flip-ui/README.md`
- :gh:`deploy/providers/AWS/README.md` — the Terraform, deploy and rollback runbook behind the deployment pages
- :ref:`deploy-central-hub` and :ref:`security`
