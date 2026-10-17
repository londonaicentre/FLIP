.. _deploy-central-hub:

########################
Deploy the Central Hub
########################

The Central Hub is the cloud-hosted side of FLIP — it manages projects, users,
the federated learning server, and the public UI. It runs in AWS and is
provisioned with Terraform/OpenTofu from **one root module**,
``deploy/providers/AWS/``. This page holds what every hub deployment shares.
For trust-side deployment see :doc:`deploy-flip-node-on-prem`
and :doc:`deploy-flip-node-in-tre`.

.. contents:: On this page
   :local:
   :depth: 1

****************
Deployment shape
****************

The hub deploys onto an AWS estate governed by the `Landing Zone Accelerator
<https://aws.amazon.com/solutions/implementations/landing-zone-accelerator-on-aws/>`_,
where a platform team owns the network, the guardrails and a shared edge, and
workload accounts are denied VPC-layer creation. FLIP discovers the network it
is given instead of building one, and ingress arrives through the estate's own
edge. Deploying this way means coordinating with whoever operates that estate.
:doc:`deploy-central-hub-aws-lza` describes the architecture and what the estate
has to provide; the operator-facing detail (the environment file, the handoff to
the networking account) is in the "Deploying onto an LZA estate" section of
`deploy/providers/AWS/README.md
<https://github.com/londonaicentre/FLIP/blob/main/deploy/providers/AWS/README.md>`_.

The self-contained single-account mode, in which FLIP built its own VPC and
in-account edge, is no longer supported and has been removed
(`FLIP#1479 <https://github.com/londonaicentre/FLIP/issues/1479>`_).

.. toctree::
   :maxdepth: 1

   deploy-central-hub-aws-lza

.. _deploy-central-hub-costs:

*************
Running costs
*************

The figures below are for the **Central Hub only**: one hub with one FL net, measured
from the AI Centre's own bills in September 2026, in ``eu-west-2``, in US dollars
before tax (AWS adds VAT for UK accounts), at on-demand list prices. They are rounded
on purpose; they are a point-in-time measurement, and the right number for a budget is
whatever Cost Explorer reports for the account in question.

.. list-table::
   :header-rows: 1
   :widths: 50 14 36

   * - What bills to the workload account
     - Roughly
     - Notes
   * - ECS Fargate (three tasks), RDS and RDS Proxy, EFS, the internal NLB, CloudWatch,
       GuardDuty and Security Hub; the NAT gateway, endpoints, CloudFront and WAF are the
       estate's
     - $400 a month
     - Plus a share of the **platform layer**: Transit Gateway attachments, the central
       Network Firewall, central endpoints and NAT, and the shared edge. For the AI
       Centre's estate that layer is of the order of $800 a month in total. It exists
       whether or not FLIP is deployed, so attribute all of it to FLIP only when FLIP is
       the estate's sole workload.

The platform layer's firewall and attachment charges land on the platform team's
accounts rather than the workload's, so agree with them how that layer is recharged.
A staging environment costs about the same as production, since nothing in the hub
scales with the number of users. The AI Centre keeps an itemised estimate of its
platform layer alongside its LZA configuration.

.. note::

   **Trust hosts are not included.** A trust's running cost is set by the GPU instance
   it provisions and by how long that instance is busy, which follows from the number
   and size of the projects the trust takes part in, so it is a per-trust decision
   rather than a property of the hub. The mock trust EC2 Terraform can provision
   (a ``t3.xlarge`` with no GPU) is a test fixture, not a sizing recommendation. For
   always-on versus on-demand GPU provisioning at a trust, see the *GPU Resource
   Management* section of :doc:`deploy-flip-node-in-tre`.

*************
Prerequisites
*************

1. **AWS CLI configured with SSO access** — see `deploy/README.md <https://github.com/londonaicentre/FLIP/blob/main/deploy/README.md>`_.
2. **Terraform >= 1.13.1** (or OpenTofu).
3. **Python 3.12 or 3.13** with `UV <https://docs.astral.sh/uv/guides/install-python/>`_.
4. **GitHub CLI** — needed to authenticate against GitHub Container Registry for image pulls.
5. **SSH key pair** at ``~/.ssh/host-aws`` — Terraform registers its public half
   as the EC2 key pair, and it is the identity file for the SSM ProxyCommand-based
   SSH config.
6. **Environment file** — ``.env.stag`` (staging) or ``.env.prod``
   (production) in the project root.
7. **AWS Session Manager plugin** — required for ``ssh flip`` and
   ``make forward-trust``.

AWS profile aliases (``lza-prod``, ``lza-stag`` and ``dev``) should be configured in ``~/.aws/config`` so
the Makefile guards can verify the active profile against the chosen environment.

The ``HUB`` variable (``stag`` or ``prod``, required by every
``deploy/providers/AWS`` target) selects the environment file (``.env.stag`` /
``.env.prod``) and is mapped onto ``TF_VAR_environment`` so Terraform can gate
prod-only RDS hardening (deletion protection, final snapshot).

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
- ``ElasticLoadBalancingFullAccess``
- ``CloudFrontFullAccess``
- ``AWSCertificateManagerFullAccess``
- ``AmazonRoute53FullAccess``
- ``AWSCloudMapFullAccess``
- ``AmazonSSMFullAccess``
- ``AmazonSESFullAccess`` (optional, for email functionality)

Deployed EC2 instances themselves use **scoped least-privilege roles** rather
than these broad permissions — see ``deploy/providers/AWS/iam_ecs.tf`` for the
exact policy attachments. The canonical list lives in
``deploy/providers/AWS/README.md`` under "Required IAM permissions". The
workload account's Identity Center permission set stands in for this list, and the estate's service control policies bound it:
network-layer creation is denied there by design.

***********************
Service authentication
***********************

The hub uses three separate authentication mechanisms (see :doc:`/sys-admin`
for full details):

- **Trust API keys** — minted by the ``register_trust`` service when a trust is
  registered. The hub stores only the SHA-256 hash in the ``api_key_hash``
  column of the ``trust`` table; the plaintext is written once into that
  trust's kit file (``trust/.env.<CODE>.<hub>``). Trusts are registered from
  ``deploy/providers/AWS`` with ``make register-trusts KIT=<CODE> HUB=<hub>`` for a
  single kit, or ``make register-trusts HUB=<hub>`` with no ``KIT`` for every
  ``trust/.env.*.<hub>`` kit present.
- **Internal service key** — single hub-internal key for fl-server → flip-api
  calls. Generated with ``make generate-internal-service-key``.
- **Trust-internal service keys** — per-trust shared secret used inside each
  trust for trust-api / imaging-api / fl-client → imaging-api / data-access-api
  calls. The hub never sees these. Minted by ``register_trust`` alongside the
  trust API key and written into the trust's kit file.

``make generate-internal-service-key`` populates the active env file
(``.env.stag`` or ``.env.prod``) and
preserves any keys that already exist; ``make register-trusts`` writes the
per-trust keys into the kit files.

***********************************************************
Upgrading across the payload-encryption change (FLIP#1179)
***********************************************************

Task payloads between the hub and its trusts are AES-256-GCM envelopes, with no
compatibility for the earlier AES-CBC format. A hub and every trust registered to it
therefore cross that release together, as a flag day:

1. Enable Deployment Mode (Admin → Deployments) and wait for ``GET /fl/quiesce`` to
   report ``deployment_mode: true`` and ``fl_quiesced: true``.
2. Redeploy the hub (``make deploy-centralhub``) and every trust (``make deploy-trust``,
   the on-prem playbook, or the Kubernetes chart) in one window.
3. Disable Deployment Mode.

Anything still encrypted under the old format at the moment of upgrade fails
afterwards: a task collected under CBC by a trust that has since upgraded stays
``IN_PROGRESS`` (reset it to ``PENDING``), and a running FL job carrying a CBC project
id fails at imaging-api once that trust upgrades — which is what step 1 prevents.

The same page's ``AES_KEY_BASE64`` must be byte-identical on the hub and on every
trust container that decrypts. On staging and production the hub's copy is whatever
the CI Terraform apply wrote into Secrets Manager from the GitHub environment; recover
the deployed value with ``deploy/providers/AWS/scripts/reconcile_ci_env.py`` rather than
trusting a checked-out ``.env.<hub>``.

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
- **Staging / Production**: a normal ECS redeploy
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

   In development neither is used: flip-api pins
   ``EMAIL_BACKEND=console``, which logs the would-be email instead of calling
   SES, and to ``AUTH_BACKEND=keycloak``, a local Keycloak container in place
   of Cognito (``KEYCLOAK_URL``, ``KEYCLOAK_PUBLIC_URL``, ``KEYCLOAK_REALM``,
   ``KEYCLOAK_CLIENT_ID``, ``KEYCLOAK_AUDIENCE``, ``KEYCLOAK_ADMIN_CLIENT_ID``
   and ``KEYCLOAK_ADMIN_CLIENT_SECRET`` carry working defaults for the dev
   compose and are read only under that backend). The dev Keycloak has no
   mail server, so a newly registered user is given the shared dev password
   (``ADMIN_USER_PASSWORD``) as a temporary one instead of an invitation
   email. Development accepts only ``keycloak`` and ``console``, production
   only ``cognito`` and ``ses``; each refuses the other's at boot (FLIP#919).
