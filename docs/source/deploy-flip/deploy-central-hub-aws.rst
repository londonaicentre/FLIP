.. _deploy-central-hub-aws:

##############################
Deploy the Central Hub on AWS
##############################

This is the **self-contained** deployment mode: everything FLIP needs lives in one AWS account, and Terraform
builds the network as well as the services. It is the default mode and the one the open-source project targets.

.. seealso::

   - :ref:`deploy-central-hub` — prerequisites, IAM permissions, registering trusts, deploys and rollback, schema
     changes and email setup, shared by both modes.
   - :doc:`deploy-central-hub-aws-lza` — the platform-managed alternative for a Landing Zone Accelerator estate.

.. contents:: On this page
   :local:
   :depth: 2

************
Architecture
************

The diagrams are generated from the Terraform configuration, so they match what is deployed. The security
controls they implement are on :ref:`security`.

.. figure:: ../assets/generated/central-hub-aws-network.png
   :align: center
   :alt: Users and Trust nodes outside AWS; Route 53, CloudFront, WAF, ACM and the UI bucket at the edge; a
         VPC with a NAT gateway and FL network load balancer in public subnets, and an internal application
         load balancer, three Fargate services, Cloud Map, an SSM bastion and an optional mock Trust in
         private subnets; GHCR reached through the NAT gateway.

   Request and FL paths.

There are **two entry points**, both AWS-managed:

Web and API traffic (browsers and each Trust's ``trust-api``)
   HTTPS through **CloudFront**, which carries a WAF, serves the UI from its bucket and forwards ``/api/*`` to an
   **internal** application load balancer. The ALB has no public address and accepts traffic only from
   CloudFront. CloudFront also strips the hub's internal service key header, so the FL server calls ``flip-api``
   over the hub's internal URL instead.

FL traffic (each Trust's FL client)
   A **public network load balancer** passes the mutual-TLS gRPC stream through untouched to the FL server. A
   layer-7 balancer would terminate the TLS that the FL frameworks authenticate with. Only the registered
   Trusts' public IP addresses are allowed in.

Terraform creates the DNS records for both names in the account's Route 53 hosted zone: the UI/API name
(``ALB_SUBDOMAIN``) and the FL name embedded in the trust kits (``NLB_SUBDOMAIN``).

**Compute is ECS Fargate in private subnets.** ``flip-api``, ``fl-api-net-1`` and ``fl-server-net-1`` run with no
public IPs and find each other through a private DNS namespace (``flip.local``). Container images come from GHCR
through a single NAT gateway. Operators connect through AWS Systems Manager Session Manager to a small bastion;
port 22 is never open.

.. figure:: ../assets/generated/central-hub-aws-data.png
   :align: center
   :alt: The three Fargate services with EFS, RDS Proxy, RDS PostgreSQL and VPC endpoints inside the VPC;
         four S3 buckets; and Cognito, KMS, CloudWatch Logs, SES, Parameter Store and Secrets Manager as
         regional services.

   Data and platform services.

**State lives in managed services:**

- **PostgreSQL (RDS)**, reached through RDS Proxy with a short-lived IAM token per connection. The application
  holds no database password, so rotating the master secret causes no outage.
- **EFS** holds the FL workspaces, certificates and job directories. Each net's participant kits are copied there
  from the kits bucket.
- **Four S3 buckets**, all encrypted with the hub's KMS key: model-file staging and scanned files, FL results,
  bundled apps, and participant kits.
- **Cognito** is the user pool, with TOTP MFA.
- **Secrets Manager** holds the ``FLIP_API`` secret (the AES key and the internal service key); **Parameter
  Store** holds plain configuration read at start-up.
- **SES** sends the application's emails.

VPC endpoints for Secrets Manager, SSM, CloudWatch Logs and S3 keep that traffic off the NAT gateway.

*********************
Full-stack deployment
*********************

*Who: FLIP admin, from a workstation with AWS SSO and GitHub CLI access.*

.. important::

   Two things must exist before the first run:

   - **The Terraform state bucket.** It is not created here; it comes from the ``terraform_ci_bootstrap`` module,
     together with the CI roles and the permissions boundary. In AI Centre's accounts the platform repositories
     apply it. In your own account, apply ``deploy/providers/AWS/ci`` first (see
     :gh:`deploy/providers/AWS/ci/README.md`).
   - **The internal service key** (FL server to hub) in the env file. The AWS Makefile will not run without it.
     Mint it from the repository root:

     .. code-block:: shell

        make generate-internal-service-key ENV_FILE=$(pwd)/.env.<env>

   Create each trust's kit file before the run too, so ``register-trusts`` has something to register (see
   :ref:`deploy-central-hub-register-trusts`).

.. note::

   Every ``make`` command on this page runs from ``deploy/providers/AWS`` unless it says otherwise.

Run the whole pipeline with one target:

.. code-block:: shell

   cd deploy/providers/AWS
   make full-deploy PROD=stag    # staging
   make full-deploy PROD=true    # production

``PROD`` selects the env file: ``stag`` uses ``.env.stag``, ``true`` uses ``.env.production``. Production also
turns on RDS deletion protection and a final snapshot.

``full-deploy`` runs these targets in order:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Target
     - What it does
   * - ``github-login``, ``aws-login``
     - Authenticate the GitHub CLI (for image pulls) and AWS SSO.
   * - ``init``
     - Initialise Terraform with the environment's state backend.
   * - ``import-persistent``
     - Import existing persistent resources (Cognito, S3, secrets) so Terraform does not replace them.
   * - ``generate-internal-service-key``
     - Check the internal service key and its hash are in sync.
   * - ``plan``, ``apply``
     - Apply the infrastructure changes.
   * - ``update-env``
     - Write Terraform outputs back into the env file.
   * - ``ssh-config``
     - Add SSM-based SSH entries to ``~/.ssh/config``.
   * - ``ansible-init``
     - Patch both hosts, install ``psql`` on the hub bastion, and install Docker, the AWS CLI, CloudWatch and the
       FL assets on the cloud Trust EC2. The FL kit is staged as ``Trust_1`` for now, because no slot has been
       assigned yet.
   * - ``deploy-centralhub``
     - Deploy the hub's Fargate services at the tip of the environment's branch, and publish the UI.
   * - ``register-trusts``
     - Register every kit file present for the environment on the hub, and write each trust's credentials into it.
   * - ``deploy-trust``
     - Re-stage the FL kit for the slot the hub assigned, seed the cloud trust's mock data, and start its services
       (see `Cloud trust data`_).
   * - ``status``
     - Run the health checks (see `Status check`_).

After ``register-trusts``, fill in the rest of each kit's hub-shared settings with ``make sync-trust-kit`` (see
:ref:`deploy-central-hub-register-trusts`).

Each hub deploy pins an immutable image tag. To redeploy, pin a specific build or roll back, see
:ref:`deploy-central-hub-deploys`.

.. tip::

   After a targeted Terraform change to the bastion alone, ``make provision-bastion PROD=<env>`` reinstalls its
   tooling without touching the Trust EC2.

.. _deploy-central-hub-aws-hub-only:

Hub-only deployment
===================

Use this when every trust runs on-prem, typically because the FL workloads need hardware the cloud Trust EC2 lacks
(it is a ``t3.xlarge`` with no GPU):

.. code-block:: shell

   cd deploy/providers/AWS
   make full-deploy-hub-only PROD=<stag|true>

It runs the same chain as ``full-deploy`` without the cloud-trust steps, and Terraform creates **no Trust EC2**.
``ssh-config`` adds no ``flip-trust`` entry, and the cloud-trust targets refuse to run against this environment.

.. warning::

   On an environment previously deployed with ``full-deploy``, the hub-only apply **destroys the existing Trust
   EC2**, and its data volumes are not kept. Re-run with ``DEPLOY_TRUST_EC2=true`` (the default) to bring one back.

Each on-prem trust then joins as described in :doc:`onboarding-a-trust`: register its kit
(:ref:`deploy-central-hub-register-trusts`), add its public IP to ``LOCAL_TRUST_PUBLIC_IPS`` and run
``make -C deploy/providers/AWS allow-local-trust-nlb PROD=<env>``, then start the stack on the trust host.

.. important::

   Register on-prem trusts with ``make -C deploy/providers/AWS register-trusts``, not the root
   ``make register-trust``. The root target runs inside a local ``flip-api`` container and cannot reach the hub's
   ECS task.

Several on-prem trusts can share one host if each kit uses its own ports and data directories. The shipped
``trust/.env.*.development.example`` kits show a working two-trust allocation.

UI-only deploys
===============

To redeploy just the UI, no Terraform is needed:

.. code-block:: shell

   make deploy-ui PROD=stag

This builds the UI from your working tree, uploads it to S3 and invalidates the CloudFront cache. CloudFront is the
only supported way to serve the UI.

***********************
Step-by-step deployment
***********************

To debug or run selected steps, run the same targets one at a time:

.. code-block:: shell

   export PROD=stag    # or: export PROD=true

   make github-login
   make aws-login
   make init
   make import-persistent
   make generate-internal-service-key
   make plan
   make apply
   make update-env
   make ssh-config
   make ansible-init
   make deploy-centralhub
   make register-trusts
   make deploy-trust KIT=<CODE>
   make status

``register-trusts`` must run after ``deploy-centralhub``, and ``deploy-trust`` after ``register-trusts``.

After ``register-trusts``, fill the rest of each kit file's hub-shared settings from the repository root:

.. code-block:: shell

   make sync-trust-kit KIT=<CODE> PROD=<env>

``update-env`` writes the database host, the database secret ARN and the two Cognito IDs into your env file.

Cloud trust data
================

``deploy-trust`` is more than one action. It re-stages the FL kit for the slot the hub assigned, stops the trust's
services, **seeds** the Trust EC2 with that trust's mock OMOP and Orthanc data, then starts the services again.
Seeding downloads about 1 GB from Hugging Face, at the data version pinned in ``trust/.data_version``, then loads the
OMOP vocabulary. It reads the slot number and database passwords from the kit file, which is why it must follow
``register-trusts``.

**The seed runs on every** ``deploy-trust``. A routine redeploy therefore downloads and loads the mock data again.
That costs minutes, not correctness: only the listed projects' rows are replaced, and Orthanc skips images it
already holds.

- To re-seed after a data version bump without a full redeploy:

  .. code-block:: shell

     make seed-trust-data KIT=<CODE>

- If an interrupted run left the DICOM vocabulary half-loaded, add ``FORCE_DICOM_VOCAB=1``. Otherwise the vocabulary
  reports itself present and is never reloaded.

************
Status check
************

.. code-block:: shell

   make status

This checks Terraform state and outputs, the network, EC2 health, database connectivity, Secrets Manager, S3,
Cognito, the Docker services on the Trust EC2 and the ECS services, the public endpoints, SSH access and CloudWatch
logging.

.. _deploy-central-hub-aws-ssm:

******************
SSH access via SSM
******************

The hub bastion and the cloud Trust EC2 sit in private subnets with **no inbound ports**; port 22 is never open.
SSH runs through an AWS Systems Manager Session Manager tunnel instead.

Install the Session Manager plugin
==================================

You need version 1.2.319.0 or later.

.. code-block:: shell

   # macOS
   brew install session-manager-plugin     # or: brew upgrade session-manager-plugin

   # Ubuntu / Debian
   curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" \
     -o session-manager-plugin.deb
   sudo dpkg -i session-manager-plugin.deb

   session-manager-plugin --version

For other systems, see the `AWS installation guide
<https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html>`_.

Connect
=======

Sign in to the account and region the instances run in, then write the SSH entries:

.. code-block:: shell

   export AWS_PROFILE=<profile> AWS_REGION=<region>
   aws sso login --profile $AWS_PROFILE
   make ssh-config

``ssh-config`` reads the instance IDs from Terraform and adds ``Host flip`` and ``Host flip-trust`` entries to
``~/.ssh/config``, using ``~/.ssh/host-aws``. Run it again after an ``apply`` that replaces an instance.

.. code-block:: shell

   ssh flip          # Central Hub bastion
   ssh flip-trust    # cloud trust, if deployed

To test the tunnel without SSH, ``aws ssm start-session --target $(terraform output -raw Ec2InstanceId)`` should
open a shell on the bastion.

Open the trust's web UIs
========================

.. code-block:: shell

   make forward-trust

This forwards the cloud trust's web UIs over SSM and prints their local URLs. Press ``Ctrl+C`` to close all
forwards.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Service
     - Local URL
     - Notes
   * - XNAT
     - ``http://localhost:8105``
     - Web UI
   * - Orthanc
     - ``http://localhost:8042``
     - Sign in with ``ORTHANC_USERNAME`` / ``ORTHANC_PASSWORD`` from the kit file
   * - trust-api docs
     - ``http://localhost:8020/docs``
     -
   * - imaging-api docs
     - ``http://localhost:8001/docs``
     -
   * - data-access-api docs
     - ``http://localhost:8010/docs``
     -
   * - Grafana
     - ``http://localhost:3000``
     - Observability dashboards

The hub's own UI and API need no forwarding: they are served through CloudFront.

.. note::

   ``forward-trust`` always forwards port 8105 for XNAT, the shipped default for ``XNAT_WEB_PORT``. ``XNAT_PORT``
   (the DICOM receiver) and ``XNAT_WEB_PORT`` must differ, and a kit from before the two were split sets only
   ``XNAT_PORT``: the next ``deploy-trust`` on it is refused with a message naming both. Add ``XNAT_WEB_PORT`` to the
   kit file and deploy again.

If SSH fails
============

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Symptom
     - Fix
   * - ``Unable to locate credentials``
     - Your SSO session has expired: ``aws sso login --profile $AWS_PROFILE``.
   * - ``SessionManagerPlugin not found`` or ``is not installed``
     - Install or upgrade the plugin (see above).
   * - ``InvalidInstanceID.NotFound``
     - Check the instance exists: ``terraform output Ec2InstanceId`` or ``terraform output TrustEc2InstanceId``,
       then run ``make ssh-config`` again.
   * - ``AccessDeniedException`` from ``aws ssm start-session``
     - Check the Session Manager permissions: your role needs ``ssm:StartSession``, and the instance's role needs
       the Session Manager policy, which Terraform attaches.
   * - The connection hangs
     - Check ``aws ssm describe-instance-information`` reports the instance ``Online`` and that it is running. No
       inbound SSH rule is needed.
   * - ``Unable to connect to SSM endpoint``
     - ``AWS_REGION`` does not match the region the instances run in.
   * - ``Bad ProxyCommand``
     - Regenerate the entries with ``make ssh-config``.

***************************
Destroy infrastructure
***************************

.. code-block:: shell

   make destroy

.. warning::

   This deletes the database **without a final snapshot**; in production, RDS deletion protection makes that delete
   fail. It asks you to type ``yes`` first. **Cognito**, **Secrets Manager**, the **S3 buckets**, the ACM
   certificates and Route 53 records are kept, and so is the VPC unless you pass ``PRESERVE_VPC=false`` (production
   always keeps it).

It removes only what it targets: the RDS database with its parameter and subnet groups, the Trust EC2 and the hub
bastion with their key pairs, the security groups, the ALB, the EC2 IAM role and the SES email templates, plus any
resources Terraform finds depend on them. Everything else stays. See :doc:`/sys-admin/admin-backup-and-restore`
before you run it.

***************
Troubleshooting
***************

Run ``make status`` first: it checks resource health, network connectivity, endpoints, containers and resource
usage.

.. seealso::

   - :ref:`ts-central-hub-aws` — common Central Hub problems and their fixes.
   - :gh:`deploy/providers/AWS/README.md` — maintainer notes: how the stack is built, the Terraform CI pipeline, the
     test suites and the design rationale.
