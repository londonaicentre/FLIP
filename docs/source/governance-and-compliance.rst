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
data**. Patient data never leaves the trust that holds it. No project can use a trust's
data until that trust has explicitly approved that project. A trust can decline any
individual project without leaving the federation and without affecting any other
participant. Nothing in the platform can override that decision, because the data and
the approval both live on the trust's own infrastructure.

*****************************************
Each site can veto any individual project
*****************************************

Approval in FLIP is **per project, per trust** — not a blanket agreement to
participate.

When a research project is created, it must be approved separately by an administrator
at each trust whose data it proposes to use. Approval is recorded against that specific
project–trust pairing, timestamped and attributed. A trust that declines is simply not
included: the project proceeds with the trusts that approved it, and the declining
trust's data is never queried, never imported, and never contributes to the model.

Three consequences matter for information governance:

- **Participation is not all-or-nothing.** A trust can support one research question and
  decline another — on clinical, ethical, or capacity grounds — without renegotiating
  its involvement in the platform.
- **The decision sits with the data holder.** The approval gate runs on the trust's own
  deployment, so it cannot be bypassed by the Central Hub or by another participant.
- **The decision is auditable.** Who approved what, and when, is recorded and can be
  produced for an audit or an ethics review.

**************************************
Data residency and what actually moves
**************************************

**Patient data stays in the trust.** Imaging retrieved for a project is held in the
trust's own XNAT instance. Cohort queries execute against the trust's own OMOP database.
Neither is copied to the Central Hub.

**What leaves a trust** is limited to: aggregate cohort statistics, subject to a minimum
group size below which results are suppressed; model updates produced during federated
training, which pass through a privacy filter before transmission; and operational
telemetry such as task status.

**Model updates deserve an honest treatment rather than a reassuring one.** They are
parameter values, not patient records — but a trained model can, in adverse cases,
memorise fragments of its training data, and inference attacks against model updates
are an active research area. FLIP manages this as a residual risk rather than defining
it away. The mitigations are: a privacy filter applied to updates before they leave the
trust; aggregation, so the model a researcher receives combines all participating
trusts and no single trust's update is exposed to them; and — the structural control —
no update is ever produced for a project the trust has not individually approved. The
trained model is the project's deliverable and does leave the platform to the research
team, so a trust approving a project is approving that egress: trusts should treat
model outputs as derived data whose disclosiveness depends on the model and the
cohort, and are free to make output-checking or publication conditions part of their
approval decision.

**The suppression threshold is the trust's own setting.** Each trust sets its
disclosure floor in the deployment configuration it holds locally — the same kit file
that carries its credentials — so a trust whose small-numbers policy demands a higher
minimum simply raises its own floor. Trusts need not agree on a shared value, and the
Central Hub cannot lower it: the suppression runs on the trust's own deployment, next
to the data.

**What enters a trust** is the definition of work to be done — an approved project, a
cohort query, and the model application to train — and nothing else. The hub cannot read
into a trust: trusts poll outbound and the hub has no route back in.

The controls that constrain a cohort query before it runs, and the suppression threshold
applied to its results, are described under :ref:`security`.

**********************************************
Network architecture as a governance guarantee
**********************************************

The network design is why the guarantees above are structural rather than procedural.
Each trust polls the Central Hub outbound, and there is no route from the internet — or
from the hub — into a trust's network. For a trust's own network team, onboarding FLIP
requires no inbound exposure to the outside world. Where FLIP is connected to the trust's
own PACS, one inbound rule is needed *inside* the trust: FLIP asks the PACS for a study,
and the PACS opens a connection back to XNAT to deliver it, on the DICOM port alone.
A site-to-site VPN can be provisioned on request where a trust's policy calls for
network-layer separation as well.

The practical governance point: a trust does not have to rely on the Central Hub's access
controls to be confident its systems are unreachable. There is no path.

See :ref:`security` for the detail, and :ref:`deploy-flip-node-on-prem` for the outbound
ports a trust host needs.

*********************
National data opt-out
*********************

The National Data Opt-Out applies to the use of confidential patient information for
research and planning, and each organisation is responsible for applying it to its own
data.

**In FLIP this is applied where the data is prepared — in the OMOP database — rather than
in the platform's query layer.** The production OMOP database is periodically reconciled
against the national opt-out register, so patients who have opted out are removed from
the dataset FLIP is able to see.

Because FLIP queries only what that database contains, an opt-out applied there
propagates automatically: to every subsequent cohort query, every project, and every
model trained thereafter. There is no separate platform-side list to keep in step, and no
route by which a project can reach data the extract has excluded.

Two points an information governance function will want to record explicitly:

- **Reconciliation is periodic, not per-query.** The control question is therefore the
  reconciliation interval, which should be agreed with each trust and documented
  alongside the data flow.
- **The platform inherits the decision rather than enforcing it.** FLIP holds no
  independent view of opt-out status; its guarantee is the narrower one that it can only
  ever see what the OMOP extract contains. This is a deliberate separation of concerns —
  opt-out is a property of the dataset, applied once, rather than a filter each consuming
  system has to reimplement correctly.

Where a trust wishes to go further, the per-project approval gate is a second control
point: a project can be declined outright if its purpose falls outside what the trust's
patients have been informed of.

**********************************************
Data protection roles and impact assessment
**********************************************

FLIP is a platform; it does not settle data protection roles by itself. The
controller/processor determination for each data flow, the lawful basis for each use,
and the Data Protection Impact Assessment are made **per deployment and per project**
by the organisations involved — and for a Caldicott or IG function these are the first
questions, so this page names them explicitly rather than leaving them implied.

The architecture gives those determinations a clean starting point: patient data
remains in the custody and on the infrastructure of the trust that holds it, each
trust's approval gate runs locally, and what crosses the boundary is enumerated above.
For each deployment the items to record are:

- **Controllership**, per flow: the imaging held in the trust's own XNAT, the cohort
  statistics that leave, and the model updates and trained model.
- **Lawful basis** for the research use — including, where confidential patient
  information is used without consent, the relevant support (for example CAG s251) —
  determined by the participating organisations, not by the platform.
- **A DPIA** covering the federated flow, which can reference the structural controls
  on this page and in :ref:`security` rather than restating them.
- **The national opt-out reconciliation interval** (previous section), agreed with each
  trust and documented alongside the data flow.

Where these determinations have not yet been made for a given deployment, that is an
open item to close before the deployment handles patient data — not a gap this page
papers over.

*****************
Access governance
*****************

**Role-based access control.** Users hold one of three defined roles — Admin,
Researcher, or Viewer — each with an explicit permission set, applied default-deny.
Access to a specific project's data additionally requires membership of that project.
See :ref:`User Roles <rbac-roles>` for the full permission matrix.

**Multi-factor authentication is mandatory** for every user. Enrolment is verified at
the application layer on every authenticated request — not only at login — so a session
issued before enrolment, or after an administrative MFA reset, cannot reach any
protected function. (The second factor is presented at sign-in; the per-request control
is the enrolment check.)

**Access review.** Access is granted by a FLIP administrator. Periodic review of
accounts and removal of dormant access is an operating commitment of the organisation
running the hub, supported by the platform's role and membership records.

**Separation from the trust's wider estate.** A FLIP role does not confer access to the
trust's broader imaging systems. Retrieval from the trust PACS is restricted to the FLIP
service account and to accounts explicitly granted the DQR role; an ordinary XNAT account
cannot pull imaging on demand.

**Audit.** Administrative actions, project approvals, and role changes are recorded.

**********************
Retention and deletion
**********************

What happens to project artefacts at project end is a question every IG review asks,
so here is the position, stated plainly — including where deletion is a decision
rather than an automatism.

**The cohort is held as a query, not an extract.** The hub stores the cohort
definition as SQL text and re-executes it against the trust's own OMOP database at
each stage that needs it. No row-level cohort data is retained on the hub — there is
nothing hub-side to delete at project end beyond the query text and audit records.

**Imaging pulled for a project stays in the trust's XNAT, deliberately.** Closing or
deleting a project on the hub does **not** destroy the trust-side imaging (FLIP#964).
This is a considered position, not an omission: during data enrichment, segmentations,
labels and annotations are added to that imaging, and unlike the source images — which
can be re-pulled from PACS — that enrichment work cannot be regenerated. Destruction of
an XNAT project is therefore an explicit action taken at the trust, by the trust,
rather than a cascade the hub can trigger. A trust's own retention schedule governs
when it happens.

**Working copies at FL clients.** During training each FL client holds a working copy
of its own net's converted imaging. On the NVFLARE backend the application cleans this
up after the run; bounded retention for the Flower backend's cache is open, tracked
work (FLIP#1050).

**Hub-side artefacts persist until removed.** Uploaded model files, the bundled
applications sent to trusts, and training outputs — including the trained model — live
in versioned cloud storage and are retained until an operator removes them. There is
no automated end-of-project purge today. A deployment that requires one should record
the manual deletion step in its project-closure procedure, naming who deletes what.

**********************************
Assurance and independent scrutiny
**********************************

- FLIP is subject to a **commissioned penetration test** and to **recurring structured
  security review supported by automated analysis**, with findings tracked in a live
  register carrying an owner and a status for each item, and re-verified rather than
  left to age.
- A **published security policy** provides a private reporting route and a coordinated
  disclosure process.
- **Automated security checking** runs continuously in the delivery pipeline, so
  regressions are caught at the point of change.
- FLIP is **open source under Apache 2.0** — the code, its change history, and its
  automated checks are publicly inspectable. This is transparency that *supports*
  supplier due diligence rather than a substitute for it: named responsibilities,
  contractual terms, and exit arrangements remain matters for the agreement between
  the operating organisation and each participant.

**********************************************
Clinical safety and procurement standards
**********************************************

Two standards frameworks sit adjacent to this page and are worth addressing before a
trust asks.

**DTAC.** Trusts procuring digital health technologies assess them against the Digital
Technology Assessment Criteria. FLIP is a research platform rather than a patient-facing
clinical tool, but where a deployment falls within a trust's DTAC scope, the evidence
for that assessment is substantially this page and :ref:`security`; the operating
organisation will support a trust's DTAC assessment with the underlying artefacts.

**DCB0129 / DCB0160.** FLIP itself is out of scope for clinical safety case management:
models trained on FLIP are research outputs, and the platform deploys nothing into
clinical care. Those obligations attach at the point a trained model is taken *toward*
clinical use — for example when packaged as a MONAI Application Package for deployment.
That step is outside FLIP, and it triggers the deploying organisation's DCB0160 process
(and the manufacturer-side DCB0129 and regulatory position for the model itself).
This page should not be read as clinical-safety evidence for any model trained on the
platform — the scope boundary is the point of the paragraph.

.. _compliance-mappings:

***************************************
How the appendices map to NHS assurance
***************************************

Since September 2024 the **Data Security and Protection Toolkit (DSPT)** completed by
NHS trusts, ICBs, CSUs, arm's-length bodies, operators of essential services and
genomics organisations is **aligned to the NCSC Cyber Assessment Framework (CAF)**: a
health-and-care overlay extends the CAF with a fifth objective on using and sharing
information appropriately, and each organisation assesses itself **per contributing
outcome** — *Achieved*, *Partially Achieved* or *Not Achieved* — against a profile set
by NHS England, with independent assessment for the largest organisations. It is not a
pass/fail checklist: the profile sets a minimum achievement level per outcome,
*Partially Achieved* is the proportionate expectation for many of them, and profiles
tighten year on year. Where NHS England judges a risk too great for local flexibility
it issues binding directive policy instead — multi-factor authentication was the
first such directive.

Three consequences for how to read the appendices:

- **Appendix B maps FLIP at contributing-outcome level** against the CAF-aligned DSPT,
  because that is the unit at which the trusts deploying FLIP are actually assessed.
  It states what FLIP contributes and where responsibility sits — it does **not**
  assert achievement levels, which are each organisation's own assessment against its
  profile. Smaller organisations retain a prescriptive controls interface, mapped
  nationally against the same CAF profile, so the same crosswalk serves them.
- **Cyber Essentials (Appendix A) is a separate, supplier-side certification.** Under
  the CAF-aligned DSPT there is no exemption for Cyber Essentials Plus or ISO 27001
  certification (the last CE+ evidence-item equivalence was removed from the legacy
  toolkit in October 2023), so Appendix A evidences supplier posture and substitutes
  for nothing in Appendix B.
- **Deployments inside a trusted research environment** can draw on the TRE's own
  assurance: alongside the `SATRE specification
  <https://satre-specification.readthedocs.io/>`_, the community-maintained
  SATRE-centric Control Alignment Table (an unofficial, exploratory mapping) relates
  SATRE controls to the DSPT and Cyber Essentials, so where FLIP runs inside a
  SATRE-aligned TRE (as in the KCL CREATE deployment) much of the evidence base is
  shared rather than duplicated.

*****************************
Appendix A — Cyber Essentials
*****************************

Supplier-side scope: this table maps the five Cyber Essentials technical control
themes. As noted above, it evidences supplier posture only and grants no DSPT
exemption.

.. list-table::
   :header-rows: 1
   :widths: 18 57 25

   * - Theme
     - How FLIP addresses it
     - Status
   * - **1. Firewalls and internet gateways**
     - Trust environments expose no inbound ports and poll outbound only; services run in
       private subnets with no public address; a single hardened CloudFront edge fronts
       the hub, with an internal-only load balancer behind it; AWS WAF managed rules are
       deployed.
     - Operational (WAF rules moving from monitoring to enforcement)
   * - **2. Secure configuration**
     - Infrastructure defined as code with automated validation; immutable, pinned image
       versions rather than mutable tags; insecure conveniences disabled in production,
       including API documentation endpoints; configuration drift is detectable rather
       than silent; default credentials removed from deployment templates, with services
       refusing to start on a weak or unset credential.
     - Operational
   * - **3. Security update management**
     - Automated dependency vulnerability monitoring; a 72-hour supply-chain cooldown on
       newly published packages; remediation of critical and high-severity
       vulnerabilities within the 14-day window the scheme requires; releases gated on
       passing automated tests.
     - Operational
   * - **4. User access control**
     - Managed identity provider with full token verification; mandatory MFA with
       enrolment verified on every
       authenticated request; default-deny role-based access control across three defined
       roles; per-project membership checks; least-privilege per-service cloud roles;
       per-trust credential scoping; PACS retrieval restricted to service accounts and
       explicitly granted roles; brokered, audited operator access with no exposed
       remote-login port.
     - Operational
   * - **5. Malware and code protection**
     - Uploaded model files are checked before use, including a non-blocking static-analysis
       scan of Python source for common risky patterns; the containers that execute training
       workloads are isolated and hardened; automated secret scanning on every change;
       supply-chain cooldown; cloud-native runtime threat detection. Arbitrary Python logic in
       uploaded training code is not sandboxed at runtime — the accepted control is uploader
       self-review plus RBAC, not an enforced platform gate (FLIP#877, GHSA-8465).
     - Operational

*********************************
Appendix B — The CAF-aligned DSPT
*********************************

.. note::

   **Framework version — read before citing.** This appendix is keyed to the
   CAF-aligned DSPT guidance as republished by NHS England on 26 August 2026, which
   follows **NCSC CAF v4.0**: 41 cyber contributing outcomes across Objectives A–D,
   plus 8 information-governance outcomes in the health-and-care Objective E — 49 in
   total. Earlier assessment years (2024-25 and 2025-26) were built on the CAF v3.2
   baseline of 39 + 8 = 47 outcomes, and several IDs differ between the two versions
   (A2, A4, C1, C2 and D2 were restructured in v4.0). NHS England sets the DSPT
   profile independently of NCSC's CAF releases, so before citing this mapping in a
   submission or audit, confirm the CAF version and outcome set your assessment year
   is pinned to.

Each row states **where the control sits** and **what FLIP contributes** — it does not
assert an achievement level, which is each organisation's own assessment against the
NHS England profile for its year. Scope values: **Hub** — implemented on the Central
Hub by the operating organisation; **Node** — implemented on the trust's own FLIP
deployment; **Shared** — divided between platform and deploying organisation;
**Organisational** — belongs to the deploying organisation, with the platform
supporting at most. *In delivery* marks scheduled work rather than a gap left
unaddressed; where FLIP has nothing today, the row says **gap** rather than dressing
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
     - Direction-setting for information assurance belongs to each organisation; the
       platform contributes a published security policy and named ownership for its
       own scope.
     - —
   * - **A1.b** Roles and responsibilities
     - Shared
     - Three defined platform roles with an explicit permission matrix; named security
       contacts and a published disclosure route. The organisation's own SIRO,
       Caldicott and IG structures sit above this.
     - —
   * - **A1.c** Decision making
     - Node
     - The per-project, per-trust approval gate places the decision with the
       organisation that carries the risk, timestamped and attributed, on the trust's
       own deployment.
     - —
   * - **A2.a** Risk management process
     - Shared
     - Platform findings are held in a live register carrying an owner and a status
       per item, re-verified rather than left to age. The deployment's place in the
       organisation's own risk process is the organisation's.
     - —
   * - **A2.b** Understanding threat
     - Organisational
     - The platform maintains no threat-intelligence function; the supply-chain
       cooldown and managed edge rule sets encode published ecosystem attack
       patterns, no more.
     - No platform threat-intelligence capability.
   * - **A2.c** Assurance
     - Shared
     - Commissioned penetration test; recurring structured security review supported
       by automated analysis; an open codebase that any participant can inspect.
     - —
   * - **A3.a** Asset management
     - Hub
     - Infrastructure defined as code is the authoritative inventory; immutable
       pinned image versions; dependency lockfiles act as a software bill of
       materials. Trust-side hosts are the organisation's estate.
     - —
   * - **A4.a** Supply chain
     - Shared
     - Automated dependency vulnerability monitoring; a 72-hour cooldown on newly
       published packages; lockfile-pinned builds; curated base application
       templates. For a deploying trust, FLIP is itself a supplier — the open code
       and published policy support that due diligence, while contractual terms and
       exit arrangements remain matters for the agreement.
     - —
   * - **A4.b** Secure software development and support
     - Hub
     - A secure development lifecycle for the platform itself: peer-reviewed changes
       with automated acceptance checks, contributor sign-off, a protected mainline,
       secret scanning on every change, and security rules enforced by tooling rather
       than convention.
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
     - Documented security rules and engineering conventions; a published security
       policy and coordinated disclosure process. The organisation's IG procedures
       are its own.
     - —
   * - **B1.b** Policy, process and procedure implementation
     - Hub
     - Platform policy is enforced by tooling — pre-commit guards, CI gates, and
       services that refuse to start on a weak or unset credential — rather than by
       convention.
     - —
   * - **B2.a** Identity verification, authentication and authorisation
     - Hub + Node
     - Managed identity provider with full token verification on every request;
       mandatory MFA with enrolment verified per request; per-trust API keys;
       per-trust internal service keys inside each trust; FL clients deliberately
       hold no hub credentials.
     - —
   * - **B2.b** Device management
     - Organisational
     - End-user and clinical devices are the deploying organisation's estate; the
       platform mandates nothing device-side.
     - —
   * - **B2.c** Privileged user management
     - Shared
     - An explicit Admin role; brokered, audited operator access with no exposed
       remote-login port; least-privilege per-service cloud roles. The organisation
       governs who holds privileged accounts.
     - —
   * - **B2.d** Identity and access management (IdAM)
     - Shared
     - Default-deny RBAC across three defined roles; per-project membership
       re-checked on every access. Periodic account review and dormant-account
       removal is an operating commitment of the organisation running the hub.
     - —
   * - **B3.a** Understanding data
     - Shared
     - The platform's data flows are enumerated on this page — what leaves a trust,
       what enters it, and what never moves. Understanding of the trust's own OMOP
       and PACS estates is the trust's.
     - —
   * - **B3.b** Data in transit
     - Hub + Node
     - Encrypted transport throughout, with an additional payload-encryption layer;
       mutually authenticated TLS between federated learning participants.
     - —
   * - **B3.c** Stored data
     - Hub + Node
     - Patient data at rest stays on the trust's own infrastructure; hub-side storage
       is encrypted under managed keys; no standing production database credential.
     - —
   * - **B3.d** Mobile data
     - Organisational
     - Not applicable to the platform — FLIP has no mobile estate.
     - —
   * - **B3.e** Media and equipment sanitation
     - Organisational
     - Trust hardware lifecycle is the organisation's; hub-side storage sanitisation
       is inherited from the cloud provider's controls.
     - —
   * - **B4.a** Secure by design
     - Hub + Node
     - Outbound-only trust boundary; private-by-default networking; a quarantine
       boundary between uploaded and scanned model files; approval and suppression
       gates that run on the trust's own deployment, next to the data.
     - Arbitrary Python in uploaded training code is not sandboxed at runtime — the
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
     - Administration over brokered, audited channels only; no exposed remote-login
       port anywhere in the estate; least-privilege service roles.
     - —
   * - **B4.d** Vulnerability management
     - Hub
     - Automated dependency monitoring; remediation of critical and high-severity
       findings within the Cyber Essentials 14-day window; penetration-test findings
       tracked and re-verified; structural scanning of uploaded model files before
       they can be used.
     - —
   * - **B5.a** Resilience preparation
     - Shared
     - Versioned storage, immutable images, and infrastructure redeployable from
       code. Trust-side operational continuity remains the organisation's.
     - —
   * - **B5.b** Design for resilience
     - Hub
     - Private subnets with no public addresses; a single hardened edge; account
       separation and centrally managed guardrails under the AWS Landing Zone
       Accelerator.
     - LZA consolidation is in delivery, not complete.
   * - **B5.c** Backups
     - Shared
     - Hub-side artefacts live in versioned cloud storage; backups of trust-side
       systems (OMOP, XNAT) follow the trust's own arrangements.
     - —
   * - **B6.a** Culture
     - Organisational
     - The organisation's own; the platform supports it with documented conventions
       and clear role definitions.
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
     - Cloud-native runtime threat detection; audit trails for administrative
       actions, approvals and role changes; scheduled and on-change secret scanning.
     - Centralised logging under the LZA — in delivery.
   * - **C1.b** Securing logs
     - Hub
     - Access logs held in dedicated storage with lifecycle management; audit records
       separated from application data.
     - LZA log centralisation — in delivery.
   * - **C1.c** Generating alerts
     - Hub
     - Configuration drift detection with alerting; runtime threat-detection
       findings; secret-scanning and code-scanning alerts raised at the point of
       change.
     - —
   * - **C1.d** Triage of security incidents
     - Shared
     - A published disclosure process with a defined acknowledgement window; triaged
       code-scanning alerts. Incident triage for the deployment sits with the
       operating organisation.
     - —
   * - **C1.e** Personnel skills for monitoring and detection
     - Organisational
     - The operating organisation's; not a platform control.
     - —
   * - **C1.f** Understanding user and system behaviour, and threat intelligence
     - Organisational
     - Managed edge rule sets provide a baseline; the platform has no behavioural
       analytics or threat-intelligence capability of its own.
     - No platform capability — gap.
   * - **C2.a** Threat hunting
     - Organisational
     - None today — stated plainly. Penetration testing and pipeline scanning are
       *vulnerability* discovery, recorded under A2.c and B4.d; they are not threat
       hunting and are not claimed here.
     - Platform threat-hunting capability — gap.

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
       acknowledgement window on the platform side. The organisation's incident
       plans — which under the health overlay include personal data breaches —
       cover the deployment.
     - —
   * - **D1.b** Response and recovery capability
     - Shared
     - Recovery is supported by versioned storage, immutable image versions, and
       infrastructure redeployable from code; trust-side operational response
       remains the organisation's.
     - —
   * - **D1.c** Testing and exercising
     - Organisational
     - Recovery-from-code is exercised implicitly by routine redeployment; there is
       no incident-exercising programme for the platform today.
     - No platform-side exercising programme — gap.
   * - **D2.a** Post incident analysis
     - Shared
     - Audit trails support root-cause analysis; findings enter the tracked register
       with an owner and a status.
     - —
   * - **D2.b** Using incidents and near misses to drive improvements
     - Shared
     - Register items are re-verified after fix rather than closed on assertion, and
       fixes land with regression checks at the point of change.
     - —

Objective E — Using and sharing information appropriately
=========================================================

The health-and-care objective, additional to the NCSC CAF — and the natural home of
FLIP's strongest governance material.

.. list-table::
   :header-rows: 1
   :widths: 22 12 46 20

   * - Contributing outcome
     - Scope
     - How FLIP contributes
     - Residual gap
   * - **E1.a** Privacy information
     - Organisational
     - Transparency to patients is each trust's duty. The platform helps it be
       accurate: what leaves a trust is enumerated on this page, so a privacy notice
       can describe the flow concretely.
     - —
   * - **E2.a** Managing data subject rights under UK GDPR
     - Organisational
     - Rights requests are handled where the data already is — patient data never
       leaves the trust's custody, and the platform holds no separate copy to
       complicate rectification or erasure.
     - —
   * - **E2.b** Consent
     - Organisational
     - The lawful basis and consent position are determined per project by the
       organisations involved (see *Data protection roles* above); the per-project
       approval gate is where a trust enforces its position.
     - —
   * - **E2.c** National data opt-out policy
     - Node
     - Applied where the data is prepared — the OMOP database is periodically
       reconciled against the opt-out register, so the decision propagates to every
       query the platform can make (see *National data opt-out* above). The
       reconciliation interval should be agreed and documented per trust.
     - Reconciliation is periodic, not per-query — the interval is the control.
   * - **E3.a** Using and sharing information for direct care
     - Organisational
     - Not applicable — FLIP performs no direct-care processing; every platform use
       is research, under E3.b.
     - —
   * - **E3.b** Using and sharing information for other purposes
     - Node
     - FLIP's home outcome. Per-project, per-trust approval with timestamped audited
       decisions; a trust-set minimum group size below which cohort results are
       suppressed; validated read-only cohort queries; row-level egress gated on the
       trust's own deployment.
     - —
   * - **E4.a** Managing records
     - Shared
     - The retention position is stated plainly in *Retention and deletion* above:
       cohorts are held as queries, not extracts; trust-side imaging outlives the
       project by design; hub-side artefacts persist until removed. The
       organisation's records schedule governs trust-side holdings.
     - No automated end-of-project purge of hub-side artefacts.
   * - **E4.b** Clinical coding
     - Organisational
     - Coding quality in OMOP is the trust's own data-preparation function; the
       platform queries what the extract contains.
     - —
