.. _deploy-central-hub-aws:

##############################
Deploy the Central Hub on AWS
##############################

The **self-contained** deployment mode: everything FLIP needs lives in one AWS
account, and Terraform builds the network as well as the services. It is the
default (``PROD=stag`` / ``PROD=true``) and the shape the open-source project
targets. The prerequisites, IAM permissions, service authentication, schema
changes and email setup that apply to both modes are on
:ref:`deploy-central-hub`; the platform-managed alternative is
:doc:`deploy-central-hub-aws-lza`.

.. contents:: On this page
   :local:
   :depth: 2

************
Architecture
************

The pictures below are rendered when this documentation is built, from
`deploy/providers/AWS/architecture/central_hub.py <https://github.com/londonaicentre/FLIP/blob/develop/deploy/providers/AWS/architecture/central_hub.py>`_
— a script kept next to the Terraform it depicts. A test in that tree pins every drawn node to a resource in
the ``.tf`` files, and every load-bearing resource to a drawn node, so the diagram cannot quietly fall out of
step with the infrastructure. The security controls it implements are on :ref:`security`.

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
and accepts traffic only from CloudFront's service security group. CloudFront also strips the hub's
internal-service-key header at the edge, so the FL server's callbacks to ``flip-api`` travel over the
hub-internal URL instead. FL clients connect to a **public network load balancer** instead: FL traffic is
mutual-TLS gRPC end to end, and a layer-7 balancer would terminate the TLS the FL frameworks authenticate
with, so the NLB passes TCP through untouched to the FL server. Its security group allow-lists the registered
Trusts' public addresses. Terraform creates the Route 53 records for both entry points — the UI/API name
(``ALB_SUBDOMAIN``) and the FL name the trust kits embed (``NLB_SUBDOMAIN``) — in the account's own hosted zone.

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
