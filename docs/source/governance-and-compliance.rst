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
telemetry such as task status. Model updates are mathematical parameters, not records.

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
requires no inbound exposure to the outside world at all. The one inbound rule is
internal to the trust: retrieval from the trust's PACS is a pull, so the PACS opens a
connection back to XNAT to deliver the studies it was asked for, scoped to that PACS on
the DICOM port. A site-to-site VPN can be provisioned on request
where a trust's policy calls for network-layer separation as well.

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

*****************
Access governance
*****************

**Role-based access control.** Users hold one of three defined roles — Admin,
Researcher, or Viewer — each with an explicit permission set, applied default-deny.
Access to a specific project's data additionally requires membership of that project.
See :ref:`User Roles <rbac-roles>` for the full permission matrix.

**Multi-factor authentication is mandatory** for every authenticated user, enforced on
every request rather than only at login.

**Access review.** Access is granted by a FLIP administrator, reviewed annually, and
dormant accounts are removed.

**Separation from the trust's wider estate.** A FLIP role does not confer access to the
trust's broader imaging systems. Retrieval from the trust PACS is restricted to the FLIP
service account and to accounts explicitly granted the DQR role; an ordinary XNAT account
cannot pull imaging on demand.

**Audit.** Administrative actions, project approvals, and role changes are recorded.

**********************************
Assurance and independent scrutiny
**********************************

- FLIP is subject to **independent security review** and to a **commissioned penetration
  test**, with findings tracked in a live register carrying an owner and a status for
  each item, and re-verified rather than left to age.
- A **published security policy** provides a private reporting route and a coordinated
  disclosure process.
- **Automated security checking** runs continuously in the delivery pipeline, so
  regressions are caught at the point of change.
- FLIP is **open source under Apache 2.0** — the code, its change history, and its
  automated checks are publicly inspectable, which is an unusually direct form of
  supplier assurance.

.. _compliance-mappings:

*****************************
Appendix A — Cyber Essentials
*****************************

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
       newly published packages; prompt remediation of disclosed vulnerabilities;
       releases gated on passing automated tests.
     - Operational
   * - **4. User access control**
     - Managed identity provider with full token verification; MFA enforced on every
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

*****************************************************
Appendix B — NHS Data Security and Protection Toolkit
*****************************************************

Mapped against the National Data Guardian's ten Data Security Standards.

.. list-table::
   :header-rows: 1
   :widths: 20 55 25

   * - NDG standard
     - How FLIP addresses it
     - Status
   * - **1. Personal confidential data**
     - The federated architecture means patient data never leaves the trust that holds
       it; per-trust project approval enforces need-to-know; per-trust isolation is
       structural, not procedural. The National Data Opt-Out is applied by periodic
       reconciliation of the OMOP dataset, so it propagates to every query the platform
       can make.
     - Operational
   * - **2. Staff responsibilities**
     - An organisational responsibility of each deploying trust; the platform supports it
       with defined roles, RBAC, and audit trails.
     - Organisational (platform supports)
   * - **3. Training**
     - An organisational responsibility of each deploying trust; not a platform control.
     - Organisational
   * - **4. Managing data access**
     - Default-deny RBAC with three defined roles; MFA on every request; per-trust,
       per-project approval with timestamped audited decisions; immediate effect on
       administrative MFA reset; least-privilege service accounts; PACS retrieval
       restricted at the trust imaging platform.
     - Operational
   * - **5. Process reviews**
     - A tracked security remediation programme with post-fix verification; independent
       penetration testing; audit trails that support post-incident review.
     - Operational
   * - **6. Responding to incidents**
     - A published coordinated disclosure and private advisory workflow with a defined
       acknowledgement window; audit trails to support investigation.
     - Operational
   * - **7. Continuity planning**
     - Versioned storage, immutable image versions, and infrastructure redeployable from
       code; trust-side operational continuity remains an organisational responsibility.
     - Shared with organisation
   * - **8. Unsupported systems**
     - Continuous dependency monitoring, supply-chain cooldown, prompt vulnerability
       remediation, and explicit version pinning of platform components.
     - Operational
   * - **9. IT protected from cyber threats**
     - Layered automated scanning and independent penetration testing; least-privilege
       cloud design; encryption in transit and at rest; runtime threat detection; an AWS
       Landing Zone Accelerator foundation.
     - Operational
   * - **10. Accountable suppliers**
     - Transparent, documented controls; an open-source codebase; a published security
       policy — supporting supplier-assurance due diligence between FLIP and partner
       organisations.
     - Supports organisational process

********************************************
Appendix C — NCSC Cyber Assessment Framework
********************************************

Objective A — Managing security risk
====================================

.. list-table::
   :header-rows: 1
   :widths: 20 55 25

   * - CAF principle
     - How FLIP addresses it
     - Status
   * - **A1 Governance**
     - Documented security rules and engineering conventions; named ownership; a live
       tracked remediation programme; a published coordinated disclosure policy.
     - Operational
   * - **A2 Risk management**
     - Independent penetration testing with severity-prioritised, tracked, and verified
       remediation; recurring independent review.
     - Operational
   * - **A3 Asset management**
     - Infrastructure defined as code serves as the authoritative inventory; immutable
       pinned image versions; dependency lockfiles act as a software bill of materials.
     - Operational
   * - **A4 Supply chain**
     - Automated dependency vulnerability monitoring; 72-hour cooldown on new packages;
       lockfile-pinned builds; curated base application templates.
     - Operational

Objective B — Protecting against cyber attack
=============================================

.. list-table::
   :header-rows: 1
   :widths: 20 55 25

   * - CAF principle
     - How FLIP addresses it
     - Status
   * - **B1 Policies and processes**
     - A secure development lifecycle; peer-reviewed changes with automated acceptance
       checks; contributor sign-off; security rules enforced by tooling rather than
       convention.
     - Operational
   * - **B2 Identity and access control**
     - Managed identity provider with token verification; MFA on every authenticated
       request; default-deny RBAC; per-project membership checks; per-trust credential
       scoping; PACS retrieval restricted to service accounts and granted roles; brokered
       operator access with no exposed remote-login port.
     - Operational
   * - **B3 Data security**
     - Patient data never leaves the trust; encrypted transport with an additional
       payload-encryption layer; mutually authenticated TLS between federated learning
       participants; encryption at rest under managed keys; no standing production
       database credential.
     - Operational; per-trust authenticated payload encryption in delivery
   * - **B4 System security**
     - Container hardening with least privilege, dropped capabilities, and
       no-new-privileges; secure-by-default configuration; immutable images; automated
       secret scanning; services that refuse to start without required credentials.
     - Operational; training-container isolation in delivery
   * - **B5 Resilient networks and systems**
     - Private-by-default networking; an outbound-only trust boundary; a single hardened
       edge; an AWS Landing Zone Accelerator foundation providing account separation and
       centrally managed guardrails.
     - Operational
   * - **B6 Staff awareness and training**
     - An organisational responsibility of the deploying trust; the platform supports it
       with documented conventions and clear role definitions.
     - Organisational (platform supports)

Objective C — Detecting cyber security events
=============================================

.. list-table::
   :header-rows: 1
   :widths: 20 55 25

   * - CAF principle
     - How FLIP addresses it
     - Status
   * - **C1 Security monitoring**
     - Cloud-native runtime threat detection; audit trails; scheduled and on-change secret
       scanning; configuration drift detection with alerting; centralised logging under
       the Landing Zone Accelerator.
     - Operational
   * - **C2 Proactive event discovery**
     - Independent penetration testing; continuous automated scanning in the delivery
       pipeline; triaged code-scanning alerts.
     - Operational

Objective D — Minimising the impact of incidents
================================================

.. list-table::
   :header-rows: 1
   :widths: 20 55 25

   * - CAF principle
     - How FLIP addresses it
     - Status
   * - **D1 Response and recovery planning**
     - A coordinated disclosure and private advisory workflow with a defined
       acknowledgement window; recovery supported by versioned storage, immutable images,
       and infrastructure redeployable from code; trust-side operational response remains
       an organisational responsibility.
     - Shared with organisation
   * - **D2 Lessons learned**
     - A tracked remediation programme with post-fix verification; audit trails supporting
       post-incident review and continuous improvement.
     - Operational

.. note::

   **Status values.** *Operational* — the control is in place. *Organisational* — the
   control belongs to the deploying trust rather than the platform. *Shared* —
   responsibility is divided between the platform and the deploying organisation. Where a
   control is noted as "in delivery", it is scheduled work rather than a gap left
   unaddressed.
