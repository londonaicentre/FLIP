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

.. _deploy-trust-node:

###################
Deploy a trust node
###################

A **trust node** is FLIP's footprint inside a participating trust: trust-api, imaging-api, data-access-api, the FL
client, an OMOP database, XNAT, a PACS (mocked by Orthanc until you connect your own) and a logging stack. It polls
the Central Hub for work over HTTPS. **All traffic to the hub is outbound**; no inbound port is opened for it.

Read this page first. It covers what every trust node needs, whatever it runs on. Then follow
:doc:`onboarding-a-trust`, which sends you to your platform's page for the install.

.. contents:: On this page
   :local:
   :depth: 1

**********
Who's who
**********

Onboarding is split between two people. Neither needs access to the other's machines.

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Role
     - Has
     - Does
   * - **FLIP admin**
     - Hub AWS credentials and the hub env file
     - Creates, registers and delivers the trust's kit; opens the FL load balancer to the trust.
   * - **Trust operator**
     - The trust's host or cluster; no AWS access
     - Installs the kit, starts the node, loads the OMOP vocabulary.

What is a kit?
==============

A trust's **kit** is everything it needs to join one FLIP network:

- **The kit file**, ``trust/.env.<CODE>.<env>`` (``<CODE>`` is the trust's short code). On a Docker Compose host
  the operator's copy is named after the FL slot instead, ``trust/.env.<slot>``. It holds four blocks:

  - *Hub-shared* settings, the same for every trust on the hub (encryption key, hub URL, FL backend, image tags);
  - *Kit credentials*, this trust's own keys and FL slot, written when the trust is registered;
  - *Host-local profile*, ports and directories for this host;
  - *Trust-local credentials*, passwords for the node's own databases and services.

  The admin owns the first two; the operator edits only the last two.

- **This trust's share of the FL participant kit**: the certificates and keys its FL client uses to reach the FL
  server.

.. warning::

   A kit holds a plaintext API key, the encryption key and an FL TLS private key. Move it over an encrypted channel
   only.

.. seealso::

   :ref:`trust-authentication` for what each credential does and how the hub identifies a trust.

********************
Choose your platform
********************

.. list-table::
   :header-rows: 1
   :widths: 22 48 30

   * - Platform
     - Use it when
     - Install guide
   * - **Docker Compose host**
     - The trust runs an Ubuntu host (physical or VM) with governed access to its own OMOP database and PACS. The
       common case.
     - :doc:`deploy-flip-node-on-prem`
   * - **Azure VM**
     - The trust builds the node from code in its own Azure subscription. It is a Docker Compose host on a VM
       that configures itself and accepts no inbound connection.
     - :doc:`deploy-flip-node-azure`
   * - **Kubernetes**
     - The trust's platform team runs Kubernetes. The Helm chart is validated on single-node k3s; read its known
       limitations first.
     - :doc:`deploy-flip-node-kubernetes`
   * - **Inside a TRE**
     - Governance requires data access through a Trusted Research Environment loaded with a project-specific
       subset. The node itself installs as a Docker Compose host.
     - :doc:`deploy-flip-node-in-tre`

The mock trust on an AWS EC2 instance, which the Central Hub deployment can create for testing, is covered on
:doc:`deploy-central-hub-aws`.

****************
Before you start
****************

Trust data
==========

The node reads two systems. Both must already exist, and agree with each other, before you deploy:

- An **OMOP database** (PostgreSQL) holding the trust's clinical data in the ``omop`` schema: OMOP CDM 5.4 plus the
  MI-CDM imaging tables (``image_occurrence``, ``image_feature``). FLIP reads it through a read-only role and writes
  nothing back.
- A **PACS** that answers a study-level query by accession number. Agree the checklist in
  :doc:`../components/component-pacs` with your PACS manager.

**The accession number links the two.** Every imaging row in ``image_occurrence`` must carry, in its ``accession_id``
column, the accession number the PACS answers to for that study (DICOM tag ``(0008,0050)``). This column is FLIP's
own addition to MI-CDM, so a standard OMOP ETL does not create it: your data team adds and fills it during the OMOP
load. :ref:`omop-accession-id` has the DDL and what to fill it from.

Rows may be per study or per series; FLIP imports each study once. Each value must be exactly one study's accession
number:

- never empty;
- never containing ``*``, ``?`` or ``\``. A PACS treats these as wildcards, so FLIP refuses to send them. The row is
  dropped with a warning in the imaging-api log, and **that study is never pulled**.

.. important::

   The deployment checks neither system. A gap only shows up when the first project's imaging pull runs, so confirm
   both with the data and PACS teams first.

Central Hub
===========

The hub must be deployed in AWS (:doc:`deploy-central-hub-aws`) before a trust can be registered, receive its FL
participant kit and be let through the FL load balancer. On a Landing Zone Accelerator estate the FL entry point is
the networking account's edge instead (:doc:`deploy-central-hub-aws-lza`). Shared prerequisites are in
:doc:`deploy-central-hub`.

.. _deploy-trust-node-network:

Network requirements
====================

**No inbound port forwarding from the internet is needed.** trust-api polls the hub, and the FL client connects out
to the FL server. The one exception is retrieval from the trust's own PACS, which needs a rule inside the trust's
network (see :doc:`../components/component-pacs`).

The node must reach two **separate** hub endpoints. They have different hostnames behind different load balancers,
so allow both:

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Endpoint
     - Example host
     - Port
   * - Central Hub API (HTTPS)
     - ``app.flip.aicentre.co.uk``
     - 443
   * - FL server (gRPC or HTTP)
     - ``fl.app.flip.aicentre.co.uk``
     - The kit's ``FL_EDGE_PORT``, else ``FL_SERVER_PORT``: 8002 on production, 8003 on LZA staging

The FL server's load balancer admits only the public IPs the admin has listed. Step 6 of :doc:`onboarding-a-trust`
adds the trust's IP; repeat it whenever that IP changes. On a Landing Zone Accelerator estate there is no such list:
the networking account's edge decides what reaches the FL server.

The node also needs outbound access to pull FLIP's container images (``ghcr.io/londonaicentre``) and OS packages.
The Docker Compose deploy commands always pull, so this applies in a TRE too (see :doc:`deploy-flip-node-in-tre`).
Kubernetes adds a few destinations of its own; see :doc:`deploy-flip-node-kubernetes`.

.. toctree::
   :maxdepth: 1

   onboarding-a-trust
   deploy-flip-node-on-prem
   deploy-flip-node-azure
   deploy-flip-node-kubernetes
   deploy-flip-node-in-tre
