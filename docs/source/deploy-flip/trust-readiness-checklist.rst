..  Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

.. _trust-readiness-checklist:

##############################
Is your trust ready for FLIP?
##############################

This page is for the people at a trust who decide whether it can join FLIP and who then make it possible: the
information governance lead, the data or informatics lead, the PACS manager and the IT lead. It lists what must be
in place before the FLIP team can add the trust to a network, in plain language, grouped by who owns each item.
Each item links to the technical page behind it, so the engineer who installs the node can start from the same
list.

A tick means the item is done or formally agreed, not planned. The single hardest prerequisite is the first one in
:ref:`readiness-clinical-data`: the trust's clinical records must already be in the OMOP Common Data Model, with
imaging linked by accession number. Without that there is nothing for FLIP to query.

.. contents:: On this page
   :local:
   :depth: 1

**************************
What joining FLIP involves
**************************

Two things join a trust to FLIP: a decision, and a small server inside the trust's network, called the **FLIP
node**. The node holds the trust's OMOP database and an imaging archive, answers research cohort queries with counts
only, pulls an approved project's images from the trust's PACS, and trains models on them locally. It only ever
connects outwards; nothing on the internet can open a connection into the trust.

Patient data never leaves the trust. What leaves is cohort counts above a minimum group size the trust sets, model
updates and status messages. Every research project is approved separately, by the trust, before it touches that
trust's data. :doc:`../overview` has the one-page picture, and :doc:`../governance-and-compliance` the detail an
information governance function will want.

.. _readiness-who-does-what:

*****************************
Who does what, in what order
*****************************

The documented onboarding runbook (:doc:`onboarding-a-trust`) covers the last phase only: it starts when the FLIP
team creates the trust's kit and ends with the node polling the hub. Everything before it is the trust's own
preparation, and it takes far longer than the install.

.. list-table::
   :header-rows: 1
   :widths: 5 22 25 33 15

   * -
     - Phase
     - Who
     - Done when
     - Typical lead time
   * - 1
     - Decide and govern
     - Sponsoring director, information governance lead
     - The trust has decided to take part, named its two roles and closed the data protection items in
       :ref:`readiness-governance`.
     - Weeks to months, driven by the DPIA and lawful-basis work
   * - 2
     - Prepare the clinical data
     - Data or informatics team
     - Records are in OMOP with the imaging extension and the accession-number column filled; vocabulary licences
       are in hand (:ref:`readiness-clinical-data`).
     - Months for a trust new to OMOP; weeks for one that already runs it
   * - 3
     - Prepare imaging and network
     - PACS manager, network team
     - The PACS will serve FLIP's node by accession number, and the firewall allows the outbound destinations
       (:ref:`readiness-imaging`, :ref:`readiness-network`).
     - Weeks, through the trust's change process
   * - 4
     - Provide the host
     - IT infrastructure
     - A server sized for the projects the trust wants to run is installed and reachable by the trust operator
       (:ref:`readiness-hardware`).
     - Weeks, longer if a GPU must be procured
   * - 5
     - Onboard
     - FLIP admin, then the trust operator
     - The runbook on :doc:`onboarding-a-trust`: the FLIP team registers the trust and delivers its kit, the
       operator installs the node, checks it polls and loads the vocabularies.
     - Days
   * - 6
     - First project
     - Trust Admin, researcher
     - A researcher stages a project at the trust, the Trust Admin approves it, the first images are pulled and
       the first training round runs.
     - Days

Phases 2, 3 and 4 can run in parallel once phase 1 has decided to proceed. Phase 5 cannot start until phases 2 to 4
are done: the installation checks neither the OMOP contents nor the PACS, so a gap in either shows up only when the
first project's imaging pull runs.

.. _readiness-governance:

*********************
Governance and people
*********************

*Owner: information governance lead and the sponsoring director.*

- ☐ **The trust has decided to take part** and understands that each research project is approved separately.
  Joining commits the trust to no project; every project is a yes or no on its own, and a no leaves the trust out of
  that project's imaging pull and training.
- ☐ **A Trust Admin is named.** This person approves or declines each project staged at the trust, in the FLIP web
  application. They need a FLIP account; see :ref:`trust-admin-guide`.
- ☐ **A trust operator is named.** Someone in IT who installs the node, keeps it running and runs the occasional
  upgrade. Upgrades are never pushed to a trust; the operator runs them when the trust is ready
  (:ref:`admin-upgrading-sites`).
- ☐ **Data protection is settled for the federated flow:** who is controller for each flow, the lawful basis for
  the research use (where confidential data is used without consent, the relevant support, for example CAG
  section 251) and a Data Protection Impact Assessment. These must be closed before the node handles patient
  data. The "Data protection roles and impact assessment" section of :doc:`../governance-and-compliance` lists
  what to record.
- ☐ **The national data opt-out has been applied** to the trust's OMOP data, and the trust has agreed how often it is
  re-applied. FLIP queries only what that database holds, so an opt-out applied there is honoured in every project.
- ☐ **The trust has chosen its minimum group size** for cohort counts. The default is 10 patients; a smaller cohort is
  reported as if it were empty. The hub cannot lower a trust's setting.
- ☐ **The trust understands what leaves.** Approving a project also approves the release of the trained model to the
  research team, and a trained model is derived data whose disclosiveness the trust should consider.
- ☐ *Optional:* the trust wants its own privacy filter or access rules on top of the defaults. A per-trust governance
  document can tighten them; see "Trust governance document (per trust)" on :ref:`fl-update-privacy`.

.. _readiness-clinical-data:

*************
Clinical data
*************

*Owner: data, informatics or research data team.*

FLIP asks every trust the same cohort question in the same language, and that language is the OMOP Common Data
Model. A trust whose records are not in OMOP cannot answer any cohort query, so no project can use it.

- ☐ **The trust's clinical records are in an OMOP database.** OMOP CDM version 5.4, in PostgreSQL. FLIP reads it
  through a read-only account and writes nothing back. See :doc:`../components/component-omop-database`.
- ☐ **The imaging extension is present.** The MI-CDM tables that describe imaging studies exist and are filled, so an
  imaging study is a row a cohort query can find (:ref:`omop-micdm`).
- ☐ **Every imaging row carries the study's accession number**, exactly as the PACS knows it. This column is FLIP's
  own addition to the standard model, so a standard OMOP conversion does not create it; the data team adds and fills
  it. It is the only link between a patient's records and their images. A row without it, or with a value the PACS
  does not recognise, is a study FLIP can never pull, and nobody is told. :ref:`omop-accession-id` has the rules.
- ☐ **Pseudonymisation is consistent on both sides.** Either records and images are both pseudonymised the same way,
  or neither is. A mismatch breaks the link above.
- ☐ **The conversion covers what researchers will ask for.** FLIP's own cohort queries use the person, condition,
  procedure, observation, visit and imaging tables. The more of the record and the more years the conversion
  covers, the more projects the trust can take part in. FLIP sets no minimum beyond the tables above.
- ☐ **The trust can obtain the OMOP vocabularies under its own licences.** Cohort queries resolve clinical concepts
  through SNOMED CT, LOINC and the other standard vocabularies, which every site downloads itself: the FLIP team
  cannot hand on its copy. Without them the node starts and passes every health check, but every cohort query
  that names a clinical concept returns nothing. What this involves:

  - **Cost:** no licence fees for an NHS trust. SNOMED International charges nothing for SNOMED CT in member
    countries and the UK is one, so the affiliate licence is free; LOINC is free with a registered account; the
    OHDSI vocabularies themselves are free. The UK editions (SNOMED CT UK, dm+d) come from NHS England's TRUD
    service, where most subscriptions are accepted automatically.
  - **Lead time:** days, not months. Three free accounts (OHDSI Athena, TRUD and, if the trust wants LOINC's own
    release, LOINC) and the acceptance of each vocabulary's licence on sign-up. Athena assembles the chosen
    vocabularies into one bundle and emails a download link, which is valid for 72 hours. The bundle FLIP uses is
    about 600 MB zipped and 3.6 GB unpacked, and loading it into the node takes about 25 minutes.
  - **Owner:** the data team, since the same vocabularies are what their OMOP conversion maps to. Start it in
    phase 2, not at install time.

  The roster FLIP's queries need, and the load itself, are described in step 7 of :doc:`onboarding-a-trust`.

.. _readiness-imaging:

*******
Imaging
*******

*Owner: PACS manager.*

The node contains an imaging archive (XNAT) that asks the trust's PACS for an approved project's studies by
accession number, removes identifying fields from the image headers as they arrive, and stores the images for
training. The PACS team has to let that happen.

- ☐ **The PACS answers a study-level query by accession number.** Standard DICOM networking (C-FIND and C-MOVE).
  Every mainstream PACS does this; the question is whether the trust's configuration allows it for a new node.
- ☐ **The PACS team will register the FLIP node as a DICOM destination**: an application entity title, the node's
  address and port, as for any other modality or workstation. "What the PACS team must register" on
  :doc:`../components/component-pacs` lists exactly what.
- ☐ **One network rule inside the trust** lets the PACS send images to the node. This is the only inbound rule FLIP
  needs anywhere, and it is internal to the trust's network.
- ☐ **A retrieval window is agreed.** Image pulls run on a schedule set in the node so they never compete with
  clinical use.
- ☐ **The PACS holds exactly one study per accession number**, and matches the value exactly. Duplicates and loose
  matches are skipped rather than guessed.

.. _readiness-network:

**************
Network and IT
**************

*Owner: network and security team.*

The node only ever connects outwards. No port is opened to the internet, and the Central Hub cannot start a
connection into the trust. What the firewall must allow is a short list of outbound destinations, given in full,
with which ones have been measured behind a deny-everything firewall, on :ref:`deploy-trust-node-network`.

- ☐ **Outbound HTTPS to the Central Hub**, and to the hub's separate federated-learning endpoint on its own port.
  The addresses and port come with the trust's kit. The federated-learning connection is mutual TLS on a
  non-standard port (8002 on the production hub) and must be exempt from TLS inspection. It is the one rule that
  differs from the usual "outbound 443"; the questions below settle how the site can allow it.
- ☐ **Outbound access to the container registries and package sources** the node installs and updates from, as
  listed on :ref:`deploy-trust-node-network`. A trust with no route to the registries needs a mirror; see
  :doc:`deploy-flip-node-in-tre`.
- ☐ **A secure channel to receive the kit.** The kit is a small file holding the trust's keys and must not travel by
  plain email.
- ☐ **The trust's outbound address is known.** The hub identifies a trust by its certificates and API key, not by
  its address, and the node needs no fixed public address of its own. The FLIP team still asks for the address the
  node's traffic leaves the trust from, so the hub's federated-learning edge can admit declared trusts only, and
  needs to hear when it changes.

.. rubric:: Questions for your network team

Whether the federated-learning rule is a five-minute change or a months-long project depends on answers only the
network team has. Settle them before the node is built:

- **How does the node reach the internet?** Directly, through a firewall with an allow-list, is the normal case and
  needs one rule. A site whose only route is an explicit web proxy is not supported yet. A site with no internet
  route for this traffic needs a site-to-site VPN (:ref:`site-to-site-vpn`).
- **Does the firewall classify traffic by application?** Palo Alto, Fortinet and Check Point devices do. A rule that
  allows ``ssl`` only on its default port passes 443 and resets the federated-learning connection on its port: the
  TCP connection opens, the TLS handshake starts, and the connection is reset with nothing read back. Ask for the
  ``ssl`` application to be allowed to the FL endpoint on its port, or for an application override for it.
- **Is outbound TLS decrypted?** Then the FL endpoint needs an exemption. Mutual TLS cannot be decrypted, and a
  re-signed connection fails the client's certificate check.
- **Can rules name a host, or only an address?** The FL endpoint has a fixed address, so either works; give both.
- **Can the node resolve public names?** Some enclaves have no public DNS. The node then needs a hosts entry for the
  hub and the FL endpoint.
- **How long may an idle connection live?** The FL client holds a long-lived connection to the FL server. A firewall
  that drops idle flows after a few minutes forces reconnects. It does not break training, but it is worth knowing.

The request itself, in the form a firewall team can copy: *from the FLIP node to the FL endpoint host, at its fixed
address, TCP on the FL port, mutually authenticated TLS, exempt from decryption, allowed as the* ``ssl`` *application
on that non-default port.* On a Compose node ``make onboard-onprem-trust KIT=<CODE>`` probes every destination from
the host and prints what it cannot reach, and :ref:`deploy-trust-node-network` has the check that tells a blocked
port from a firewall that resets the handshake.

.. _readiness-hardware:

********
Hardware
********

*Owner: IT infrastructure.*

The common case is one Linux server inside the trust. A trust that would rather build in its own cloud
subscription, or that runs Kubernetes, has a supported route for each ("Choose your platform" on
:doc:`deploy-trust-node`); the governance and data items are the same whichever it picks.

How big the server must be depends on the kind of projects the trust wants to take part in, so decide that first:

.. list-table::
   :header-rows: 1
   :widths: 22 30 48

   * - Projects the trust wants
     - GPU
     - What FLIP's own examples needed
   * - **Health-record (tabular) models only**: risk prediction from coded data, no imaging
     - None. A CPU-only host takes part in every project that asks for no GPU
     - The whole node, with the sample data, runs on 4 cores and 16 GB of memory, the size FLIP's own cloud nodes
       default to
   * - **2D imaging**: classification of X-rays and other single images
     - One current NVIDIA GPU
     - FLIP's X-ray classification and fine-tuning examples; the fine-tuning example needs about 15 GB of free
       disk, of which the dataset is about 6 GB
   * - **3D imaging**: segmentation of CT or MRI volumes
     - One GPU with 24 GB of memory or more
     - FLIP's prostate segmentation example is planned for a 24 GB GPU; deeper configurations no longer fit
   * - **Generative models**: diffusion models trained on the trust's images
     - One GPU with 32 GB of memory, or more
     - FLIP's brain MRI diffusion example used about 10 GB of GPU memory per site at the smallest batch size and
       trained for 12 hours (autoencoder stage) plus 6 hours (diffusion stage) on a 32 GB workstation GPU

Whatever the tier:

- ☐ **An Ubuntu 22.04 or newer server or virtual machine**, with an administrator account and internet access for
  installation (:ref:`deploy-flip-node-on-prem`).
- ☐ **Disk for the imaging archive.** The node keeps a copy of each approved project's images, and keeps it after
  the project closes: deleting it is the trust's own decision, under its retention schedule ("Retention and
  deletion" on :doc:`../governance-and-compliance`). Size the disk to the largest cohort the trust expects to
  offer. FLIP's own cloud nodes start at 100 GB for everything, archive included. A chest X-ray study is a few
  megabytes; a CT or MRI study is hundreds.
- ☐ **For GPU tiers, an NVIDIA GPU with driver version 580 or newer, and the NVIDIA Container Toolkit.** FLIP's
  training images bring their own CUDA runtime, so the host needs no CUDA toolkit. A trust can start CPU-only and
  add a GPU later.
- ☐ **Or one of the alternatives:** the Helm chart on the trust's Kubernetes (:doc:`deploy-flip-node-kubernetes`),
  or, built from FLIP's own infrastructure code in the trust's account, an AWS EC2 instance
  (:doc:`deploy-flip-node-aws-ec2`) or EKS cluster (:doc:`deploy-flip-node-aws-eks`), or an Azure VM
  (:doc:`deploy-flip-node-azure`) or AKS cluster (:doc:`deploy-flip-node-azure-aks`).
- ☐ **Or a Trusted Research Environment**, where governance bars live access to the hospital systems. The node then
  runs inside the TRE against a project-specific extract that the TRE operator loads (:doc:`deploy-flip-node-in-tre`).

**********
Next steps
**********

When every item above is ticked, tell the FLIP team which platform the trust has chosen and which release the hub
runs. They start the runbook on :doc:`onboarding-a-trust`; the trust operator picks it up at step 5.
