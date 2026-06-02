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

************
Architecture
************

The Central Hub stack runs in a custom VPC with public and private subnets across two AZs:

- **flip-ui** — static assets served from S3 behind CloudFront at the canonical subdomain (``stag.flip.aicentre.co.uk`` / ``app.flip.aicentre.co.uk``). CloudFront also forwards ``/api/*`` to the ALB.
- **flip-api** — the central application API.
- **fl-api** and **fl-server** — the federated learning control plane. The FL server accepts outbound gRPC connections from trust-side FL clients via a Network Load Balancer (NLB).
- **PostgreSQL (RDS)** — managed database, private subnet.
- **Cognito** — user authentication (TOTP MFA enforced).
- **SES** — transactional email (invites, password reset, access requests).
- **Secrets Manager** — AES key, database password, internal service key hash.

Operator access is via AWS Systems Manager (SSM) Session Manager — port 22 is
**not** open on any security group.

*************
Prerequisites
*************

1. **AWS CLI configured with SSO access** — see `deploy/README.md <https://github.com/londonaicentre/FLIP/blob/main/deploy/README.md>`_.
2. **Terraform >= 1.13.1** (or OpenTofu).
3. **Python 3.12+** with `UV <https://docs.astral.sh/uv/guides/install-python/>`_.
4. **GitHub CLI** — needed to authenticate against GitHub Container Registry for image pulls.
5. **SSH key pair** at ``~/.ssh/host-aws`` — uploaded to AWS and used as the
   identity file for the SSM ProxyCommand-based SSH config.
6. **Environment file** — ``.env.stag`` (staging) or ``.env.production``
   (production) in the project root.
7. **AWS Session Manager plugin** — required for ``ssh flip`` and ``make forward-trust``.

AWS profile aliases (``prod``, ``stag``, ``dev``) should be configured in
``~/.aws/config`` so the Makefile guards can verify the active profile against
the chosen environment.

************************
Required IAM permissions
************************

The operator role used to provision infrastructure needs the following managed
policies (or equivalent custom permissions):

- ``AmazonEC2FullAccess``
- ``AmazonRDSFullAccess``
- ``CloudWatchLogsFullAccess``
- ``SecretsManagerReadWrite``
- ``IAMFullAccess``
- ``ElasticLoadBalancingFullAccess``
- ``AmazonSESFullAccess`` (optional, for email functionality)

Deployed EC2 instances themselves use **scoped least-privilege roles** rather
than these broad permissions — see ``deploy/providers/AWS/iam_ecs.tf`` for the
exact policy attachments.

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
5. ``plan`` and ``apply`` — apply infrastructure changes.
6. ``update-env`` — refresh the root env file with Terraform outputs.
7. ``ssh-config`` — write SSH config blocks with SSM ProxyCommand.
8. ``ansible-init`` — configure EC2 instances with Docker, CloudWatch, and FL assets.
9. ``deploy-centralhub`` — deploy hub services via Docker Compose / ECS.
10. ``deploy-trust`` — deploy any AWS-hosted trust services (skip when only using on-prem trusts).
11. ``status`` — comprehensive health checks.

The ``PROD`` variable selects the environment file (``stag`` → ``.env.stag``,
``true`` → ``.env.production``) and is mapped onto ``TF_VAR_environment``
(``stag`` or ``prod``) so Terraform can gate prod-only RDS hardening (deletion
protection, final snapshot).

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

   export PROD=stag    # or: export PROD=true

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
(``.env.stag`` or ``.env.production``) and preserves any keys that already
exist; ``make register-trusts`` writes the per-trust keys into the kit files.

***********************
Applying schema changes
***********************

The hub has **no migration framework** (no Alembic). On startup the entrypoint runs
``seed_essential_data.py``, which calls ``SQLModel.metadata.create_all()`` — this only creates
*missing* tables. It never alters an existing table, so a release that adds a non-nullable column,
changes a column type, or drops/renames a column is **not** applied to a database that already has
those tables; the new code then fails at runtime against the old schema.

The Central Hub database is treated as **recreatable**: it holds platform state (projects, queries,
FL job / metrics / audit rows, the trust registry), not a system of record that must be migrated in
place. To apply a schema-changing release, recreate the database so the entrypoint rebuilds it.

**Development** (docker-compose, disposable volume):

.. code-block:: shell

   make down
   docker compose -f deploy/compose.development.yml down -v   # drop the postgres volume
   make up                                                    # entrypoint reruns create_all + seeders

**Staging** (RDS is not deletion-protected):

.. code-block:: shell

   cd deploy/providers/AWS
   make destroy PROD=stag && make full-deploy PROD=stag

**Production** (RDS has deletion protection + a final snapshot):

1. Take a manual RDS snapshot first.
2. Connect to the database (SSM port-forward) and reset the schema so the entrypoint can rebuild it —
   do **not** delete the RDS instance (deletion protection blocks it, and recreating churns the
   endpoint and secrets):

   .. code-block:: sql

      DROP SCHEMA public CASCADE;
      CREATE SCHEMA public;

3. Force a new ``flip-api`` deploy so the entrypoint reruns ``create_all`` and the seeders.
4. Re-register the trusts (``make register-trusts``) and redistribute the refreshed kit files — the
   ``trust`` table and FL kit-slot pool come back empty.

.. warning::

   Recreation is destructive: every project, cohort query, model, FL result, and trust registration
   is lost. Confirm the database genuinely holds no data that must be preserved before recreating a
   production hub. If in-place preservation is ever required, a real migration (Alembic or
   hand-written ``ALTER`` / backfill SQL) must be introduced instead.

**********************************
FL image compatibility on upgrade
**********************************

The hub and the trust-side FL base images (``flip-fl-base`` for NVFLARE, ``flip-fl-base-flower`` for
Flower) share a wire contract for training metrics and logs. The metrics and logs endpoints now
**require** an ``fl_client_name`` field, so the hub and the FL images must be upgraded together:

- An **old** FL base image that omits ``fl_client_name`` is rejected (HTTP 422) by the new hub. FL
  clients historically swallow that failure, so training appears to complete while the metrics chart
  and logs stay empty.
- Deploy the hub and bump the trust-side FL image tag in the same maintenance window; do not run
  training in the gap.

This pairs with the ``flip-fl-base`` follow-up that adds the trust-internal service-key header to the
``flip`` client wrappers (see the **Trust-internal Service Authentication** section in the repo-root
``CLAUDE.md``).

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
