.. _deploy-central-hub-aws-lza:

####################################
Deploy the Central Hub on AWS (LZA)
####################################

The **platform-managed** deployment mode, for an AWS estate governed by the
`Landing Zone Accelerator <https://aws.amazon.com/solutions/implementations/landing-zone-accelerator-on-aws/>`_
(LZA): a platform team owns the network, the guardrails and a shared edge, and
FLIP's workload account is denied network-layer creation. It is the same
Terraform root as the self-contained mode, selected by ``PROD=lza`` (production)
or ``PROD=lza-stag`` (staging), which set the ``lza_managed_network`` flag; with
the flag off the resolved configuration is identical to before, so the
self-contained environments are never touched by LZA work (FLIP#749).

This page describes **what the mode changes and what the estate has to
provide**. The operator runbook — the environment file, the out-of-band
prerequisites and their commands, the apply sequence — is the "Deploying onto an
LZA estate (PROD=lza)" section of `deploy/providers/AWS/README.md
<https://github.com/londonaicentre/FLIP/blob/main/deploy/providers/AWS/README.md#deploying-onto-an-lza-estate-prodlza>`_,
and the estate-side configuration lives in the platform team's own repositories.
The prerequisites, IAM permissions, service authentication, schema changes and
email setup shared with the self-contained mode are on :ref:`deploy-central-hub`.

.. contents:: On this page
   :local:
   :depth: 2

************
Architecture
************

The pictures below are rendered when this documentation is built, from the same
`deploy/providers/AWS/architecture/central_hub.py <https://github.com/londonaicentre/FLIP/blob/develop/deploy/providers/AWS/architecture/central_hub.py>`_
script and node map as the self-contained pictures on :doc:`deploy-central-hub-aws`, with the
LZA-gated resources drawn and the legacy-gated ones left out. The networking account's edge and
the Transit Gateway are drawn from the handoff contract, not from Terraform in this repository.

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

**State is the same set of managed services.** RDS Proxy, EFS, the S3 buckets, Cognito, Secrets Manager,
Parameter Store, KMS, CloudWatch Logs and SES are provisioned and used exactly as in the self-contained
mode. Two placements differ: the database sits in the accelerator's isolated **data subnets** (local routes
only — RDS needs nothing beyond the VPC), and the interface endpoints are the estate's central ones rather
than in-account resources.

*********************
What the mode changes
*********************

With ``lza_managed_network`` set, the root module:

**Does not create**

- the VPC, subnets, internet gateway, NAT gateways and DHCP options;
- in-account VPC endpoints (interface endpoints are central; S3 and DynamoDB gateway endpoints are
  platform-provided);
- the in-account CloudFront distribution, its VPC origin and its WAF, and the public FL NLB — the estate's
  own edge replaces all three, and an internet-facing load balancer is impossible without an internet
  gateway;
- the internal ALB — the internal NLB fronts ``flip-api`` instead;
- the security-group drift monitor (CloudTrail → EventBridge → Lambda), which the organisation baseline
  covers.

**Discovers instead**

- the accelerator-provisioned VPC and its ``app`` and ``data`` subnets, by Name tag (``LZA_VPC_NAME``),
  matching every subnet of each kind so ones the platform team adds later appear on the next plan.

**Keeps unchanged**

- ECS Fargate, RDS and RDS Proxy, EFS, Cloud Map, Cognito, S3 with the hub's KMS key, Secrets Manager,
  Parameter Store and SES.

Two behavioural differences follow from the ALB's absence: the load balancer no longer filters to
``/api`` paths or answers a default 404 (CloudFront's behaviours and the WAF are the only layer-7 gate,
which is why the workload load balancer must stay reachable only from the edge), and idle connections
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
- a versioned, KMS-encrypted Terraform state bucket (``make create-backend PROD=lza`` creates it);
- an Identity Center permission set for the operator, with a matching ``lza-prod`` or ``lza-stag`` profile
  alias in ``~/.aws/config`` for the Makefile guard;
- the estate's central load-balancer access-logs bucket, which the workload stack writes to.

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

``PROD=lza`` selects ``.env.lza-prod``, production semantics (``TF_VAR_environment=prod``, so RDS deletion
protection and the final snapshot stay on) and deploys from ``origin/main``; ``PROD=lza-stag`` selects
``.env.lza-stag``, staging semantics and ``origin/develop``. Each keeps its own trust-kit suffix
(``trust/.env.<CODE>.lza-prod`` / ``.lza-stag``), so self-contained kits are never overwritten.

.. code-block:: shell

   cd deploy/providers/AWS
   export PROD=lza      # or: export PROD=lza-stag

   make init
   make plan
   make apply
   make deploy-centralhub
   make deploy-ui

.. note::

   ``full-deploy`` is the self-contained chain. Some of its steps — the cloud
   trust EC2, the SSH and Ansible provisioning — do not apply to a
   platform-managed deployment, and the chain, along with ``make status`` and
   ``make destroy``, is not yet exercised there. ``deploy-ui`` publishes the
   bundle without a CloudFront invalidation: the edge distribution is not in
   this account, so ``index.html`` is served with ``no-cache`` and the hashed
   assets as immutable instead.

***************
Further reading
***************

- `deploy/providers/AWS/README.md — Deploying onto an LZA estate
  <https://github.com/londonaicentre/FLIP/blob/main/deploy/providers/AWS/README.md#deploying-onto-an-lza-estate-prodlza>`_
  — the operator runbook: environment file, out-of-band prerequisites, the handoff variables.
- `FLIP#749 <https://github.com/londonaicentre/FLIP/issues/749>`_ — the migration that introduced the mode.
- :ref:`security` — the "Cloud infrastructure" controls, including the estate-level ones the accelerator adds.
- The AI Centre's estate configuration (``londonaicentre/lza``) and its edge stack
  (``londonaicentre/aicentre-lza-iac``) are private repositories.
