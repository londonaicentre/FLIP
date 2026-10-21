################
Platform support
################

.. contents:: On this page
   :local:
   :depth: 1

**********
Networking
**********

**All trust traffic is outbound.** Trusts poll the Central Hub for tasks over HTTPS (through its load balancer), and
FL clients connect out to the FL server through the hub's network load balancer (NLB). The hub never connects in to a
trust, so trust hosts need no inbound firewall rules or port forwarding from the internet.

If a site needs it, trust-to-hub traffic can also run over a site-to-site VPN between the trust network and the
Central Hub VPC. This is set up on request, not by default.

.. seealso::

   :ref:`security` explains why FLIP is built this way and the wider controls around it.

.. figure:: ../assets/support/flip_architecture-flip_network_architecture.png
   :align: center

   FLIP network architecture.

Firewall rules
==============

Port 22 (SSH) is never opened.

.. list-table:: Firewall Rules
   :header-rows: 1
   :widths: 50 25 25
   :align: center

   * - Description
     - Inbound
     - Outbound
   * - DICOM query/retrieve from the local PACS (XNAT → PACS)
     -
     - local PACS query/retrieve port
   * - DICOM C-STORE return leg (PACS → XNAT)
     - XNAT DICOM port (``XNAT_PORT``, 8104 by default)
     -
   * - Trust → Central Hub task polling (HTTPS)
     -
     - 443
   * - FL client → FL server (via NLB)
     -
     - configured ``FL_EDGE_PORT`` (else ``FL_SERVER_PORT``)
   * - Operator access to trust host
     - none (SSM Session Manager on the cloud trust EC2; the site's own access elsewhere)
     -

The PACS return leg is the one inbound rule. :doc:`../components/component-pacs` explains it.

Operator access
===============

On the cloud trust EC2, operators connect through AWS Systems Manager Session Manager (SSH over SSM), and XNAT,
Orthanc and the trust-api Swagger docs are reachable only through SSM port forwarding. From the repo root on the
deployment workstation:

.. code-block:: shell

   make -C deploy/providers/AWS forward-trust

On-prem and Kubernetes nodes are reached through the site's own access, such as SSH or ``kubectl port-forward``.

Orthanc also asks for a username and password: use ``ORTHANC_USERNAME`` and ``ORTHANC_PASSWORD`` from the trust's
kit file.

****************
Backup / restore
****************

Types of data
=============

FLIP handles three types of data:

1. **OMOP data** (persistent): the OMOP Common Data Model, covering demographics, diagnoses and imaging details.
2. **XNAT image data** (a cache): images retrieved from the Trust PACS, possibly enriched locally with segmentations,
   labels and other annotations.
3. **Log files**: event logs and other records of the system's operation.

.. important::

   **Deleting a project does not delete its XNAT data.** A project delete in FLIP only marks the platform record as
   deleted. The images could be pulled from PACS again, but local enrichment (segmentations, labels, contours,
   annotations) could not, so FLIP leaves the data in place. Removing it is an explicit action by an administrator at
   the Trust.

Backup
======

FLIP does not run scheduled backups of trust data; a trust backs up its data stores with its own tooling and
arrangements. :doc:`admin-backup-and-restore` lists where each deployment shape keeps state, what FLIP can recreate
and what it cannot, and how to restore.

******
Access
******

- A FLIP administrator grants access to FLIP.
- Access is reviewed every year, and dormant accounts are removed.
- Access is role-based, managed through the FLIP UI and API. There are four roles: **Admin**, **Researcher**,
  **Viewer** and **Trust Admin** (a Researcher who also approves or declines projects for one Trust). See
  :ref:`rbac-roles` for what each can do.
