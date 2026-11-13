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
     - Submit a cohort query to the project's Trusts and read back the aggregated results; draft cohort SQL from
       a description (Beta, see `Cohort SQL drafting (Beta)`_).
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

The hub runs on AWS, provisioned with Terraform/OpenTofu from one root module, on a `Landing Zone
Accelerator <https://aws.amazon.com/solutions/implementations/landing-zone-accelerator-on-aws/>`_ estate
whose platform team owns the network and the edge.

.. seealso::

   :ref:`deploy-central-hub` and :doc:`/deploy-flip/deploy-central-hub-aws-lza` have the architecture diagrams,
   the resource layout and the runbook. Security controls are on :ref:`security`.

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
       statistics each time a Trust answers. It accepts them only from a Trust it dispatched that query to (the
       Trust's ``cohort_query`` task) and only while the project is still unstaged; anything else is refused.

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

When a job finishes it uploads its results the same way. The FL server holds no object-store credentials: it
asks the hub to start the upload of the model's results file, then for a short-lived URL for each part, then
to complete it, or to abort it if a part cannot be sent. The hub signs only for an upload it started for that
model, for the parts the declared size has and for each part's exact size. The size is capped
(``MAX_RESULTS_FILE_BYTES``, 50 GiB by default), and a model has one upload in progress at a time: starting
another discards the unfinished one.

- ``POST /api/model/{id}/results-upload``
- ``POST /api/model/{id}/results-upload/part``
- ``POST /api/model/{id}/results-upload/complete``
- ``POST /api/model/{id}/results-upload/abort``

These calls carry the hub's **internal service key**, not a Trust key. They must use the hub's internal URL
(``FLIP_API_INTERNAL_URL``), never the public address, because the public edge strips that key's header.

Each call also carries a **job credential**. The hub mints one for every job it dispatches and keeps only its
hash; the FL API leaves it where the FL server reads it and no Trust does. The hub accepts a callback about a
model only with the credential of that model's own job, and only while that job is still reporting, so one job
cannot change another model's status, logs, metrics or results.

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

Four other keys each secure one connection:

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
   * - Job credential
     - One job's FL server → hub, for that job's model
     - The hub only, minted per job; hashed in the database
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

.. _cohort-sql-drafting:

***************************
Cohort SQL drafting (Beta)
***************************

An optional assistant on the cohort page that drafts cohort SQL from a researcher's description (FLIP#1556). It is
**off by default**, and a draft only fills the editor: it never runs SQL, never changes a project's cohort query, and
every Trust still checks the query it receives with its own validator.

``flip-api`` makes one bounded model call per draft, to Claude on Amazon Bedrock through the official ``anthropic``
SDK (``AnthropicBedrockMantle``), signed with the hub's ECS task role. No API key is used; a configured Bedrock API
key is refused. The instructions are versioned files under ``flip_api/cohort_services/drafting/assets/``: the rules
(including the Trusts' function allowlist and the vocabulary-free style), the OMOP schema summary generated from the
Trusts' DDL, the concept ids FLIP's data uses, and tutorial queries as examples. Before a draft is returned it must
pass the hub's fast pre-check, an OMOP schema check, and the project's output contract (``person_id`` and
``accession_id`` for imaging projects, ``person_id`` for tabular ones). A rejected draft goes back to the model with
the reason, at most twice. A refusal by the model is shown as a decline: Amazon Bedrock has no server-side fallback
model, and the SDK's client-side fallback would send the description to a second model nobody has reviewed.

Routes
======

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Route
     - Behaviour
   * - ``POST /api/cohort/draft``
     - Body ``{"project_id", "description", "clarifications": [{"question", "answer"}]}`` (at most three answers)
       and an ``Idempotency-Key`` header. The caller must be able to edit the project, which must be ``UNSTAGED``;
       the project's shape is read on the hub. Returns ``status`` ``draft`` (``sql``, ``explanation``,
       ``assumptions``, ``concepts_used``), ``clarification`` (``question``, ``options``), ``decline``
       (``message``) or ``error`` (``error_code``: ``allowance_exhausted``, ``request_cap_reached``,
       ``rate_limited``, ``duplicate_request``, ``model_unavailable`` or ``invalid_draft``), always with ``usage``.
       ``404`` while the feature is off.
   * - ``GET /api/cohort/draft/usage``
     - ``{"enabled", "used", "limit", "remaining", "resets_at"}`` for the caller, so the UI can hide the assistant
       or draw the allowance ring before the first draft.

Allowance and limits
====================

Each user has a monthly allowance of successful drafts across projects, reset at 00:00 UTC on the first of the
month. A draft that passes the checks is charged; a clarification, decline or error is not. Uncharged requests still
call the model, so a second, hidden cap bounds **model calls** per user per month whatever the outcome, validation
retries included, and a per-minute rate limit applies to every request. All three are counted in the hub's database
(``cohort_draft_allowance`` and ``cohort_draft_request``) under a per-user row lock, so they hold across replicas: a
request reserves its place before the model call, and a repeated ``Idempotency-Key`` is never charged twice. A
reservation left behind by a crashed request stops counting after ``COHORT_DRAFT_RESERVATION_TTL_MINUTES``.

The hub logs metadata only: user, project, outcome, model calls, tokens and latency. The description and the
model's output are never logged or stored.

Settings
========

.. list-table::
   :header-rows: 1
   :widths: 40 15 45

   * - Variable
     - Default
     - Meaning
   * - ``COHORT_DRAFT_ENABLED``
     - ``false``
     - Turns the assistant and both routes on. Turning it off is the rollback.
   * - ``COHORT_DRAFT_PROVIDER``
     - per environment
     - ``fake`` (canned drafts) is the only value in development; ``bedrock`` the only one in staging and
       production.
   * - ``COHORT_DRAFT_BEDROCK_MODEL_ID``
     - ``anthropic.claude-sonnet-5-5``
     - Bedrock model id.
   * - ``COHORT_DRAFT_BEDROCK_REGION``
     - ``eu-west-2``
     - Bedrock region.
   * - ``COHORT_DRAFT_EFFORT``
     - ``medium``
     - Model effort (``low``, ``medium``, ``high``).
   * - ``COHORT_DRAFT_GUARDRAIL_ID``, ``COHORT_DRAFT_GUARDRAIL_VERSION``
     - unset
     - A Bedrock Guardrail passed through on every call; set both or neither.
   * - ``COHORT_DRAFT_MAX_DESCRIPTION_CHARS``
     - ``2000``
     - Longest description, and the longest total of clarification answers.
   * - ``COHORT_DRAFT_MAX_TOKENS``
     - ``8000``
     - Output tokens per model call, thinking included.
   * - ``COHORT_DRAFT_TIMEOUT_SECONDS``
     - ``45``
     - Per model call; the SDK retries a timed-out or throttled call once.
   * - ``COHORT_DRAFT_MONTHLY_ALLOWANCE``
     - ``30``
     - Successful drafts per user per UTC month, shown to the user.
   * - ``COHORT_DRAFT_MONTHLY_REQUEST_CAP``
     - ``90``
     - Model calls per user per UTC month whatever the outcome, retries included. Not shown to the user.
   * - ``COHORT_DRAFT_RATE_LIMIT_PER_MINUTE``
     - ``5``
     - Draft requests per user per minute.
   * - ``COHORT_DRAFT_RESERVATION_TTL_MINUTES``
     - ``10``
     - Age after which an unsettled reservation is treated as abandoned.

Before turning it on (phase 2)
==============================

Phase 1 ships the feature off, with no AWS changes. Enabling it in staging or production still needs:

- **Model and region.** Confirm that ``anthropic.claude-sonnet-5-5`` is offered in ``eu-west-2``, and whether it is
  served there or only through a cross-region inference profile, which may process requests in another region.
  Information governance decides whether that is acceptable.
- **IAM.** Invocation permission for the chosen model (and guardrail) on the hub's ``ecs_flip_api_task`` role only.
- **Network.** A route from the hub's private subnets to Bedrock, normally a VPC interface endpoint. On the LZA estates
  this is an account- or network-level change.
- **Guardrail.** Create the Bedrock Guardrail for UK identifiers and set its id and version. The guardrail headers
  the hub sends are those of Bedrock's InvokeModel API; confirm the Mantle endpoint honours them.
- **DPIA** and information-governance review of sending researcher free text to Bedrock.
- **Benchmark.** The semantic benchmark of reviewed cohort descriptions, and the effort setting it supports.
- **Admin override** of a user's allowance, with audit records.

*****
Email
*****

FLIP's own emails (access-request notifications, XNAT credentials) are sent according to ``EMAIL_BACKEND``:

- ``ses`` in staging and production;
- ``console`` in development (the only value accepted there). Messages are written to the log with secrets
  redacted, so a local stack needs no SES setup.

The identity provider sends its own invitations and password-reset codes. In staging and production, Cognito
sends them. The development Keycloak has no mail server, so a new user, or one an admin resets, gets the shared dev
password (``ADMIN_USER_PASSWORD``) as a temporary password and changes it at first sign-in.

***************
Further reading
***************

- :gh:`flip-api/README.md` and :gh:`flip-ui/README.md`
- :gh:`deploy/providers/AWS/README.md` — the Terraform, deploy and rollback runbook behind the deployment pages
- :ref:`deploy-central-hub` and :ref:`security`
