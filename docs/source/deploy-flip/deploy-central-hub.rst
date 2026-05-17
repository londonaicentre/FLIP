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
- **Secrets Manager** — AES key, database password, API key hashes.

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
   make generate-keys                           # trust API keys (idempotent)
   make generate-internal-service-key           # fl-server → flip-api key
   make generate-trust-internal-service-keys    # per-trust internal keys (idempotent)
   make plan
   make apply
   make ssh-config
   make ansible-init
   make deploy-centralhub
   make deploy-trust
   make status

***********************
Service authentication
***********************

The hub uses three separate authentication mechanisms (see :doc:`/sys-admin`
for full details):

- **Trust API keys** — per-trust plaintext key in ``TRUST_API_KEYS``; hub stores
  only the SHA-256 hash in ``TRUST_API_KEY_HASHES``. Generated from
  ``deploy/providers/AWS`` with ``make generate-keys``.
- **Internal service key** — single hub-internal key for fl-server → flip-api
  calls. Generated with ``make generate-internal-service-key``.
- **Trust-internal service keys** — per-trust shared secret used inside each
  trust for trust-api / imaging-api / fl-client → imaging-api / data-access-api
  calls. The hub never sees these. Generated with
  ``make generate-trust-internal-service-keys``.

All three commands populate the active env file (``.env.stag`` or
``.env.production``) and preserve any keys that already exist.

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
