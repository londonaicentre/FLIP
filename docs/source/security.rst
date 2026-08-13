.. _security:

########
Security
########

This page describes how FLIP addresses security at each layer of the platform: the
network, the cloud estate the Central Hub runs on, the identity of every user and
service, the boundary around clinical data, the federated learning process itself, the
storage and transport of everything in between, and the software supply chain the
platform is built from.

It is written for partner trusts, information governance leads, and anyone assessing
FLIP before deploying it. It deliberately contains no exploitation detail.

FLIP is built on a single principle: **patient data does not leave the hospital that
holds it.** Models travel to the data, results come back aggregated, and every control
described below exists to keep that boundary intact.

FLIP is open source under Apache 2.0 — the code, its full change history, and the
automated checks that run against every change are publicly inspectable.

*********************
Network and perimeter
*********************

**Trust systems accept no inbound connections.** Each participating trust runs FLIP
services that reach *out* to the Central Hub to collect work and report results.
Nothing on the internet can open a connection to a trust's FLIP services. This is
enforced in the infrastructure definitions themselves — the security groups permit no
inbound traffic at all — rather than depending on configuration discipline.
Operator access is via AWS Systems Manager Session Manager, so port 22 is never
opened.

**Only the Central Hub is internet-facing.** It sits behind CloudFront with modern TLS,
HSTS, AWS WAF managed rules, and an internal-only Application Load Balancer. Nothing
else in the platform is reachable from the public internet.

**A site-to-site VPN is available on request.** Trust-to-hub traffic is encrypted in
transit by default, and payloads carry their own encryption layer on top of
that. Where a trust's own policy calls for network-layer separation as well, a
site-to-site VPN between the trust's network and the hub VPC can be provisioned,
carrying all outbound polling and FL client traffic.

See :ref:`deploy-flip-node-on-prem` for the firewall rules required at a trust host.

********************
Cloud infrastructure
********************

The Central Hub runs on AWS, architected to align with AWS's own reference guidance
for regulated workloads.

**The estate is being consolidated onto the AWS Landing Zone Accelerator** — AWS's
reference implementation for organisations with elevated compliance requirements, and
the pattern AWS recommends for healthcare and public sector workloads. It provides
account separation, centrally managed guardrails, consistent logging and encryption
baselines, and a controlled path for images and dependencies entering the environment,
applied uniformly across environments rather than configured per service.

Within that estate:

- **Least-privilege identity.** Each service has its own IAM role scoped to named
  resources rather than wildcards, so compromise of one component grants nothing
  beyond that component's own function.
- **Private by default.** Compute runs in private subnets with no public IP addresses.
- **Encryption under managed keys.** S3 storage, RDS, and EBS volumes are encrypted,
  under a customer-managed KMS key where the data is FLIP's own.
- **No standing database credential.** The production database is reached through RDS
  Proxy using a short-lived IAM authentication token minted per connection, so there is
  no long-lived password to leak or rotate.
- **Infrastructure as code.** The environment is defined in Terraform/OpenTofu and
  validated automatically in CI, so what is deployed matches what was reviewed.
- **No static cloud credentials in automation.** CI authenticates to AWS with
  short-lived federated tokens rather than stored access keys.

*******************
Identity and access
*******************

**Authentication is layered.** Sign-in uses AWS Cognito with the SRP password protocol,
which never transmits the password itself. Access tokens are verified on every request:
the signature algorithm is pinned, the issuer and audience are checked, and ID tokens
presented in place of access tokens are rejected.

**Multi-factor authentication is mandatory.** MFA is enforced at the application
boundary on *every* authenticated request, not only at the moment of login, so a
session cannot outlive the requirement. A user who has not enrolled cannot reach any
protected function, and an administrative MFA reset takes effect immediately.

**Access is role-based and default-deny.** Users hold one of three defined roles —
**Admin**, **Researcher**, or **Viewer** — each carrying an explicit permission set (see
:ref:`User Roles <rbac-roles>` for the full permission matrix). Permission is granted
rather than assumed: a request with no matching grant is refused. Role membership alone
is not sufficient for project data, which additionally requires membership of that
specific project, re-checked on every access. Credential comparisons are constant-time,
so response timing cannot be used to guess a secret. Access is reviewed annually and
dormant accounts are removed.

**Role separation continues inside the trust.** Access control is not only a hub
concern. XNAT enforces its own roles, and the ability to query and retrieve from the
trust PACS is restricted to the FLIP service account and to accounts explicitly granted
the DQR role. An ordinary XNAT account cannot pull imaging from PACS — retrieval happens
as part of an approved project's data import, not on demand by an individual user. A
FLIP role therefore never becomes an implicit route into the trust's wider imaging
estate.

**Machine-to-machine access is separately controlled.** Each trust authenticates to the
hub with its own ``TRUST_API_KEY``, of which the hub stores only a SHA-256 hash.
Services within a trust authenticate to one another with a per-trust
``TRUST_INTERNAL_SERVICE_KEY`` that never reaches the Central Hub, compared in constant
time. Credentials compromised at one trust cannot be replayed against another.

.. _trust-internal-service-authentication:

Trust-internal service authentication
=====================================

Trust-side APIs can retrieve cohorts and imaging, so Docker or Kubernetes network
reachability alone is not treated as authorisation. Every call from ``trust-api``,
``imaging-api``, or an FL client to ``imaging-api`` or ``data-access-api`` carries the
per-trust ``TRUST_INTERNAL_SERVICE_KEY`` in the configured header. Receivers compare it
in constant time before running the requested operation. Health endpoints remain
unauthenticated so orchestrator liveness probes do not need access to the secret.

The key is minted during trust registration and written only to that trust's deployment
kit. It is shared by the services inside one trust, never stored by the Central Hub, and
is distinct from both the trust-to-hub API key and the hub-internal FL-server key. Each
trust receives a different value, limiting the effect of disclosure to one trust. Key
rotation is performed by issuing a new trust kit and restarting the trust-side services
together so callers and receivers change atomically.

**************************
The clinical data boundary
**************************

Cohort queries execute inside the trust, against the trust's own OMOP database. Several
independent controls would each have to fail before anything unintended could execute:

- the query runs as a read-only database role with DML and DDL revoked — the database
  itself refuses to write, regardless of what the query says;
- the query is parsed with ``sqlglot`` and re-emitted from its parsed form before
  execution, which breaks the injection taint chain;
- only read-only statement types are permitted, decided from the parsed AST rather than
  by scanning for banned keywords;
- multiple statements bundled into one request are rejected;
- queries are pinned to the ``omop`` schema, with ``LIMIT``/``OFFSET`` restricted to
  literal values;
- results below ``COHORT_QUERY_THRESHOLD`` are suppressed, and a genuine zero is
  deliberately indistinguishable from a small suppressed count, so a response cannot
  reveal that a handful of patients matched — the threshold is the trust's own
  disclosure floor (default 10), set by each trust in its deployment kit: trusts need
  not agree on a shared value, and the hub cannot lower it;
- cached results are scoped to the requesting project and expire in minutes, so no
  project is served another's data and no result outlives a withdrawal of consent or a
  correction to a record.

This is achieved **without restricting researchers to a fixed menu of queries** —
arbitrary analytical SQL remains available. The constraint is on the shape and privilege
of the query, not on the questions that may be asked.

******************************
Federated learning and privacy
******************************

Federated learning is what makes FLIP possible: models travel to the data rather than
the reverse.

**Researcher-supplied training code runs on trust hardware with access to that trust's
data.** That is the nature of federated learning, and it is why the surrounding controls
matter. Model files are checked before use — Python source additionally gets a
non-blocking static-analysis pass flagging common risky patterns — and the container
that runs researcher code is hardened; FL clients deliberately hold **no Central Hub
credentials**, so compromising one yields no access to the wider platform.

**Arbitrary Python logic in uploaded training code is not sandboxed at runtime.** The
static-analysis scan above is advisory, not a gate: it does not stop obfuscated or
otherwise-undetected code from running. The accepted control for this class of risk is
uploader self-review — supported by RBAC on who can upload — rather than a platform-enforced
review process; there is no GitHub-review step in the upload path today. This is a
deliberate decision (weighed against runtime enforcement options — an import allowlist,
RestrictedPython, OS-level sandboxing — each rejected as either easily bypassed or a poor
fit for legitimate ML code), not an oversight — see FLIP#877 (tracking GHSA-8465) for the
full reasoning, so it does not need re-deciding the next time this class of finding comes up.

**FL traffic is mutually authenticated.** Both supported backends — NVIDIA FLARE and
Flower — run over TLS with per-participant certificates issued during network
provisioning, so the FL server and each client authenticate *each other* rather than one
side trusting the network. A client cannot join a training network without a valid
provisioned identity.

**Model updates are filtered before they leave a trust.** A privacy filter is applied to
training updates by default, so the aggregating server never sees a raw update. For
governance purposes this should be described precisely: it is **statistical clipping and
sparsification of model updates, not formal differential privacy** — there is no
calibrated noise and no privacy budget. It is a meaningful protection and it is on by
default, but describing it as differential privacy to an ethics committee or information
governance panel would misstate it. Formally differentially private aggregation is on the
roadmap.

***************************
Data in transit and at rest
***************************

**All traffic is encrypted in transit.** Every connection between a trust and the
Central Hub runs over HTTPS, outbound from the trust only. On top of that transport
encryption, task payloads are themselves encrypted before they are handed to the
transport, so the payload body is never carried in the clear inside an established
session. Stated precisely, because this page exists to be relied on: today the payload
layer uses a **single platform-wide symmetric key**, and message integrity is provided
by the TLS transport rather than by the payload cipher itself. An upgrade to
**authenticated encryption with per-trust keys** — tampering makes decryption fail
outright, each trust's traffic is protected by its own key so a compromise at one trust
exposes no other's, and keys carry identifiers so they can be rotated without a
synchronised cutover — is in delivery, not yet a shipped control.

**At rest**, model and results storage uses S3 with managed encryption under a
customer-managed KMS key, versioning, blocked public access, HTTPS-only bucket policies,
and access logging. RDS storage and EC2 root volumes are encrypted, and database
connections require TLS on both hops.

**Access links expire.** Pre-signed URLs used to upload and download model files are
time-limited, with a hard ceiling enforced centrally, because such a link is a
capability against the bucket in either direction.

**Credentials are designed to be rotated.** A trust's API key and its trust-internal
service key are issued at registration and can be re-issued without redeploying the
platform. Rotating the payload-encryption key currently means re-issuing the shared
key to every participant at once; removing that coordination — by giving each trust
its own identified key, so a new key can be introduced and an old one retired without
a synchronised cutover — is part of the per-trust key work described above.

**The trust imaging archive requires authentication to start.** Orthanc will not run
without credentials configured, an automated check verifies that authentication is
actually enforced before any new image is published, and interfaces with no consumer are
not enabled.

**********************************
Diagnostics and disclosure control
**********************************

Error messages are a quiet disclosure route: an unhandled error can return database
structure, internal hostnames, or fragments of a failing query to whoever triggered it.

The platform's target here is a fixed client-facing message accompanied by a
**correlation identifier**: generated server-side, never accepted from the caller, and
recorded alongside the full technical detail in the internal logs, so a user who
encounters an error quotes the identifier and an engineer finds exactly what happened —
diagnosability preserved without disclosing internals — with automated checks in CI
keeping raw exception text from reappearing in responses. That mechanism is **in
delivery, not yet a shipped control**: today some error paths still return the
underlying exception text to the caller, and the CI guard does not yet exist. What
holds today is narrower: log lines are written to avoid carrying sensitive values
themselves — counts, file names, and hashed object identifiers stand in for pre-signed
URLs and storage keys.

****************************************
Software supply chain and change control
****************************************

- **Secret scanning** runs on every push and pull request, with a scheduled
  full-repository sweep, mirrored by pre-commit hooks that run before a commit is
  created.
- **A 72-hour dependency cooldown** prevents newly published third-party packages being
  adopted immediately — a direct defence against compromised-package attacks — enforced
  by ``uv`` and ``npm`` configuration and backstopped by a CI gate.
- **Automated dependency vulnerability alerting** is enabled.
- **Container images publish only after their tests pass**, so a failing build cannot
  become a deployable artefact, and deployments pin immutable commit-sha tags rather
  than moving labels.
- **Every change is peer-reviewed**, with automated acceptance checks, DCO sign-off, and
  a protected mainline.
- **Infrastructure is defined as code** and validated automatically in CI.

*************************************
Assurance and vulnerability reporting
*************************************

FLIP is subject to independent security review and to a commissioned penetration test,
with findings tracked and re-verified rather than left to age. Automated security
checking runs continuously in the delivery pipeline, so regressions are caught at the
point of change.

Vulnerability reports are welcome. FLIP publishes a security policy with a private
reporting route and a coordinated disclosure process — see |SECURITY.md|_ in the
repository. Please do not open a public issue for a suspected vulnerability.

.. |SECURITY.md| replace:: ``SECURITY.md``
.. _SECURITY.md: https://github.com/londonaicentre/FLIP/blob/main/SECURITY.md
