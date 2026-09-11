.. _deploy-central-hub:

########################
Deploy the Central Hub
########################

The Central Hub is the cloud-hosted side of FLIP — it manages projects, users,
the federated learning server, and the public UI. It runs in AWS and is
provisioned with Terraform/OpenTofu from **one root module**,
``deploy/providers/AWS/``, in one of **two deployment modes**. This page holds
what the two modes share; each mode's architecture, diagrams and runbook has its
own page below. For trust-side deployment see :doc:`deploy-flip-node-on-prem`
and :doc:`deploy-flip-node-in-tre`.

.. contents:: On this page
   :local:
   :depth: 1

****************
Deployment modes
****************

Both modes are permanently supported. They share the one root module — the
second is selected by an environment flag, not a fork of the code.

.. list-table::
   :header-rows: 1
   :widths: 20 18 31 31

   * - Mode
     - Selected by
     - Network
     - Ingress
   * - **Self-contained** (default)
     - ``PROD=stag`` / ``PROD=true``
     - FLIP creates its own VPC, subnets, internet gateway and NAT
     - In-account CloudFront for the UI and ``/api/*``; a public NLB for FL traffic
   * - **Platform-managed**
     - ``PROD=lza`` (production) / ``PROD=lza-stag`` (staging)
     - Discovered from a VPC provisioned by the AWS `Landing Zone Accelerator
       <https://aws.amazon.com/solutions/implementations/landing-zone-accelerator-on-aws/>`_;
       FLIP creates no network resources
     - A shared networking account's edge, reached over a Transit Gateway; no public load
       balancer in the workload account

**Self-contained** is the default. Everything FLIP needs lives in one AWS
account, which is the simplest way to stand up a Central Hub, and it is the
shape the open-source project targets. Its architecture and the full runbook are
on :doc:`deploy-central-hub-aws`.

**Platform-managed** suits an AWS estate already governed by the Landing Zone
Accelerator, where a platform team owns the network, the guardrails and a shared
edge, and workload accounts are denied VPC-layer creation. FLIP then discovers
the network it is given instead of building one, and ingress arrives through the
estate's own edge. Deploying this way means coordinating with whoever operates
that estate. :doc:`deploy-central-hub-aws-lza` describes what the mode changes
and what the estate has to provide; the operator-facing detail (the environment
file, the flag's exact gating, the handoff to the networking account) is in the
"Deploying onto an LZA estate" section of `deploy/providers/AWS/README.md
<https://github.com/londonaicentre/FLIP/blob/main/deploy/providers/AWS/README.md>`_.

.. toctree::
   :maxdepth: 1

   deploy-central-hub-aws
   deploy-central-hub-aws-lza

Everything below applies to both modes.

*************
Prerequisites
*************

1. **AWS CLI configured with SSO access** — see `deploy/README.md <https://github.com/londonaicentre/FLIP/blob/main/deploy/README.md>`_.
2. **Terraform >= 1.13.1** (or OpenTofu).
3. **Python 3.12 or 3.13** with `UV <https://docs.astral.sh/uv/guides/install-python/>`_.
4. **GitHub CLI** — needed to authenticate against GitHub Container Registry for image pulls.
5. **SSH key pair** at ``~/.ssh/host-aws`` — uploaded to AWS and used as the
   identity file for the SSM ProxyCommand-based SSH config (self-contained mode;
   the platform-managed mode has no SSH or Ansible step).
6. **Environment file** — ``.env.stag`` (staging) or ``.env.production``
   (production) for the self-contained mode, ``.env.lza-stag`` or
   ``.env.lza-prod`` for the platform-managed mode, in the project root.
7. **AWS Session Manager plugin** — required for ``ssh flip`` and
   ``make forward-trust`` (self-contained mode).

AWS profile aliases (``prod``, ``stag``, ``dev``, and ``lza-prod`` / ``lza-stag``
for a platform-managed deployment) should be configured in ``~/.aws/config`` so
the Makefile guards can verify the active profile against the chosen environment.

The ``PROD`` variable selects the environment file (``stag`` → ``.env.stag``,
``true`` → ``.env.production``, ``lza`` → ``.env.lza-prod``, ``lza-stag`` →
``.env.lza-stag``) and is mapped onto ``TF_VAR_environment`` — ``prod`` for
``true`` and ``lza``, ``stag`` otherwise — so Terraform can gate prod-only RDS
hardening (deletion protection, final snapshot). The two ``lza`` values
additionally set the platform-managed-network flag, which is orthogonal to the
environment name: ``PROD=lza`` is a production estate and keeps every prod-only
control, while ``PROD=lza-stag`` is staging semantics on the same
platform-managed network.

************************
Required IAM permissions
************************

The operator role used to provision infrastructure needs the following managed
policies (or equivalent custom permissions):

- ``AmazonEC2FullAccess``
- ``AmazonECS_FullAccess``
- ``AmazonRDSFullAccess``
- ``AmazonElasticFileSystemFullAccess``
- ``CloudWatchLogsFullAccess``
- ``SecretsManagerReadWrite``
- ``IAMFullAccess``
- ``ElasticLoadBalancingFullAccess`` (covers ALB and NLB)
- ``CloudFrontFullAccess``
- ``AWSWAFFullAccess``
- ``AWSCertificateManagerFullAccess``
- ``AmazonRoute53FullAccess``
- ``AWSCloudMapFullAccess``
- ``AmazonSSMFullAccess``
- ``AmazonSESFullAccess`` (optional, for email functionality)

Deployed EC2 instances themselves use **scoped least-privilege roles** rather
than these broad permissions — see ``deploy/providers/AWS/iam_ecs.tf`` for the
exact policy attachments. The canonical list lives in
``deploy/providers/AWS/README.md`` under "Required IAM permissions". On a
platform-managed estate the workload account's Identity Center permission set
stands in for this list, and the estate's service control policies bound it:
network-layer creation is denied there by design.

***********************
Service authentication
***********************

The hub uses three separate authentication mechanisms (see :doc:`/sys-admin`
for full details):

- **Trust API keys** — minted by the ``register_trust`` service when a trust is
  registered. The hub stores only the SHA-256 hash in the ``api_key_hash``
  column of the ``trust`` table; the plaintext is written once into that
  trust's kit file (``trust/.env.<CODE>.<env>``). Trusts are registered with
  ``make register-trust KIT=<CODE>`` (or ``make register-trusts`` for the
  shipped dev roster).
- **Internal service key** — single hub-internal key for fl-server → flip-api
  calls. Generated with ``make generate-internal-service-key``.
- **Trust-internal service keys** — per-trust shared secret used inside each
  trust for trust-api / imaging-api / fl-client → imaging-api / data-access-api
  calls. The hub never sees these. Minted by ``register_trust`` alongside the
  trust API key and written into the trust's kit file.

``make generate-internal-service-key`` populates the active env file
(``.env.stag``, ``.env.production``, ``.env.lza-stag`` or ``.env.lza-prod``) and
preserves any keys that already exist; ``make register-trusts`` writes the
per-trust keys into the kit files.

***********************
Applying schema changes
***********************

The hub schema is owned by **Alembic** (``flip-api/src/flip_api/db/migrations/``). On
startup the ``flip-api`` entrypoint runs ``alembic upgrade head`` **before** seeding —
fail-fast, so a release whose code expects a column an in-place database hasn't migrated
yet refuses to start instead of silently corrupting data.

Any schema-affecting change to ``flip-api/src/flip_api/db/models/*.py`` must ship a revision
in the same PR. The drift guard at ``flip-api/tests/integration/test_migrations.py`` fails
CI otherwise.

**Authoring a revision** (from ``flip-api/``):

.. code-block:: shell

   make migration MESSAGE="<short description>"   # autogenerate from the model diff (flip-db must be up)
   # review the file under src/flip_api/db/migrations/versions/ — autogen misses native-PG-enum
   # ALTER TYPE … ADD VALUE (needs op.get_context().autocommit_block()) and downgrades that drop
   # an enum-typed table (must also DROP TYPE).
   make migrate                                   # alembic upgrade head, apply locally
   make migration_current                         # confirm head matches the new revision

**Applying a release** — nothing extra is required. The ``flip-api`` entrypoint runs
``alembic upgrade head`` on every container start, so a fresh ``flip-api`` deploy
applies any pending revisions in order against the existing database:

- **Development**: ``make restart`` re-creates the ``flip-api`` service's container
  (``deploy-flip-api-1`` — compose names it from the project, no service sets
  ``container_name``); revisions apply on boot.
- **Staging / Production, in either mode**: a normal ECS redeploy
  (``make deploy-centralhub`` from ``deploy/providers/AWS/``) applies the
  revisions before the new task serves traffic.

Because revisions are real ``ALTER`` / ``UPDATE`` statements written by the PR author, schema
changes preserve the existing rows — there is **no** drop-and-recreate workflow for the
Central Hub database in routine operation.

**********************************
FL image compatibility on upgrade
**********************************

The FL base images for the hub and trust-side (``flare-fl-base`` for NVFLARE, ``flower-fl-base`` for
Flower) share a wire contract for training metrics and logs. The metrics and logs endpoints now
**require** an ``fl_client_name`` field, so the hub and the FL images must be upgraded together:

- An **old** FL base image that omits ``fl_client_name`` is rejected (HTTP 422) by the new hub. FL
  clients historically swallow that failure, so training appears to complete while the metrics chart
  and logs stay empty.
- Deploy the hub and bump the trust-side FL image tag in the same maintenance window; do not run
  training in the gap.

This pairs with the ``flip-utils`` package that adds the trust-internal service-key header to the
``flip`` client wrappers (see :ref:`trust-internal-service-authentication`).

************
Email setup
************

FLIP uses SES for transactional email and Cognito for authentication. Before
the first deploy you must:

1. Verify the sender identity — Terraform creates the SES identity; click the
   verification link in the email that arrives at the verified address.
2. Confirm the identity status shows **Verified** in the SES console.

If SES is still in the sandbox, request production access from the SES console
or only send to verified destination addresses for testing.

.. note::

   SES carries only the emails the application sends itself: access-request
   notifications and XNAT credential emails. **Cognito sends its own invites
   and password-reset codes** from the user pool's default sender — the pool
   declares no ``email_configuration``, so it uses ``COGNITO_DEFAULT`` rather
   than SES. A broken or sandboxed SES identity therefore does not stop
   invites or password resets, and conversely verifying the SES identity does
   not fix them; those have their own delivery limits (see FLIP#592).

   In development neither is needed: flip-api defaults to
   ``EMAIL_BACKEND=console``, which logs the would-be email instead of calling
   SES.
