.. _security:

########
Security
########

This page describes how FLIP addresses security at each layer of the platform:

- the network;
- the cloud estate the Central Hub runs on;
- the identity of every user and service;
- the boundary around clinical data;
- the federated learning process itself;
- the storage and transport of everything in between;
- the software supply chain the platform is built from.

It is written for partner trusts, information governance leads, and anyone assessing
FLIP before deploying it. It deliberately contains no exploitation detail.

FLIP is built on a single principle: **patient data does not leave the hospital that
holds it.** Models travel to the data, results come back aggregated, and every control
described below exists to keep that boundary intact.

FLIP is open source under Apache 2.0. The code, its full change history, and the
automated checks that run against every change are publicly inspectable.

*********************
Network and perimeter
*********************

**Trust systems accept no inbound connections from the internet or the Central Hub.**
Each participating trust runs FLIP services that reach *out* to the Central Hub to
collect work and report results. This is enforced in the infrastructure definitions
themselves rather than depending on configuration discipline: the AWS trust security
groups define no ingress rules at all. Operator access is via AWS Systems Manager
Session Manager, so port 22 is never opened.

**The one inbound connection in the design** applies only where FLIP is connected to a
trust's own PACS, which today means an on-premises deployment. FLIP asks the PACS for a
study, and the PACS opens a connection back to XNAT to deliver it, on the DICOM port
alone. It stays inside the trust's own network. AWS-hosted trusts have no such path. They
run FLIP's own bundled Orthanc, which XNAT reaches over the container network, so their
security groups keep no ingress rules at all.

**Only the Central Hub is internet-facing.** It sits behind CloudFront with modern TLS,
HSTS, AWS WAF managed rules, and an internal-only load balancer. Nothing
else in the platform is reachable from the public internet.

**A site-to-site VPN is available on request.** Trust-to-hub traffic is encrypted in
transit by default, and payloads carry their own authenticated encryption layer
(AES-256-GCM) on top of that. Where a trust's own policy calls for network-layer
separation as well, a site-to-site VPN between the trust's network and the hub VPC can be
provisioned. It carries all outbound polling and FL client traffic.

See :ref:`deploy-trust-node-network` for the firewall rules required at a trust host.

********************
Cloud infrastructure
********************

The Central Hub runs on AWS, architected to align with AWS's own reference guidance for
regulated workloads.

**The estate runs on the AWS Landing Zone Accelerator.** This is AWS's
reference implementation for organisations with elevated compliance requirements, and the
pattern AWS recommends for healthcare and public sector workloads. It provides:

- account separation;
- centrally managed guardrails;
- consistent logging and encryption baselines;
- a controlled path for images and dependencies entering the environment.

These apply uniformly across environments rather than being configured per service. The
Central Hub's own layout is on :ref:`deploy-central-hub-aws-lza`.

Within that estate:

- **Least-privilege identity.** Each service has its own IAM role scoped to named
  resources rather than wildcards. Compromise of one component therefore grants nothing
  beyond that component's own function.
- **Private by default.** Compute runs in private subnets with no public IP addresses.
- **Encryption under managed keys.** S3 storage, RDS, and EBS volumes are encrypted,
  under a customer-managed KMS key where the data is FLIP's own.
- **No standing database credential.** The production database is reached through RDS
  Proxy using a short-lived IAM authentication token minted per connection. There is no
  long-lived password to leak or rotate.
- **Infrastructure as code.** The environment is defined in Terraform/OpenTofu and
  validated automatically in CI, so what is deployed matches what was reviewed.
- **No static cloud credentials in automation.** CI authenticates to AWS with
  short-lived federated tokens rather than stored access keys.

*******************
Identity and access
*******************

**Authentication is layered.** In every deployed environment, sign-in uses AWS Cognito
with the SRP password protocol, which never transmits the password itself. The local
development backend (Keycloak) uses the OIDC password grant on loopback only, and the
production configuration refuses it at boot. Access tokens are verified on every request:

- the signature algorithm is pinned;
- the issuer and audience are checked;
- ID tokens presented in place of access tokens are rejected.

**Multi-factor authentication is mandatory.** MFA enrolment is verified at the
application boundary on *every* authenticated request, not only at the moment of login.
So a session cannot outlive the requirement:

- a user who has not enrolled cannot reach any protected function;
- an administrative MFA reset takes effect immediately.

(The second factor itself is presented at sign-in. What runs per-request is the
enrolment check.)

**Access is role-based and default-deny.** Users hold one of four defined roles:
**Admin**, **Researcher**, **Viewer**, or **Trust Admin**. A Trust Admin is held for one
trust, and its holder also has Researcher access. Each role carries an explicit permission set (see
:ref:`User Roles <rbac-roles>` for the full permission matrix). Permission is granted
rather than assumed, so a request with no matching grant is refused.

Role membership alone is not sufficient for project data. That additionally requires
membership of the specific project, re-checked on every access. There are two exceptions:
an Admin can access every project, and a Trust Admin can read a project staged at their
trust, so they can decide on it. Credential comparisons are constant-time, so response
timing cannot be used to guess a secret. Periodic review of accounts and removal of
dormant access is an operating commitment of the organisation running the hub, supported
by the platform's role and membership records.

**Role separation continues inside the trust.** Access control is not only a hub concern.
XNAT enforces its own roles. The ability to query and retrieve from the trust PACS is
restricted to the FLIP service account and to accounts explicitly granted the DQR role.
An ordinary XNAT account cannot pull imaging from PACS: retrieval happens as part of an
approved project's data import, not on demand by an individual user. A FLIP role
therefore never becomes an implicit route into the trust's wider imaging estate.

**Machine-to-machine access is separately controlled.**

- Each trust authenticates to the hub with its own ``TRUST_API_KEY``. The hub stores only
  a SHA-256 hash of it.
- Services within a trust authenticate to one another with a per-trust
  ``TRUST_INTERNAL_SERVICE_KEY``. It never reaches the Central Hub and is compared in
  constant time.

Credentials compromised at one trust cannot be replayed against another.

.. _trust-internal-service-authentication:

Trust-internal service authentication
=====================================

Trust-side APIs can retrieve cohorts and imaging, so Docker or Kubernetes network
reachability alone is not treated as authorisation. Every call from ``trust-api``,
``imaging-api``, or an FL client to ``imaging-api`` or ``data-access-api`` carries the
per-trust ``TRUST_INTERNAL_SERVICE_KEY`` in the configured header. Receivers compare it in
constant time before running the requested operation. Health endpoints remain
unauthenticated, so orchestrator liveness probes do not need access to the secret.

**Each trust has its own key, which never leaves the trust.**

- The key is minted during trust registration and written only to that trust's
  deployment kit.
- It is shared by the services inside one trust and never stored by the Central Hub.
- It is distinct from both the trust-to-hub API key and the hub-internal FL-server key.
- Each trust receives a different value, limiting the effect of disclosure to one trust.

Key rotation is performed by issuing a new trust kit and restarting the trust-side
services together, so callers and receivers change atomically.

**Recording or deleting a frozen cohort needs a second proof.** The FL client
legitimately holds the shared key, because it reads the approved cohort and pulls
imaging. The key alone therefore cannot separate reading a project's cohort from
*defining* it. So the two ``data-access-api`` routes that record or delete the frozen
cohort carry a second gate on top of the shared key: proof of possessing the
platform-wide payload-encryption key (``AES_KEY_BASE64``), which the trust also holds.

- ``trust-api`` sends that proof when it records an approved cohort, and
  ``data-access-api`` checks it.
- ``imaging-api`` also holds the key but never calls these routes. The FL client does not
  hold it.
- The proof is a one-way digest of the key, not the key itself, so the key never appears
  on the wire or in logs.
- A caller that presents a valid shared key but not this proof is refused.

No additional secret is provisioned. The control reuses a possession boundary that
already exists.

**************************
The clinical data boundary
**************************

Cohort queries execute inside the trust, against the trust's own OMOP database. Several
independent controls would each have to fail before anything unintended could execute:

- **Read-only role.** The query runs as a read-only database role with DML and DDL
  revoked. The database itself refuses to write, regardless of what the query says.
- **Parsed and re-emitted.** The query is parsed with ``sqlglot`` and re-emitted from its
  parsed form before execution, which breaks the injection taint chain.
- **Read-only statement types only.** This is decided from the parsed AST rather than by
  scanning for banned keywords.
- **One statement per request.** Multiple statements bundled into one request are
  rejected.
- **Schema and limits pinned.** Queries are pinned to the ``omop`` schema, with
  ``LIMIT``/``OFFSET`` restricted to literal values.
- **Small results suppressed.** A cohort with fewer distinct patients than
  ``COHORT_QUERY_THRESHOLD`` is suppressed whole. A genuine zero is deliberately
  indistinguishable from a small suppressed cohort, so a response cannot reveal that a
  handful of patients matched the query. Above the threshold, the age, sex and modality
  breakdowns group small categories, but the per-column counts of present and missing
  values are released without a small-number floor. The threshold is the trust's own
  disclosure floor (default 10), set by each trust in its deployment kit. Trusts need
  not agree on a shared value, and the hub cannot lower it.
- **Row-level data only for the approved cohort.** Row-level data is released only for the
  **cohort frozen at project approval**. Each Trust records the approved query and the
  patient and study identifiers it returned (no clinical values). From then on it serves
  only that query, restricted to those identifiers, ignoring any SQL supplied at request
  time. No patient or imaging study outside the approved cohort can therefore reach
  training, and training code cannot run queries of its own.

  The freeze is of patients, not records. An approved patient's records are read live, so
  one added after approval (a new measurement, visit or condition) is served. The
  national data opt-out is per patient, so it acts on that membership. A patient removed
  from the Trust's database, for instance after an opt-out, drops out on the next fetch,
  and a cohort that falls below the disclosure threshold stops being served.

This is achieved **without restricting researchers to a fixed menu of queries**:
arbitrary analytical SQL remains available. The constraint is on the shape and privilege
of the query, not on the questions that may be asked.

******************************
Federated learning and privacy
******************************

Federated learning is what makes FLIP possible: models travel to the data rather than
the reverse.

**Researcher-supplied training code runs on trust hardware with access to that trust's
data.** That is the nature of federated learning, and it is why the surrounding controls
matter:

- Model files are checked before use. Python source additionally gets a non-blocking
  static-analysis pass flagging common risky patterns.
- The container that runs researcher code is hardened.
- FL clients deliberately hold **no Central Hub credentials**, so compromising one yields
  no access to the wider platform.

**Arbitrary Python logic in uploaded training code is not sandboxed at runtime.** The
static-analysis scan above is advisory, not a gate: it does not stop obfuscated or
otherwise-undetected code from running. The accepted control for this class of risk is
uploader self-review, supported by RBAC on who can upload, rather than a
platform-enforced review process. There is no GitHub-review step in the upload path
today.

This is a deliberate decision, not an oversight. Runtime enforcement options were
weighed: an import allowlist, RestrictedPython, OS-level sandboxing. Each was rejected as
either easily bypassed or a poor fit for legitimate ML code. The full reasoning is in
FLIP#877 (tracking GHSA-8465), so it does not need re-deciding the next time this class
of finding comes up.

**The FL API fetches app bundles only from the hub's object store.** When a job is
submitted, the net's FL API downloads the bundled app server-side, from presigned S3 URLs
handed to it by the Central Hub API. The FL API authenticates no caller of its own: it
trusts its position on the hub's internal network. That download is therefore guarded as
a server-side request forgery surface:

- URLs may never name a private, loopback or link-local address as an IP literal, in any
  spelling.
- An explicit origin allow-list, ``BUNDLE_URL_ALLOWED_ORIGINS``, is consulted before any
  name is resolved. Every deployment derives it from the object store the hub presigns
  against: on AWS the regional S3 endpoint (``https://s3.<region>.amazonaws.com``), in
  local development the RustFS container in the dev compose. So the FL API will fetch
  from that one origin and nothing else.
- Without a list, the URL must be https on the default port. A DNS name is resolved with
  every answer held to the same range check, failing closed if it cannot be resolved.

**The allow-list is the control that matters.** The range check on resolved addresses
cannot, on its own, close a DNS-rebinding race between the check and the fetch. An FL API
started without the allow-list logs a warning naming what that leaves open.

**FL traffic is mutually authenticated.** Both supported backends, NVIDIA FLARE and
Flower, run over TLS with per-participant certificates issued during network
provisioning. So the FL server and each client authenticate *each other*, rather than one
side trusting the network. A client cannot join a training network without a valid
provisioned identity.

**Privacy filtering of model updates differs by FL backend** (see
:ref:`fl-update-privacy`):

- **NVIDIA FLARE.** Every training template clips and sparsifies each update before it
  leaves the trust, and an uploaded app cannot turn this off. It is **statistical
  clipping and sparsification, not formal differential privacy**: there is no calibrated
  noise and no privacy budget. A trust can also enforce its own filter as site policy,
  which a job cannot bypass.
- **Flower.** Local differential privacy (clipping plus calibrated noise) is available,
  but it is **opt-in**: the researcher's uploaded code must enable it. Code that does not
  sends raw updates, and only review of the uploaded code catches that. There is no
  site-enforced filter. The template's noise settings are demonstration values, not a
  defensible privacy budget.

Describing either as a differential privacy guarantee to an ethics committee or
information governance panel would misstate it.

***************************
Data in transit and at rest
***************************

**All traffic is encrypted in transit.** Every connection between a trust and the Central
Hub runs over HTTPS, outbound from the trust only. On top of that transport encryption,
task payloads are themselves encrypted before they are handed to the transport. So the
payload body is never carried in the clear inside an established session.

**The payload layer is authenticated encryption (AES-256-GCM), checked end to end.** TLS
terminates at CloudFront and again at the load balancer in front of the hub, so transport
integrity is only ever hop by hop. The payload's authentication tag, by contrast, is
verified by the receiving service itself. That includes the leg from an FL client to the
trust's own services, which has no TLS at all.

- A payload altered at any hop or boundary fails decryption outright rather than
  yielding altered content.
- The tag also binds the payload's purpose, so an instruction the hub issued for one
  action cannot be re-labelled as another.

**What the payload layer does not cover.** Stated precisely, because this page exists to
be relied on:

- It covers what the hub sends to a trust and what a trust's FL client presents to its
  own services. A trust's responses back to the hub (cohort statistics, task outcomes)
  travel as plain JSON under TLS alone.
- It still uses a **single platform-wide symmetric key**. The tag therefore proves that a
  holder of the platform key produced the payload, not which participant did. A
  compromised participant could still forge payloads for another.

**Per-trust keys are in delivery, not yet a shipped control.** The planned upgrade is:

- each trust's traffic protected by its own key, so a compromise at one trust can neither
  read nor forge another's;
- keys carrying identifiers, so they can be rotated without a synchronised cutover.

**At rest**, model and results storage uses S3 with:

- managed encryption under a customer-managed KMS key;
- versioning;
- blocked public access;
- HTTPS-only bucket policies;
- access logging.

RDS storage and EC2 root volumes are encrypted, and database connections require TLS on
both hops.

**Access links expire.** Pre-signed URLs used to upload and download model files are
time-limited, with a hard ceiling enforced centrally. Such a link is a capability against
the bucket in either direction.

**Credentials are designed to be rotated.** A trust's API key and its trust-internal
service key are issued at registration and can be re-issued without redeploying the
platform. Rotating the payload-encryption key currently means re-issuing the shared key
to every participant at once. Removing that coordination is part of the per-trust key
work described above: each trust is given its own identified key, so a new key can be
introduced and an old one retired without a synchronised cutover.

**The trust imaging archive requires authentication to start.**

- Orthanc will not run without credentials configured.
- An automated check verifies that authentication is actually enforced before any new
  image is published.
- Interfaces with no consumer are not enabled.

**********************************
Diagnostics and disclosure control
**********************************

Error messages are a quiet disclosure route. An unhandled error can return database
structure, internal hostnames, or fragments of a failing query to whoever triggered it.

The platform's target here is a fixed client-facing message accompanied by a
**correlation identifier**:

- The identifier is generated server-side and never accepted from the caller.
- It is recorded alongside the full technical detail in the internal logs. A user who
  encounters an error quotes the identifier, and an engineer finds exactly what happened.
  Diagnosability is preserved without disclosing internals.
- Automated checks in CI keep raw exception text from reappearing in responses.

**That mechanism is in delivery, not yet a shipped control.** Today some error paths still
return the underlying exception text to the caller, and the CI guard does not yet exist.

What holds today is narrower: log lines are written to avoid carrying sensitive values
themselves. Counts, file names, and hashed object identifiers stand in for pre-signed URLs
and storage keys.

****************************************
Software supply chain and change control
****************************************

- **Secret scanning** runs on every push and pull request, with a scheduled
  full-repository sweep. Pre-commit hooks mirror it before a commit is created.
- **A 72-hour dependency cooldown** prevents newly published third-party packages being
  adopted immediately. It is a direct defence against compromised-package attacks,
  enforced by ``uv`` and ``npm`` configuration and backstopped by a CI gate.
- **Automated dependency vulnerability alerting** is enabled.
- **Container images publish only after their tests pass**, so a failing build cannot
  become a deployable artefact. Deployments pin immutable commit-sha tags rather than
  moving labels.
- **Every change is peer-reviewed**, with automated acceptance checks, DCO sign-off, and a
  protected mainline.
- **Infrastructure is defined as code** and validated automatically in CI.

*************************************
Assurance and vulnerability reporting
*************************************

FLIP is subject to a commissioned penetration test and to recurring structured security
review supported by automated analysis. Findings are tracked and re-verified rather than
left to age. Automated security checking runs continuously in the delivery pipeline, so
regressions are caught at the point of change.

**Vulnerability reports are welcome.** FLIP publishes a security policy with a private
reporting route and a coordinated disclosure process: see :gh:`SECURITY.md` in the
repository. Please do not open a public issue for a suspected vulnerability.
