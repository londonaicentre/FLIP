.. _flip-central-hub:

###########
Central Hub
###########

The Central Hub is the cloud-hosted half of FLIP: the web UI researchers and administrators use, the central
API that owns projects, users, cohort queries and models, the hub side of every FL net, and the AWS services
that hold the hub's state. It is the only internet-facing part of the platform; every Trust reaches it
outbound, and it holds no clinical data — cohort results arrive as aggregate statistics and training leaves
behind model weights and metrics, never rows.

.. contents:: On this page
   :local:
   :depth: 2

********
Services
********

flip-ui
=======

A Vue 3 single-page application (Vite, TypeScript, Pinia). It talks to ``flip-api`` through one Axios client
whose base URL is injected per environment at ``window.js`` render time, attaching the user's Cognito access
token to every request and signing the user out on a 401. In development it runs as a Vite dev server
container; on staging and production it is static assets in an S3 bucket served through CloudFront — there
is no UI container there.

flip-api
========

A FastAPI application serving everything under ``/api``. Its routers map onto the platform's domains:

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Prefix
     - Purpose
   * - ``/projects``
     - Project lifecycle: create, edit, stage, approve, unstage, delete; imaging status and study re-import.
   * - ``/cohort``
     - Submit a cohort query to the project's Trusts and read back the aggregated results.
   * - ``/model``, ``/models``
     - Models under a project, their job types, metrics, logs and per-Trust status.
   * - ``/files``
     - Model-file upload (presigned S3 POST), scan status, listing, download, and federated results download.
   * - ``/fl``
     - Training start and stop, net status, and the deployment-mode quiesce report.
   * - ``/users``, ``/roles``, ``/site``
     - Users, access requests, MFA status and roles; site banner and details.
   * - ``/trust``, ``/admin/trusts``
     - Trust roster, connection health and registration.
   * - *(unprefixed)*
     - The endpoints Trusts and FL servers call back on — see `Talking to the Trusts`_ and
       `Callbacks from the FL server`_.

Interactive API docs (``/api/docs``) are served everywhere except production. The generated module reference
is under :doc:`API reference </api-reference>`.

FL nets, hub side
=================

Each FL net contributes an FL API and an FL server to the hub (``fl-api-net-N``, ``fl-server-net-N``). The
Central Hub API submits jobs through the FL API; the FL server is what the Trusts' FL clients connect to.
Both are described on :doc:`component-fl-nets`.

PostgreSQL
==========

The hub's own database: projects, cohort queries and their results, models, jobs, the FL scheduler state,
the Trust roster (with API-key hashes, never keys), the task queue for Trusts, roles and permissions. Its
schema is owned by Alembic migrations that run at every start-up (see `Start-up`_).

**************
AWS deployment
**************

The pictures below are rendered when this documentation is built, from
`deploy/providers/AWS/architecture/central_hub.py <https://github.com/londonaicentre/FLIP/blob/develop/deploy/providers/AWS/architecture/central_hub.py>`_
— a script kept next to the Terraform it depicts. A test in that tree pins every drawn node to a resource in
the ``.tf`` files, and every load-bearing resource to a drawn node, so the diagram cannot quietly fall out of
step with the infrastructure. How to provision and deploy the stack is on :ref:`deploy-central-hub`; the
security controls it implements are on :ref:`security`.

.. figure:: ../assets/generated/central-hub-aws-network.png
   :align: center
   :alt: Users and Trust nodes outside AWS; Route 53, CloudFront, WAF, ACM and the UI bucket at the edge; a
         VPC with a NAT gateway and FL network load balancer in public subnets, and an internal application
         load balancer, three Fargate services, Cloud Map, an SSM bastion and an optional mock Trust in
         private subnets; GHCR reached through the NAT gateway.

   Request and FL paths.

**Two ways in, both through AWS-managed front doors.** Browsers and each Trust's ``trust-api`` reach the hub
over HTTPS through CloudFront, which carries a WAFv2 web ACL, serves the UI bucket for ``/`` and forwards
``/api/*`` through a VPC origin to an **internal** application load balancer — the ALB has no public address
and accepts traffic only from CloudFront's service security group. FL clients connect to a **public network
load balancer** instead: FL traffic is mutual-TLS gRPC end to end, and a layer-7 balancer would terminate the
TLS the FL frameworks authenticate with, so the NLB passes TCP through untouched to the FL server. Its
security group allow-lists the registered Trusts' public addresses.

**Compute is ECS Fargate in private subnets.** ``flip-api``, ``fl-api-net-1`` and ``fl-server-net-1`` run as
Fargate tasks with no public IPs, discovered by name through a Cloud Map private DNS namespace
(``flip.local``). Container images come from GHCR through the single NAT gateway. Operator access is through
AWS Systems Manager Session Manager to a small bastion instance: no security group opens port 22.

.. figure:: ../assets/generated/central-hub-aws-data.png
   :align: center
   :alt: The three Fargate services with EFS, RDS Proxy, RDS PostgreSQL and VPC endpoints inside the VPC;
         four S3 buckets; and Cognito, KMS, CloudWatch Logs, SES, Parameter Store and Secrets Manager as
         regional services.

   Data and platform services.

**State lives in managed services.** ``flip-api`` reaches PostgreSQL through **RDS Proxy** with a short-lived
IAM token minted per connection — the application holds no database password, and rotating the master
secret is not an outage. The FL tasks share **EFS** access points for their workspaces, certificates and job
directories; the provisioning task copies each net's participant kits there from the kits bucket. Four **S3**
buckets separate the model-file staging and scanned prefixes, FL results, bundled apps and participant kits,
all encrypted with the hub's KMS key. **Cognito** is the user pool (with TOTP MFA); **Secrets Manager** holds
the ``FLIP_API`` secret (AES key and internal service key); **Parameter Store** carries the plain configuration
tasks read at start-up; **SES** sends the application's own emails. Interface endpoints for Secrets Manager,
SSM and CloudWatch Logs plus an S3 gateway endpoint keep that traffic off the NAT gateway.

********
Start-up
********

The ``flip-api`` container entrypoint runs, in order, and stops at the first failure rather than serve a
half-ready hub:

1. wait for PostgreSQL to accept connections;
2. ``alembic upgrade head`` — the schema is owned by migrations shipped with the code, so a release whose
   code expects a column the database does not have refuses to start;
3. seed the essential data: roles, permissions, role–permission mapping, the well-known users, the FL kit
   slot pool, the site banner and configuration, the FL nets from ``NET_ENDPOINTS`` and ``FL_BACKEND``, and one
   scheduler row per net;
4. start the API server.

When FastAPI starts it extends the browser CORS allowlist from the Cognito app client's callback URLs and
starts the background scheduler below.

********************
Background scheduler
********************

An APScheduler ``BackgroundScheduler`` inside ``flip-api`` runs interval jobs; every rate is a setting in
``flip_api/config.py`` and is expressed in minutes.

.. list-table::
   :header-rows: 1
   :widths: 30 15 55

   * - Job
     - Default
     - What it does
   * - FL job pickup
     - 1
     - Assigns the oldest queued training job to a free net, bundles and submits it — the loop described
       under :ref:`flip-fl-nets`. Skipped while deployment mode is on.
   * - FL API keep-alive
     - 2
     - Polls each net's server status so the FL API's admin session does not idle out.
   * - Imaging re-import
     - 30
     - Asks Trusts to retry studies their PACS did not deliver, up to a per-project cap.
   * - Stale Trust-task recovery
     - 10
     - Returns tasks a Trust took but never reported (after 30 minutes) to ``PENDING``, failing them after
       three retries; retries post-processing that failed hub-side.
   * - Malware-scan reconcile
     - 1
     - Re-checks uploads left ``SCANNING`` by a restart mid-scan.

A Trust's online/offline indicator is not a sweep: it is derived on request from the Trust's last heartbeat
against a 30-second timeout.

*********************
Talking to the Trusts
*********************

The hub never connects to a Trust. Work for a Trust is a row in the hub's task queue, and the Trust's
``trust-api`` fetches it:

- ``GET /api/tasks/pending`` — up to 50 pending tasks for the calling Trust, flipped to ``IN_PROGRESS`` as
  they are handed out. Task types: ``cohort_query``, ``create_imaging``, ``delete_imaging``,
  ``get_imaging_status``, ``reimport_studies``, ``update_user_profile``. Payloads are encrypted with the shared
  ``AES_KEY_BASE64`` on top of TLS.
- ``POST /api/tasks/{task_id}/result`` — the outcome, which marks the task ``COMPLETED`` or ``FAILED``; a
  successful ``create_imaging`` result also triggers hub-side post-processing (recording the XNAT project and
  emailing credentials).
- ``POST /api/trust/heartbeat`` — every poll cycle, carrying the Trust's own health snapshot when it has one.
- ``POST /api/cohort/results`` — a cohort query's aggregate statistics, saved per Trust and combined once
  every Trust has answered.

All four are authenticated by the Trust's API key, presented in a header and compared against the hub's
stored hashes in constant time; the identity **is** the key, so the hub tells the Trust who it thinks it is
and the Trust refuses to run if that disagrees with its kit. The trust-side half of this exchange is on
:doc:`component-trust-apis`.

****************************
Callbacks from the FL server
****************************

During training the FL server reports to the hub on the researcher's behalf: ``POST /api/model/{id}/logs``,
``POST /api/model/{id}/metrics`` and ``PUT /api/model/{id}/status/{status}``. These carry the hub's
**internal service key** rather than a Trust key and travel over the hub-internal URL
(``FLIP_API_INTERNAL_URL``), never through CloudFront, which strips that header at the edge. FL clients have no
such credential; anything a client needs the hub to know goes through the server.

********************************
Authentication and authorisation
********************************

Users authenticate with **Cognito**. ``flip-api`` verifies each access token's RS256 signature against the
pool's JWKS, checks its expiry, issuer, ``token_use`` (ID tokens are rejected) and ``client_id``, and then —
unless ``ENFORCE_MFA`` is switched off for local development — confirms the user has TOTP enrolled, so an
administrator's MFA reset takes effect immediately rather than at the next token. Authorisation is
role-based: roles and permissions are seeded rows, a user's roles are keyed by their Cognito identity, and
per-resource checks decide who may see or change a project, model or cohort query. The matrix is on
:ref:`rbac-roles`.

Three further keys exist, each for one hop: the per-Trust API key (Trust → hub, above), the hub's internal
service key (FL server → hub, above) and the per-Trust internal service key that never reaches the hub at
all — see :ref:`trust-internal-service-authentication`.

***********
Model files
***********

A researcher's upload never passes through the hub. ``flip-api`` checks the extension against the allowed
set and returns a **presigned S3 POST** policy capped at ``MAX_MODEL_FILE_BYTES``, and the browser uploads
straight to the ``uploaded/`` staging prefix. The UI then asks the hub to process the file: the record is
marked ``SCANNING``, pickle-bearing files get a structural picklescan (dangerous globals delete the object and
mark it ``INFECTED``; a scan that errors or times out fails closed to ``ERROR``), Python files get an advisory
Bandit pass whose findings are shown but never block, and clean files are promoted to the ``scanned/``
prefix. Everything downstream — downloads, listings, the FL app bundler — reads ``scanned/`` only, so an
unscanned file cannot reach a Trust.

*****
Email
*****

The application's own emails (access-request notifications, XNAT credentials) go through
``send_templated_email``, which dispatches on ``EMAIL_BACKEND``: ``ses`` in staging and production,
``console`` — log the would-be message, with secret-shaped fields redacted — by default in development, so a
local stack needs no SES identity. Cognito sends its own invitations and password-reset codes independently
of either.

***************
Further reading
***************

- `flip-api/README.md <https://github.com/londonaicentre/FLIP/blob/develop/flip-api/README.md>`_ and
  `flip-ui/README.md <https://github.com/londonaicentre/FLIP/blob/develop/flip-ui/README.md>`_
- `deploy/providers/AWS/README.md <https://github.com/londonaicentre/FLIP/blob/develop/deploy/providers/AWS/README.md>`_
  — the Terraform, deploy and rollback runbook behind the diagrams above
- :ref:`deploy-central-hub` and :ref:`security`
- Module reference: :doc:`API reference </api-reference>`
