.. _governance-and-compliance:

#########################
Governance and compliance
#########################

This page describes who decides what in FLIP, and how the platform maps onto the
assurance frameworks NHS organisations are held to. It is written for information
governance and Caldicott functions, research ethics committees, and anyone assessing
FLIP on behalf of a participating organisation.

Its companion is :ref:`security`, which describes the technical controls at each layer.
Where a governance guarantee rests on a technical control, this page states the
guarantee and links there for the mechanism.

FLIP is designed so that **each participating organisation keeps control of its own
data**:

- Patient data never leaves the trust that holds it.
- No project uses a trust's data until that trust has agreed to take part in it.
- A trust can decline any individual project without leaving the federation and without
  affecting any other participant.
- The controls that decide what may leave a trust run on the trust's own infrastructure,
  alongside the data they protect.

*****************************************
Each site can veto any individual project
*****************************************

Approval in FLIP is **per project, per trust**. It is not a blanket agreement to
participate.

When a research project is staged, each trust whose data it proposes to use decides
whether to take part:

- **The decision is taken offline**, between the project and the trust's information
  governance function.
- **The outcome is recorded in FLIP** against that specific project–trust pairing. The
  record is timestamped, and the decider is recorded in the project's audit trail (see
  :ref:`admin-project-and-user-management`).
- **A Central Hub administrator records it by default.** A trust that wants to record its
  own decisions has a **Trust Admin**. While it has one, only its Trust Admins can approve
  or decline for it (see :ref:`trust-admin-guide`). A hub administrator can still remove
  the Trust Admin role, and removing a trust's last Trust Admin hands its decisions back
  to the hub. Role changes and decisions are both recorded in the audit trail.

**A trust that declines contributes no data.** The project proceeds with the trusts
that approved it. The declining trust will already have answered the project's cohort
query: that query runs at every registered trust when it is submitted, before staging,
and returns only the aggregate statistics described below. The declining trust's data,
however, is never pulled at row level, never imported, and never trained on. On Flower,
every trust connected to the training network still receives and runs the researcher's
code, including one that declined, but its trust services refuse that code any data.

Three consequences matter for information governance:

- **Participation is not all-or-nothing.** A trust can support one research question and
  decline another, on clinical, ethical, or capacity grounds, without renegotiating its
  involvement in the platform.
- **The decision sits with the data holder.** The agreement itself is made by the trust.
  The platform records the outcome rather than making the decision. What the platform
  enforces *on the trust's own deployment*, independently of that record, is narrower and
  more concrete:

  - every cohort query is validated and executed by the trust's own ``data-access-api``
    against a read-only database role;
  - row-level results are withheld below the trust's own minimum group size;
  - the trust reaches the Central Hub by outbound polling only, so nothing is pushed into
    a trust from outside.

  See :ref:`security`.
- **The decision is auditable.** Who approved what, and when, is recorded and can be
  produced for an audit or an ethics review.

**************************************
Data residency and what actually moves
**************************************

**Patient data stays in the trust.** Imaging retrieved for a project is held in the
trust's own XNAT instance. Cohort queries execute against the trust's own OMOP database.
Neither is copied to the Central Hub.

**What leaves a trust** is limited to:

- aggregate cohort statistics, suppressed whole when the cohort has fewer patients than
  a minimum group size. Above it, the age, sex and modality breakdowns group small
  categories, but per-column counts of present and missing values have no small-number
  floor;
- model updates produced during federated training. On FLARE these always pass through
  a privacy filter before transmission; on Flower, only if the researcher's code enables
  one;
- operational telemetry such as task status.

**Model updates deserve an honest treatment rather than a reassuring one.** They are
parameter values, not patient records. But a trained model can, in adverse cases,
memorise fragments of its training data, and inference attacks against model updates are
an active research area. FLIP manages this as a residual risk rather than defining it
away. The mitigations are:

- a privacy filter on updates before they leave the trust: always applied on FLARE,
  where a trust can also enforce its own, and opt-in on Flower, where the researcher's
  code must enable it (see :ref:`fl-update-privacy`);
- aggregation, so the model a researcher receives combines all participating trusts and
  no single trust's update is exposed to them;
- the structural control: a trust's data is released for training only for a project
  it has individually approved.

**A trust approving a project is approving the trained model's egress.** The trained
model is the project's deliverable and does leave the platform to the research team.
Trusts should treat model outputs as derived data whose disclosiveness depends on the
model and the cohort. They are free to make output-checking or publication conditions
part of their approval decision.

**The suppression threshold is the trust's own setting.** Each trust sets its disclosure
floor in the deployment configuration it holds locally: the same kit file that carries
its credentials. A trust whose small-numbers policy demands a higher minimum simply
raises its own floor. Trusts need not agree on a shared value, and the Central Hub cannot
lower it, because the suppression runs on the trust's own deployment, next to the data.

**What enters a trust** is the definition of work to be done, and nothing else: an
approved project, a cohort query, and the model application to train. The hub cannot
read into a trust. Trusts poll outbound and the hub has no route back in.

The controls that constrain a cohort query before it runs, and the suppression threshold
applied to its results, are described under :ref:`security`.

**********************************************
Network architecture as a governance guarantee
**********************************************

The network design is why the guarantees above are structural rather than procedural.
**Each trust polls the Central Hub outbound, and there is no route into a trust's
network**, either from the internet or from the hub. For a trust's own network team,
onboarding FLIP requires no inbound exposure to the outside world.

Where FLIP is connected to the trust's own PACS, one inbound rule is needed *inside* the
trust. FLIP asks the PACS for a study, and the PACS opens a connection back to XNAT to
deliver it, on the DICOM port alone.

A site-to-site VPN can be provisioned on request where a trust's policy calls for
network-layer separation as well.

The practical governance point: **a trust does not have to rely on the Central Hub's
access controls** to be confident its systems are unreachable. There is no path.

See :ref:`security` for the detail, and :ref:`deploy-trust-node-network` for the outbound
ports a trust host needs.

*********************
National data opt-out
*********************

The National Data Opt-Out applies to the use of confidential patient information for
research and planning. Each organisation is responsible for applying it to its own data.

**In FLIP this is applied where the data is prepared, in the OMOP database, rather than
in the platform's query layer.** The production OMOP database is periodically reconciled
against the national opt-out register, so patients who have opted out are removed from
the dataset FLIP is able to see.

Because FLIP queries only what that database contains, an opt-out applied there
propagates automatically: to every subsequent cohort query, every project, and every
model trained thereafter. There is no separate platform-side list to keep in step, and no
route by which a project can reach data the extract has excluded.

Two points an information governance function will want to record explicitly:

- **Reconciliation is periodic, not per-query.** The control question is therefore the
  reconciliation interval. It should be agreed with each trust and documented alongside
  the data flow.
- **The platform inherits the decision rather than enforcing it.** FLIP holds no
  independent view of opt-out status. Its guarantee is the narrower one that it can only
  ever see what the OMOP extract contains. This is a deliberate separation of concerns:
  opt-out is a property of the dataset, applied once, rather than a filter each consuming
  system has to reimplement correctly.

Where a trust wishes to go further, the per-project approval gate is a second control
point. A project can be declined outright if its purpose falls outside what the trust's
patients have been informed of.

**********************************************
Data protection roles and impact assessment
**********************************************

**FLIP is a platform; it does not settle data protection roles by itself.** The
organisations involved make these determinations **per deployment and per project**:

- the controller/processor determination for each data flow;
- the lawful basis for each use;
- the Data Protection Impact Assessment.

For a Caldicott or IG function these are the first questions, so this page names them
explicitly rather than leaving them implied.

The architecture gives those determinations a clean starting point. Patient data remains
in the custody and on the infrastructure of the trust that holds it. The disclosure
controls over that data run locally on the trust's own deployment. What crosses the
boundary is enumerated above.

For each deployment the items to record are:

- **Controllership**, per flow: the imaging held in the trust's own XNAT, the cohort
  statistics that leave, and the model updates and trained model.
- **Lawful basis** for the research use, determined by the participating organisations,
  not by the platform. Where confidential patient information is used without consent,
  this includes the relevant support (for example CAG s251).
- **A DPIA** covering the federated flow. It can reference the structural controls on
  this page and in :ref:`security` rather than restating them.
- **The national opt-out reconciliation interval** (previous section), agreed with each
  trust and documented alongside the data flow.

Where these determinations have not yet been made for a given deployment, that is an
open item to close before the deployment handles patient data. It is not a gap this page
papers over.

*****************
Access governance
*****************

**Role-based access control.** Users hold one of four defined roles: Admin, Researcher,
Viewer, or Trust Admin. A Trust Admin is held for one trust, and its holder also has
Researcher access. Each role has an explicit permission set, applied default-deny. Access to a
specific project's data additionally requires membership of that project, except that an
Admin can access every project and a Trust Admin can read a project staged at their
trust. See
:ref:`User Roles <rbac-roles>` for the full permission matrix.

**Multi-factor authentication is mandatory** for every user. Enrolment is verified at the
application layer on every authenticated request, not only at login. So a session issued
before enrolment, or after an administrative MFA reset, cannot reach any protected
function. (The second factor is presented at sign-in; the per-request control is the
enrolment check.)

**Access review.** Access is granted by a FLIP administrator. Periodic review of accounts
and removal of dormant access is an operating commitment of the organisation running the
hub, supported by the platform's role and membership records.

**Separation from the trust's wider estate.** A FLIP role does not confer access to the
trust's broader imaging systems. Retrieval from the trust PACS is restricted to the FLIP
service account and to accounts explicitly granted the DQR role. An ordinary XNAT account
cannot pull imaging on demand.

**Audit.** Administrative actions, project approvals, and role changes are recorded.

**********************
Retention and deletion
**********************

What happens to project artefacts at project end is a question every IG review asks. Here
is the position, stated plainly, including where deletion is a decision rather than an
automatism.

**On the hub, the cohort is held as a query, not an extract.** The hub stores the cohort
definition as SQL text and re-executes it against the trust's own OMOP database at each
stage that needs it. No row-level cohort data is retained on the hub. There is nothing
hub-side to delete at project end beyond the query text and audit records.

**Each approving trust keeps a record of the approved cohort.** At approval, the trust
records the approved query and the patient and imaging-study identifiers it returned (no
clinical values), so that training is limited to that cohort. The record stays on the
trust and is not deleted at project end today.

**Imaging pulled for a project stays in the trust's XNAT, deliberately.** Closing or
deleting a project on the hub does **not** destroy the trust-side imaging. This is a
considered position, not an omission:

- During data enrichment, segmentations, labels and annotations are added to that
  imaging.
- Unlike the source images, which can be re-pulled from PACS, that enrichment work cannot
  be regenerated.
- Destruction of an XNAT project is therefore an explicit action taken at the trust, by
  the trust, rather than a cascade the hub can trigger. A trust's own retention schedule
  governs when it happens.

**Working copies at FL clients.** During training each FL client holds a working copy of
its own net's converted imaging. On the FLARE backend the application cleans this up
after the run. Bounded retention for the Flower backend's cache is open, tracked work
(FLIP#1050).

**Hub-side artefacts persist until removed.** Uploaded model files, the bundled
applications sent to trusts, and training outputs (including the trained model) live in
versioned cloud storage. They are retained until an operator removes them. There is no
automated end-of-project purge today. A deployment that requires one should record the
manual deletion step in its project-closure procedure, naming who deletes what.

**Logs are retained per class, and the classes are not alike.** No UK health policy sets
a retention period for system logs. The CAF-aligned DSPT asks for coverage sufficient to
investigate an incident (C1.a, C1.d). UK GDPR's storage limitation principle asks that
whatever period is chosen be defined and justified rather than open-ended. FLIP's
positions:

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Log class
     - Retention
     - Why
   * - Hub infrastructure logs (the ECS, trust EC2 and WAF log groups)
     - **365 days** in production; **90 days** in staging
     - Production matches the retention baseline the organisation's Landing Zone
       Accelerator already applies to every log group it creates in the same accounts.
       Staging deliberately deviates. Its trust data is mock, but its authentication and
       IAM events are real and a staging compromise is a genuine incident, so the window
       stays long enough to investigate one.
   * - Trust-side application logs
     - **30 days**, in each trust's own Loki instance
     - Sized for operational debugging rather than incident forensics.
   * - Object-storage access logs
     - **90 days**
     -
   * - Edge access logs
     - **30 days**
     - CloudFront logs record request URIs, query strings and client IP addresses. Their
       window is therefore a disclosure decision before it is a cost or forensics one. It
       is kept among the shortest on purpose rather than raised to match the
       infrastructure baseline.

**Audit records are not logs and are not on this clock.** The project, model, trust and
user audit tables record who approved what and which data trained which model. That is
provenance. It supports reproducibility and research-integrity expectations. Where a
trained model is later taken forward as a medical device by its manufacturer, it also
supports traceability obligations measured in years, not months. It is retained
accordingly, and deliberately, rather than aged out with the telemetry.

**********************************
Assurance and independent scrutiny
**********************************

- FLIP is subject to a **commissioned penetration test** and to **recurring structured
  security review supported by automated analysis**. Findings are tracked in a live
  register carrying an owner and a status for each item, and re-verified rather than left
  to age.
- A **published security policy** provides a private reporting route and a coordinated
  disclosure process.
- **Automated security checking** runs continuously in the delivery pipeline, so
  regressions are caught at the point of change.
- FLIP is **open source under Apache 2.0**. The code, its change history, and its
  automated checks are publicly inspectable. This is transparency that *supports*
  supplier due diligence rather than a substitute for it. Named responsibilities,
  contractual terms, and exit arrangements remain matters for the agreement between the
  operating organisation and each participant.

**********************************************
Clinical safety and procurement standards
**********************************************

Two standards frameworks sit adjacent to this page and are worth addressing before a
trust asks.

**DTAC.** Trusts procuring digital health technologies assess them against the Digital
Technology Assessment Criteria. FLIP is a research platform rather than a patient-facing
clinical tool. Where a deployment falls within a trust's DTAC scope, the evidence for that
assessment is substantially this page and :ref:`security`. The operating organisation will
support a trust's DTAC assessment with the underlying artefacts.

**DCB0129 / DCB0160.** **FLIP itself is out of scope for clinical safety case
management.** Models trained on FLIP are research outputs, and the platform deploys
nothing into clinical care.

- Those obligations attach at the point a trained model is taken *toward* clinical use,
  for example when packaged as a MONAI Application Package for deployment.
- That step is outside FLIP. It triggers the deploying organisation's DCB0160 process,
  and the manufacturer-side DCB0129 and regulatory position for the model itself.

This page should not be read as clinical-safety evidence for any model trained on the
platform. The scope boundary is the point of this section.

.. _compliance-mappings:

***************************************
How the appendices map to NHS assurance
***************************************

The **Data Security and Protection Toolkit (DSPT)** is completed by NHS trusts, ICBs,
CSUs, arm's-length bodies, operators of essential services and genomics organisations.
Since September 2024 it has been **aligned to the NCSC Cyber Assessment Framework
(CAF)**:

- A health-and-care overlay extends the CAF with a fifth objective, on using and sharing
  information appropriately.
- Each organisation assesses itself **per contributing outcome**, as *Achieved*,
  *Partially Achieved* or *Not Achieved*, against a profile set by NHS England. The
  largest organisations are independently assessed.
- **It is not a pass/fail checklist.** The profile sets a minimum achievement level per
  outcome, *Partially Achieved* is the proportionate expectation for many of them, and
  profiles tighten year on year.
- Where NHS England judges a risk too great for local flexibility, it issues binding
  directive policy instead. Multi-factor authentication was the first such directive.

Three consequences for how to read the appendices:

- **Appendix B maps FLIP at contributing-outcome level** against the CAF-aligned DSPT,
  because that is the unit at which the trusts deploying FLIP are actually assessed. It
  states what FLIP contributes and where responsibility sits. It does **not** assert
  achievement levels, which are each organisation's own assessment against its profile.
  Smaller organisations retain a prescriptive controls interface, mapped nationally
  against the same CAF profile, so the same crosswalk serves them.
- **Cyber Essentials (Appendix A) is a separate, supplier-side certification.** Under the
  CAF-aligned DSPT there is no exemption for Cyber Essentials Plus or ISO 27001
  certification. (The last CE+ evidence-item equivalence was removed from the legacy
  toolkit in October 2023.) So Appendix A evidences supplier posture and substitutes for
  nothing in Appendix B.
- **Deployments inside a trusted research environment** can draw on the TRE's own
  assurance. Alongside the `SATRE specification
  <https://satre-specification.readthedocs.io/>`_, the community-maintained SATRE-centric
  Control Alignment Table (an unofficial, exploratory mapping) relates SATRE controls to
  the DSPT and Cyber Essentials. So where FLIP runs inside a SATRE-aligned TRE (as in the
  KCL CREATE deployment), much of the evidence base is shared rather than duplicated.

*****************************
Appendix A — Cyber Essentials
*****************************

Supplier-side scope: this table maps the five Cyber Essentials technical control themes.
As noted above, it evidences supplier posture only and grants no DSPT exemption.

.. list-table::
   :header-rows: 1
   :widths: 18 57 25

   * - Theme
     - How FLIP addresses it
     - Status
   * - **1. Firewalls and internet gateways**
     - - Trust environments expose no inbound ports and poll outbound only.
       - Services run in private subnets with no public address.
       - A single hardened CloudFront edge fronts the hub, with an internal-only load
         balancer behind it.
       - AWS WAF managed rules are deployed.
     - Operational (WAF rules moving from monitoring to enforcement)
   * - **2. Secure configuration**
     - - Infrastructure defined as code with automated validation.
       - Immutable, pinned image versions rather than mutable tags.
       - Insecure conveniences disabled in production, including API documentation
         endpoints.
       - Configuration drift is detectable rather than silent.
       - Default credentials removed from deployment templates, with services refusing to
         start on a weak or unset credential.
     - Operational
   * - **3. Security update management**
     - - Automated dependency vulnerability monitoring.
       - A 72-hour supply-chain cooldown on newly published packages.
       - Remediation of critical and high-severity vulnerabilities within the 14-day
         window the scheme requires.
       - Releases gated on passing automated tests.
     - Operational
   * - **4. User access control**
     - - Managed identity provider with full token verification.
       - Mandatory MFA with enrolment verified on every authenticated request.
       - Default-deny role-based access control across four defined roles.
       - Per-project membership checks.
       - Least-privilege per-service cloud roles.
       - Per-trust credential scoping.
       - PACS retrieval restricted to service accounts and explicitly granted roles.
       - Brokered, audited operator access with no exposed remote-login port.
     - Operational
   * - **5. Malware and code protection**
     - - Uploaded model files are checked before use, including a non-blocking
         static-analysis scan of Python source for common risky patterns.
       - The containers that execute training workloads are isolated and hardened.
       - Automated secret scanning on every change.
       - Supply-chain cooldown.
       - Cloud-native runtime threat detection.

       **Arbitrary Python logic in uploaded training code is not sandboxed at runtime.**
       The accepted control is uploader self-review plus RBAC, not an enforced platform
       gate (FLIP#877, GHSA-8465).
     - Operational

*********************************
Appendix B — The CAF-aligned DSPT
*********************************

.. note::

   **Framework version: read before citing.** This appendix is keyed to the CAF-aligned
   DSPT guidance as republished by NHS England on 26 August 2026, which follows **NCSC
   CAF v4.0**. That is 41 cyber contributing outcomes across Objectives A–D, plus 8
   information-governance outcomes in the health-and-care Objective E: 49 in total.

   Earlier assessment years (2024-25 and 2025-26) were built on the CAF v3.2 baseline of
   39 + 8 = 47 outcomes. Several IDs differ between the two versions (A2, A4, C1, C2 and
   D2 were restructured in v4.0). NHS England sets the DSPT profile independently of
   NCSC's CAF releases. Before citing this mapping in a submission or audit, confirm the
   CAF version and outcome set your assessment year is pinned to.

Each row states **where the control sits** and **what FLIP contributes**. It does not
assert an achievement level, which is each organisation's own assessment against the NHS
England profile for its year.

The **Scope** column uses these values:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Scope
     - Meaning
   * - **Hub**
     - Implemented on the Central Hub by the operating organisation.
   * - **Node**
     - Implemented on the trust's own FLIP deployment.
   * - **Shared**
     - Divided between platform and deploying organisation.
   * - **Organisational**
     - Belongs to the deploying organisation, with the platform supporting at most.

In the **Residual gap** column, *in delivery* marks scheduled work rather than a gap left
unaddressed. Where FLIP has nothing today, the row says **gap** rather than dressing
adjacent controls up as the missing one.

Objective A — Managing risk
===========================

.. list-table::
   :header-rows: 1
   :widths: 22 12 46 20

   * - Contributing outcome
     - Scope
     - How FLIP contributes
     - Residual gap
   * - **A1.a** Board direction
     - Organisational
     - Direction-setting for information assurance belongs to each organisation. The
       platform contributes a published security policy and named ownership for its own
       scope.
     - —
   * - **A1.b** Roles and responsibilities
     - Shared
     - Four defined roles with an explicit permission matrix; named security
       contacts and a published disclosure route. The organisation's own SIRO, Caldicott
       and IG structures sit above this.
     - —
   * - **A1.c** Decision making
     - Shared
     - The per-project, per-trust approval gate places the decision with the organisation
       that carries the risk. The Central Hub records the outcome per project–trust
       pairing (approved or declined), timestamped, with the hub user who recorded it on
       the record and in the project's audit trail.
     - The gate is a hub-side record of the trust's decision, not a control on the
       trust's own deployment. What the trust enforces locally is the query validation
       and row-level suppression under E3.b.
   * - **A2.a** Risk management process
     - Shared
     - Platform findings are held in a live register carrying an owner and a status per
       item, re-verified rather than left to age. The deployment's place in the
       organisation's own risk process is the organisation's.
     - —
   * - **A2.b** Understanding threat
     - Organisational
     - The platform maintains no threat-intelligence function. The supply-chain cooldown
       and managed edge rule sets encode published ecosystem attack patterns, no more.
     - No platform threat-intelligence capability.
   * - **A2.c** Assurance
     - Shared
     - Commissioned penetration test; recurring structured security review supported by
       automated analysis; an open codebase that any participant can inspect.
     - —
   * - **A3.a** Asset management
     - Hub
     - Infrastructure defined as code is the authoritative inventory; immutable pinned
       image versions; dependency lockfiles act as a software bill of materials.
       Trust-side hosts are the organisation's estate.
     - —
   * - **A4.a** Supply chain
     - Shared
     - Automated dependency vulnerability monitoring; a 72-hour cooldown on newly
       published packages; lockfile-pinned builds; curated base application templates.
       For a deploying trust, FLIP is itself a supplier. The open code and published
       policy support that due diligence, while contractual terms and exit arrangements
       remain matters for the agreement.
     - —
   * - **A4.b** Secure software development and support
     - Hub
     - A secure development lifecycle for the platform itself: peer-reviewed changes with
       automated acceptance checks, contributor sign-off, a protected mainline, secret
       scanning on every change, and security rules enforced by tooling rather than
       convention.
     - —

Objective B — Protecting against cyber attacks and data breaches
================================================================

.. list-table::
   :header-rows: 1
   :widths: 22 12 46 20

   * - Contributing outcome
     - Scope
     - How FLIP contributes
     - Residual gap
   * - **B1.a** Policy, process and procedure development
     - Shared
     - Documented security rules and engineering conventions; a published security policy
       and coordinated disclosure process. The organisation's IG procedures are its own.
     - —
   * - **B1.b** Policy, process and procedure implementation
     - Hub
     - Platform policy is enforced by tooling rather than by convention: pre-commit guards,
       CI gates, and services that refuse to start on a weak or unset credential.
     - —
   * - **B2.a** Identity verification, authentication and authorisation
     - Hub + Node
     - Managed identity provider with full token verification on every request; mandatory
       MFA with enrolment verified per request; per-trust API keys; per-trust internal
       service keys inside each trust. FL clients deliberately hold no hub credentials.
     - —
   * - **B2.b** Device management
     - Organisational
     - End-user and clinical devices are the deploying organisation's estate. The platform
       mandates nothing device-side.
     - —
   * - **B2.c** Privileged user management
     - Shared
     - An explicit Admin role; brokered, audited operator access with no exposed
       remote-login port; least-privilege per-service cloud roles. The organisation
       governs who holds privileged accounts.
     - —
   * - **B2.d** Identity and access management (IdAM)
     - Shared
     - Default-deny RBAC across four defined roles; per-project membership re-checked on
       every access, except for Admins and for a Trust Admin reading a project staged at
       their trust. Periodic account review and dormant-account removal is an operating
       commitment of the organisation running the hub.
     - —
   * - **B3.a** Understanding data
     - Shared
     - The platform's data flows are enumerated on this page: what leaves a trust, what
       enters it, and what never moves. Understanding of the trust's own OMOP and PACS
       estates is the trust's.
     - —
   * - **B3.b** Data in transit
     - Hub + Node
     - Encrypted transport throughout, with an additional authenticated (AES-256-GCM)
       payload-encryption layer; mutually authenticated TLS between federated learning
       participants.
     - —
   * - **B3.c** Stored data
     - Hub + Node
     - Patient data at rest stays on the trust's own infrastructure; hub-side storage is
       encrypted under managed keys; no standing production database credential.
     - —
   * - **B3.d** Mobile data
     - Organisational
     - Not applicable to the platform: FLIP has no mobile estate.
     - —
   * - **B3.e** Media and equipment sanitation
     - Organisational
     - Trust hardware lifecycle is the organisation's. Hub-side storage sanitisation is
       inherited from the cloud provider's controls.
     - —
   * - **B4.a** Secure by design
     - Hub + Node
     - Outbound-only trust boundary; private-by-default networking; a quarantine boundary
       between uploaded and scanned model files; a per-project, per-trust approval
       recorded on the hub; suppression gates that run on the trust's own deployment, next
       to the data.
     - Arbitrary Python in uploaded training code is not sandboxed at runtime. The
       accepted control is uploader self-review plus RBAC (see Appendix A, theme 5).
   * - **B4.b** Secure configuration
     - Hub
     - Infrastructure defined as code with automated validation; configuration drift
       detectable rather than silent; immutable pinned images; insecure conveniences
       disabled in production; default credentials removed, with services refusing to
       start without required secrets.
     - —
   * - **B4.c** Secure management
     - Hub
     - Administration over brokered, audited channels only; no exposed remote-login port
       anywhere in the estate; least-privilege service roles.
     - —
   * - **B4.d** Vulnerability management
     - Hub
     - Automated dependency monitoring; remediation of critical and high-severity findings
       within the Cyber Essentials 14-day window; penetration-test findings tracked and
       re-verified; structural scanning of uploaded model files before they can be used.
     - —
   * - **B5.a** Resilience preparation
     - Shared
     - Versioned storage, immutable images, and infrastructure redeployable from code.
       Trust-side operational continuity remains the organisation's.
     - —
   * - **B5.b** Design for resilience
     - Hub
     - Private subnets with no public addresses; a single hardened hub edge; account
       separation and centrally managed guardrails under the AWS Landing Zone Accelerator.
     - LZA consolidation is in delivery, not complete.
   * - **B5.c** Backups
     - Shared
     - Hub-side artefacts live in versioned cloud storage. Backups of trust-side systems
       (OMOP, XNAT) follow the trust's own arrangements.
     - —
   * - **B6.a** Culture
     - Organisational
     - The organisation's own. The platform supports it with documented conventions and
       clear role definitions.
     - —
   * - **B6.b** Training
     - Organisational
     - The organisation's own; not a platform control.
     - —

Objective C — Detecting cyber security events
=============================================

.. list-table::
   :header-rows: 1
   :widths: 22 12 46 20

   * - Contributing outcome
     - Scope
     - How FLIP contributes
     - Residual gap
   * - **C1.a** Sources and tools for logging and monitoring
     - Hub
     - Cloud-native runtime threat detection; audit trails for administrative actions,
       approvals and role changes; scheduled and on-change secret scanning.
     - Centralised logging under the LZA: in delivery.
   * - **C1.b** Securing logs
     - Hub
     - Access logs held in dedicated storage with lifecycle management; audit records
       separated from application data.
     - LZA log centralisation: in delivery.
   * - **C1.c** Generating alerts
     - Hub
     - Configuration drift detection with alerting; runtime threat-detection findings;
       secret-scanning and code-scanning alerts raised at the point of change.
     - —
   * - **C1.d** Triage of security incidents
     - Shared
     - A published disclosure process with a defined acknowledgement window; triaged
       code-scanning alerts. Incident triage for the deployment sits with the operating
       organisation.
     - —
   * - **C1.e** Personnel skills for monitoring and detection
     - Organisational
     - The operating organisation's; not a platform control.
     - —
   * - **C1.f** Understanding user and system behaviour, and threat intelligence
     - Organisational
     - Managed edge rule sets provide a baseline. The platform has no behavioural
       analytics or threat-intelligence capability of its own.
     - No platform capability: **gap**.
   * - **C2.a** Threat hunting
     - Organisational
     - None today, stated plainly. Penetration testing and pipeline scanning are
       *vulnerability* discovery, recorded under A2.c and B4.d. They are not threat
       hunting and are not claimed here.
     - Platform threat-hunting capability: **gap**.

Objective D — Response and recovery planning
============================================

.. list-table::
   :header-rows: 1
   :widths: 22 12 46 20

   * - Contributing outcome
     - Scope
     - How FLIP contributes
     - Residual gap
   * - **D1.a** Response plan
     - Shared
     - A coordinated disclosure and private advisory workflow with a defined
       acknowledgement window on the platform side. The organisation's incident plans
       cover the deployment; under the health overlay these include personal data
       breaches.
     - —
   * - **D1.b** Response and recovery capability
     - Shared
     - Recovery is supported by versioned storage, immutable image versions, and
       infrastructure redeployable from code. Trust-side operational response remains the
       organisation's.
     - —
   * - **D1.c** Testing and exercising
     - Organisational
     - Recovery-from-code is exercised implicitly by routine redeployment. There is no
       incident-exercising programme for the platform today.
     - No platform-side exercising programme: **gap**.
   * - **D2.a** Post incident analysis
     - Shared
     - Audit trails support root-cause analysis; findings enter the tracked register with
       an owner and a status.
     - —
   * - **D2.b** Using incidents and near misses to drive improvements
     - Shared
     - Register items are re-verified after fix rather than closed on assertion, and fixes
       land with regression checks at the point of change.
     - —

Objective E — Using and sharing information appropriately
=========================================================

This is the health-and-care objective, additional to the NCSC CAF. It is the natural home
of FLIP's strongest governance material.

.. list-table::
   :header-rows: 1
   :widths: 22 12 46 20

   * - Contributing outcome
     - Scope
     - How FLIP contributes
     - Residual gap
   * - **E1.a** Privacy information
     - Organisational
     - Transparency to patients is each trust's duty. The platform helps it be accurate:
       what leaves a trust is enumerated on this page, so a privacy notice can describe
       the flow concretely.
     - —
   * - **E2.a** Managing data subject rights under UK GDPR
     - Organisational
     - Rights requests are handled where the data already is. Patient data never leaves
       the trust's custody, and the platform holds no separate copy to complicate
       rectification or erasure.
     - —
   * - **E2.b** Consent
     - Organisational
     - The lawful basis and consent position are determined per project by the
       organisations involved (see *Data protection roles* above). The per-project
       approval gate, recorded on the Central Hub, is where a trust's position takes
       effect. A trust that has not approved a project is never pulled from at row level,
       imported from, or trained on for it. On Flower its FL client may still run the
       project's code, but is refused the data.
     - —
   * - **E2.c** National data opt-out policy
     - Node
     - Applied where the data is prepared. The OMOP database is periodically reconciled
       against the opt-out register, so the decision propagates to every query the
       platform can make (see *National data opt-out* above). The reconciliation interval
       should be agreed and documented per trust.
     - Reconciliation is periodic, not per-query. The interval is the control.
   * - **E3.a** Using and sharing information for direct care
     - Organisational
     - Not applicable. FLIP performs no direct-care processing; every platform use is
       research, under E3.b.
     - —
   * - **E3.b** Using and sharing information for other purposes
     - Node
     - FLIP's home outcome:

       - per-project, per-trust approval with timestamped audited decisions;
       - a trust-set minimum group size below which cohort results are suppressed;
       - validated read-only cohort queries;
       - row-level egress gated on the trust's own deployment.
     - —
   * - **E4.a** Managing records
     - Shared
     - The retention position is stated plainly in *Retention and deletion* above:

       - on the hub, cohorts are held as queries, not extracts; each approving trust
         keeps the approved query and cohort identifiers;
       - trust-side imaging outlives the project by design;
       - hub-side artefacts persist until removed.

       The organisation's records schedule governs trust-side holdings.
     - No automated end-of-project purge of hub-side artefacts.
   * - **E4.b** Clinical coding
     - Organisational
     - Coding quality in OMOP is the trust's own data-preparation function. The platform
       queries what the extract contains.
     - —
