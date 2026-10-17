.. _deploy-central-hub-aws-lza:

####################################
Deploy the Central Hub on AWS (LZA)
####################################

The Central Hub deploys onto an AWS estate governed by the
`Landing Zone Accelerator <https://aws.amazon.com/solutions/implementations/landing-zone-accelerator-on-aws/>`_
(LZA): a platform team owns the network, the guardrails and a shared edge, and
FLIP's workload account is denied network-layer creation (FLIP#749). It is the
only shape the Terraform root supports: the self-contained single-account mode
was removed in FLIP#1479.

This page describes **the architecture and what the estate has to provide**.
The operator runbook — the environment file, the out-of-band prerequisites and
their commands, the apply sequence — is the "Deploying onto an LZA estate"
section of `deploy/providers/AWS/README.md
<https://github.com/londonaicentre/FLIP/blob/main/deploy/providers/AWS/README.md#deploying-onto-an-lza-estate>`_,
and the estate-side configuration lives in the platform team's own repositories.
The prerequisites, IAM permissions, service authentication, schema changes and
email setup are on :ref:`deploy-central-hub`.

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
step with the infrastructure. The networking account's edge and the Transit Gateway are drawn from the
handoff contract, not from Terraform in this repository. The security controls it implements are on
:ref:`security`.

.. figure:: ../assets/generated/central-hub-aws-lza-network.png
   :align: center
   :alt: Users and Trust nodes outside AWS; in the networking account an edge CloudFront with WAF, a web
         relay NLB, an internet-facing edge NLB, and a Network Firewall in front of a Transit Gateway; in
         the workload account the accelerator-provisioned VPC with app subnets holding one internal NLB,
         three Fargate services, Cloud Map, an SSM bastion and an optional mock Trust, a private hosted
         zone, the UI bucket, the SSM handoff parameters and an ECR pull-through cache reaching GHCR.

   Request and FL paths on an LZA estate.

**One front door, in another account.** Browsers and each Trust's ``trust-api`` reach the hub over HTTPS
through a CloudFront distribution that the platform team runs in the estate's **networking account**. It
carries the WAF, serves the UI bucket through a cross-account origin access control, and forwards ``/api/*``
to a small relay load balancer beside it — a CloudFront VPC origin can only point at a load balancer in its
own account. FL clients connect to an internet-facing **edge NLB** in that same account (TCP, port 8002 for
production and 8003 for staging, since the two share one listener set). Both then cross the estate's
**Transit Gateway** and central **Network Firewall** into the workload account.

**One internal NLB anchors both legs.** Inside the workload account the accelerator-provisioned VPC has no
internet gateway and no NAT, so nothing in it can be public. Web and FL traffic both land on a single
internal network load balancer whose IP addresses are fixed per subnet (``.251``); a ``:443`` listener
forwards to ``flip-api`` and a ``:8002`` listener passes the mutual-TLS gRPC stream through untouched to the
FL server. Because those addresses never change, the edge registers them once and no synchronisation runs
at runtime. A private hosted zone resolves the FL server's bare name to the same addresses for callers
inside the VPC.

**No internet egress.** Container images come from an in-account **ECR pull-through cache** (a ``ghcr/``
prefix mirroring GHCR and a credential-less ``ecr-public/`` prefix) over the estate's central ``ecr``
endpoints, so the workload account never reaches the internet; the platform team's interface endpoints for
Secrets Manager, Cognito, SES, SSM and CloudWatch Logs serve the rest. Operator access stays AWS Systems
Manager Session Manager to the bastion: no security group opens port 22.

.. figure:: ../assets/generated/central-hub-aws-lza-data.png
   :align: center
   :alt: The three Fargate services with EFS and RDS Proxy in the app subnets and RDS PostgreSQL in the
         isolated data subnets of the accelerator-provisioned VPC; central interface endpoints in the
         networking account; five S3 buckets; and Cognito, KMS, CloudWatch Logs, SES, Parameter Store,
         Secrets Manager and an ACM certificate as regional services.

   Data and platform services on an LZA estate.

**Compute is ECS Fargate in the app subnets.** ``flip-api``, ``fl-api-net-1`` and ``fl-server-net-1`` run
as Fargate tasks with no public IPs, discovered by name through a Cloud Map private DNS namespace
(``flip.local``).

**State lives in managed services.** ``flip-api`` reaches PostgreSQL through **RDS Proxy** with a short-lived
IAM token minted per connection — the application holds no database password, and rotating the master
secret is not an outage. The database sits in the accelerator's isolated **data subnets** (local routes only
— RDS needs nothing beyond the VPC). The FL tasks share **EFS** access points for their workspaces,
certificates and job directories; the provisioning task copies each net's participant kits there from the
kits bucket. The **S3** buckets separate the model-file staging and scanned prefixes, FL results, bundled
apps and participant kits, all encrypted with the hub's KMS key. **Cognito** is the user pool (with TOTP
MFA); **Secrets Manager** holds the ``FLIP_API`` secret (AES key and internal service key); **Parameter
Store** carries the plain configuration tasks read at start-up; **SES** sends the application's own emails.
The interface endpoints are the estate's central ones.

*****************************
What the root does not create
*****************************

The root module creates none of:

- the VPC, subnets, internet gateway, NAT gateways and DHCP options;
- VPC endpoints (interface endpoints are central; S3 and DynamoDB gateway endpoints are platform-provided);
- a CloudFront distribution, a WAF or an internet-facing load balancer — the estate's own edge replaces them,
  and an internet-facing load balancer is impossible without an internet gateway;
- an ALB — the internal NLB fronts ``flip-api``;
- a security-group drift monitor — the organisation baseline covers it.

It **discovers** the accelerator-provisioned VPC and its ``app`` and ``data`` subnets by Name tag
(``LZA_VPC_NAME``), matching every subnet of each kind so ones the platform team adds later appear on the
next plan. ECS Fargate, RDS and RDS Proxy, EFS, Cloud Map, Cognito, S3 with the hub's KMS key, Secrets
Manager, Parameter Store and SES are its own.

Two behaviours follow from fronting ``flip-api`` with an NLB rather than an ALB: the load balancer does not
filter to ``/api`` paths or answer a default 404 (CloudFront's behaviours and the WAF are the only layer-7
gate, which is why the workload load balancer must stay reachable only from the edge), and idle connections
time out after 350 seconds rather than 60.

***********************************
What the platform team must provide
***********************************

Each FLIP environment needs its **own workload account** — the stack's resource names and the handoff
parameters are fixed per account, so two environments cannot share one. Before the first ``plan`` the
account, and the estate around it, need:

- a VPC with ``app`` and ``data`` subnets across two availability zones, a Transit Gateway attachment
  spanning both, and a route from the app subnets to the Transit Gateway;
- central interface endpoints reachable from the app subnets — ``ecr.api`` and ``ecr.dkr`` for image
  pulls, Secrets Manager, Cognito, SES, SSM and CloudWatch Logs — plus S3 and DynamoDB gateway endpoints;
- the edge in the networking account (the CloudFront distribution with its relay, and the edge NLB with a
  listener port for this environment) and firewall rules admitting the edge's address ranges to the
  workload load balancer's web and FL ports;
- ECR pull-through cache rules, ``ghcr/`` (backed by a read-only GHCR token held in Secrets Manager) and
  ``ecr-public/``;
- the Terraform-CI bootstrap: the Terraform state bucket, the GitHub OIDC provider and the CI plan and apply
  roles. The platform repository applies them from FLIP's ``terraform_ci_bootstrap`` module, pinned to a FLIP
  commit. The permissions boundary every FLIP role carries is the accelerator's own
  ``AICentre-WorkloadRoleBoundary``, which an SCP requires on every workload role and which the Makefile
  selects by default; the module references it rather than declaring FLIP's. The bucket is versioned, blocks public
  access, refuses unencrypted transport and keeps a bounded version history; it is encrypted with S3-managed
  keys, or on production with the AWS-managed KMS key;
- an Identity Center permission set for the operator, with a matching ``lza-prod`` or ``lza-stag`` profile
  alias in ``~/.aws/config`` for the Makefile guard.

That platform layer (the Transit Gateway, the central firewall, endpoints and NAT, and the edge) is
a shared estate cost that bills to the platform team's accounts rather than to the workload account;
:ref:`deploy-central-hub-costs` gives its rough size next to the workload account's own bill.

**********************
Two-phase edge handoff
**********************

The edge is built *from* the workload stack's outputs, so the two are applied in a fixed order. The
first workload ``apply`` publishes the handoff parameters (``/flip/networking/*``: the internal NLB's
fixed IP addresses, the web and FL ports, the load balancer's DNS name) that the edge stack reads. At that
point the two edge variables in the environment file are still empty: the UI bucket's policy then grants
no principal — fail-closed, the edge simply cannot read the bucket yet — and the UI origin is a
placeholder. Once the platform team has applied the edge, set the distribution's domain and ARN in the
environment file and re-apply: that grants the cross-account read and points bucket CORS and the Cognito
callback URLs at the edge. Later changes to the workload load balancer follow the same order, networking
stack first.

*********
Deploying
*********

**CI applies this estate.** The Terraform pipeline plans the LZA staging root on
every pull request into ``main`` or ``develop`` touching ``deploy/providers/AWS/**``,
applies it on a push to ``develop``, and plans nightly to report drift — all
authenticated by GitHub OIDC with no long-lived AWS keys. Pointing a GitHub
environment at an account is a value change (the ``TF_HUB`` variable plus the two role
ARNs), described under "Repointing CI at the LZA accounts" in the deployment
README. Production is on LZA too (`FLIP#1199
<https://github.com/londonaicentre/FLIP/issues/1199>`_, repointed 2026-10-06):
merges to ``main`` apply to LZA production.

``HUB=prod`` selects ``.env.prod``, production semantics (``TF_VAR_environment=prod``, so RDS deletion
protection and the final snapshot stay on) and deploys from ``origin/main``; ``HUB=stag`` selects
``.env.stag``, staging semantics and ``origin/develop``. Trust kits follow the same token
(``trust/.env.<CODE>.prod`` / ``.stag``).

The same targets run from a laptop as the **break-glass** path — when CI is wedged,
and for the one-off account bootstrap steps that must precede the first CI plan:

.. code-block:: shell

   cd deploy/providers/AWS
   export AWS_PROFILE=lza-stag   # or: lza-prod
   export HUB=stag               # or: HUB=prod

   make init
   make plan
   make apply
   make deploy-centralhub
   make deploy-ui

.. warning::

   On an account CI applies, a laptop ``apply`` is reverted by the next pipeline
   run and shows up as drift in between. Use it only as a recovery action; see
   "Break-glass" in the deployment README for the conditions and the per-estate
   commands.

.. note::

   ``full-deploy`` and ``full-deploy-hub-only``, along with ``make status`` and
   ``make destroy``, are not yet exercised on an LZA estate. ``deploy-ui`` publishes the
   bundle without a CloudFront invalidation: the edge distribution is not in
   this account, so ``index.html`` is served with ``no-cache`` and the hashed
   assets as immutable instead.

***************
Further reading
***************

- `deploy/providers/AWS/README.md — Deploying onto an LZA estate
  <https://github.com/londonaicentre/FLIP/blob/main/deploy/providers/AWS/README.md#deploying-onto-an-lza-estate>`_
  — the operator runbook: environment file, out-of-band prerequisites, the handoff variables.
- `FLIP#749 <https://github.com/londonaicentre/FLIP/issues/749>`_ — the migration onto LZA.
- :ref:`security` — the "Cloud infrastructure" controls, including the estate-level ones the accelerator adds.
- The AI Centre's estate configuration (``londonaicentre/lza``) and its edge stack
  (``londonaicentre/aicentre-lza-iac``) are private repositories.
