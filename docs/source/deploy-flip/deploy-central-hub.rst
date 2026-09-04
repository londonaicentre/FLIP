.. _deploy-central-hub:

########################
Deploy the Central Hub
########################

The Central Hub is the cloud-hosted side of FLIP — it manages projects, users,
the federated learning server, and the public UI. It runs in AWS and is
provisioned with Terraform/OpenTofu plus Ansible. This guide covers a standalone
Central Hub deployment; for trust-side deployment see :doc:`deploy-flip-node-on-prem`
and :doc:`deploy-flip-node-in-tre`.

.. contents:: On this page
   :local:
   :depth: 2

****************
Deployment modes
****************

The Terraform root in ``deploy/providers/AWS/`` supports **two deployment modes**, both
permanently supported. They share one root module — the second is selected by an environment
flag, not a fork of the code.

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

**Self-contained** is the default, and the shape the rest of this page assumes. Everything
FLIP needs lives in one AWS account, which is the simplest way to stand up a Central Hub.

**Platform-managed** suits an AWS estate already governed by the Landing Zone Accelerator,
where a platform team owns the network, the guardrails and a shared edge, and workload
accounts are denied VPC-layer creation. FLIP then discovers the network it is given instead
of building one, and ingress arrives through the estate's own edge. Deploying this way means
coordinating with whoever operates that estate — the account needs its VPC, Transit Gateway
attachment, egress path and edge listeners in place before FLIP applies. The operator-facing
detail (what the flag gates on and off, the environment file, and the handoff to the
networking account) is in the "Deploying onto an LZA estate" section of
`deploy/providers/AWS/README.md
<https://github.com/londonaicentre/FLIP/blob/main/deploy/providers/AWS/README.md>`_.

Where a command below differs between the two, the difference is called out inline.

************
Architecture
************

In the self-contained mode the Central Hub stack runs in a custom VPC with public and private subnets across two AZs:

- **flip-ui** — static assets served from S3 behind CloudFront at the canonical subdomain (``stag.flip.aicentre.co.uk`` / ``app.flip.aicentre.co.uk``). CloudFront also forwards ``/api/*`` to the ALB.
- **flip-api** — the central application API.
- **fl-api** and **fl-server** — the federated learning control plane. The FL server accepts outbound gRPC connections from trust-side FL clients via a Network Load Balancer (NLB).
- **PostgreSQL (RDS)** — managed database, private subnet.
- **Cognito** — user authentication (TOTP MFA enforced).
- **SES** — transactional email sent by the application: access-request notifications and XNAT credential emails. Cognito invites and password-reset codes do **not** go through SES (see below).
- **Secrets Manager** — AES key, database password, internal service key hash.

Under the platform-managed mode the same services run unchanged; what differs is where they
sit and how traffic reaches them. The VPC and subnets are the accelerator's, there is no
in-account CloudFront distribution and no public NLB, and both web and FL traffic arrive from
the shared networking account over the Transit Gateway.

Operator access is via AWS Systems Manager (SSM) Session Manager — port 22 is
**not** open on any security group.

*************
Prerequisites
*************

1. **AWS CLI configured with SSO access** — see `deploy/README.md <https://github.com/londonaicentre/FLIP/blob/main/deploy/README.md>`_.
2. **Terraform >= 1.13.1** (or OpenTofu).
3. **Python 3.12 or 3.13** with `UV <https://docs.astral.sh/uv/guides/install-python/>`_.
4. **GitHub CLI** — needed to authenticate against GitHub Container Registry for image pulls.
5. **SSH key pair** at ``~/.ssh/host-aws`` — uploaded to AWS and used as the
   identity file for the SSM ProxyCommand-based SSH config.
6. **Environment file** — ``.env.stag`` (staging), ``.env.production``
   (production), or ``.env.lza-prod`` / ``.env.lza-stag`` (platform-managed) in
   the project root.
7. **AWS Session Manager plugin** — required for ``ssh flip`` and ``make forward-trust``.

AWS profile aliases (``prod``, ``stag``, ``dev``, and ``lza-prod`` / ``lza-stag``
for a platform-managed deployment) should be configured in ``~/.aws/config`` so
the Makefile guards can verify the active profile against the chosen environment.

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
``deploy/providers/AWS/README.md`` under "Required IAM permissions".

*********************
Full-stack deployment
*********************

The complete pipeline is wrapped behind a single Make target:

.. code-block:: shell

   cd deploy/providers/AWS
   make full-deploy PROD=stag    # staging
   # OR
   make full-deploy PROD=true    # production

This runs, in order:

1. ``github-login`` — GitHub CLI auth (for GHCR image pulls).
2. ``aws-login`` — AWS SSO auth for the selected profile.
3. ``init`` — initialise Terraform with the environment-specific S3 backend.
4. ``import-persistent`` — import existing persistent resources (Cognito, S3, Secrets) to prevent replacement.
5. ``generate-internal-service-key`` — mint the fl-server → flip-api internal service key.
6. ``plan`` and ``apply`` — apply infrastructure changes.
7. ``update-env`` — refresh the root env file with Terraform outputs.
8. ``ssh-config`` — write SSH config blocks with SSM ProxyCommand.
9. ``ansible-init`` — install ``psql`` on the minimal Central Hub SSM bastion
   and provision Docker, CloudWatch, and FL assets on the Trust EC2.
10. ``deploy-centralhub`` — deploy the Central Hub ECS Fargate services at the
    tip of the env's branch via immutable ``sha-<short7>`` task-definition
    revisions, and publish the UI to S3/CloudFront. ``make rollback-centralhub``
    repoints the services at the previous revision; see the "Central Hub deploys
    and rollback" section of ``deploy/providers/AWS/README.md`` for the tag
    resolution, the ``TAG=`` override, and the production rollout timing.
11. ``register-trusts`` — register the shipped trust roster on the hub (after ``deploy-centralhub`` seeds the FL kit-slot pool).
12. ``deploy-trust`` — deploy any AWS-hosted trust services (skip when only using on-prem trusts).
13. ``status`` — comprehensive health checks.

The ``PROD`` variable selects the environment file (``stag`` → ``.env.stag``,
``true`` → ``.env.production``, ``lza`` → ``.env.lza-prod``, ``lza-stag`` →
``.env.lza-stag``) and is mapped onto ``TF_VAR_environment`` — ``prod`` for
``true`` and ``lza``, ``stag`` otherwise — so Terraform can gate prod-only RDS
hardening (deletion protection, final snapshot). The two ``lza`` values
additionally set the platform-managed-network flag, which is orthogonal to the
environment name: ``PROD=lza`` is a production estate and keeps every prod-only
control, while ``PROD=lza-stag`` is staging semantics on the same
platform-managed network.

.. note::

   ``full-deploy`` is the self-contained chain. Some of its steps — the cloud
   trust EC2, the SSH and Ansible provisioning — do not apply to a
   platform-managed deployment, and the chain, along with ``make status`` and
   ``make destroy``, is not yet exercised there. On the ``lza`` values run
   ``init`` / ``plan`` / ``apply`` followed by ``deploy-centralhub`` and
   ``deploy-ui``; see the AWS README section linked under `Deployment modes`_.

Subsequent UI-only deploys do not need Terraform:

.. code-block:: shell

   make deploy-ui PROD=stag

This rebuilds the UI from the working tree, regenerates ``window.js``, syncs to
S3, and invalidates CloudFront. There is no legacy EC2 UI container; CloudFront
is the only supported UI path.

***********************
Step-by-step deployment
***********************

For debugging or selective steps:

.. code-block:: shell

   export PROD=stag    # or: export PROD=true, or PROD=lza / PROD=lza-stag (platform-managed)

   make github-login
   make aws-login
   make create-backend                          # one-off bootstrap of the Terraform state bucket
   make init
   make import-persistent
   make generate-internal-service-key           # fl-server → flip-api key
   make plan
   make apply
   make ssh-config
   make ansible-init
   make deploy-centralhub
   make register-trusts                         # register trusts on the hub (after deploy-centralhub seeds the FL kit-slot pool)
   make deploy-trust
   make status

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
(``.env.stag``, ``.env.production`` or ``.env.lza-prod``) and preserves any keys
that already exist; ``make register-trusts`` writes the per-trust keys into the
kit files.

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
- **Staging / Production**: a normal ECS redeploy (``make deploy-centralhub`` from
  ``deploy/providers/AWS/``) applies the revisions before the new task serves traffic.

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

************
Status check
************

After deployment:

.. code-block:: shell

   make status

This validates Terraform state and outputs, VPC and subnet configuration, EC2
health, RDS connectivity, Secrets Manager access, S3 buckets, Cognito user
pool, Docker container status, public endpoint availability, SSH connectivity,
and CloudWatch logging.

******************
SSH access via SSM
******************

After ``make ssh-config`` writes the SSM-based SSH configuration to
``~/.ssh/config``, the hub and any cloud-hosted trust are reachable directly:

.. code-block:: shell

   ssh flip          # Central Hub
   ssh flip-trust    # cloud trust (if deployed)

Trust web UIs (XNAT, Orthanc, swagger docs, Grafana) are reachable via SSM port
forwarding:

.. code-block:: shell

   make forward-trust

This prints the local URLs to paste into your browser. Press ``Ctrl+C`` to
close all forwards.

***************************
Destroy infrastructure
***************************

.. code-block:: shell

   make destroy

The destroy target preserves **Cognito**, **Secrets Manager**, and the
application **S3 bucket**. In ``prod``, the RDS instance has deletion
protection enabled and a final snapshot is taken before deletion is allowed —
staging stays disposable.

***************
Troubleshooting
***************

Run ``make status`` first; it auto-diagnoses AWS resource health, network
connectivity, endpoint availability, container status, and resource usage.

For known failure modes — Terraform state drift, ECS service errors,
CloudFront cache invalidation, RDS connectivity, SSM Session Manager issues —
see ``deploy/providers/AWS/TROUBLESHOOTING.md``.
