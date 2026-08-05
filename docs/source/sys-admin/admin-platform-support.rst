################
Platform Support
################

***********
Networking
***********

All trust communication is **outbound** — trusts poll the Central Hub for tasks over HTTPS
(via the ALB), and FL clients connect outbound to the FL server via the NLB. The hub never
makes inbound connections to trusts, so no inbound firewall rules or port forwarding are
required on trust hosts. Operator access is via AWS Systems Manager Session Manager
(SSH-over-SSM); XNAT, Orthanc, and the trust-api Swagger docs are reachable only through SSM
port forwarding (``make forward-trust``). Orthanc additionally requires HTTP basic auth — log in
with the trust kit file's ``ORTHANC_USERNAME``/``ORTHANC_PASSWORD``.

Trust-to-hub traffic can additionally be carried over a site-to-site VPN between the trust
network and the Central Hub VPC, provisioned on request rather than by default.

See :ref:`security` for the rationale behind this design and the wider set of controls it
sits within. The operational detail — the architecture and the ports to open — follows here.

.. figure:: ../assets/support/flip_architecture-flip_network_architecture.png
   :align: center

   FLIP network architecture.

The ports required for trust-host communication are listed below. Port 22 (SSH) is never
opened.

.. list-table:: Firewall Rules
   :header-rows: 1
   :widths: 50 25 25
   :align: center

   * - Description
     - Inbound
     - Outbound
   * - DICOM ingestion from local PACS into the trust XNAT
     -
     - local PACS DICOM ports
   * - Trust → Central Hub task polling (HTTPS)
     -
     - 443
   * - FL client → FL server (via NLB)
     -
     - configured ``FL_SERVER_PORT``
   * - FL client per-run dependency install, Flower backend (HTTPS)
     -
     - 443 to PyPI (``pypi.org``, ``files.pythonhosted.org``) and ``download.pytorch.org``
   * - Operator access to trust host
     - none (SSM Session Manager)
     -

*****************
Backup / Restore
*****************

Types of Data
=============

As a system, the FLIP solution handles the following types of data:

1. Persistent OMOP Common Data Model data, covering demographic, diagnosis and imaging details.
2. Transient XNAT cached image data, potentially enriched locally with segmentation, labelling, etc., sourced from Trust PACS.
3. Log files including event logs and other information generated as part of the operation of the system.

Backup
======

The data partitions for the PostgreSQL (OMOP), XNAT and Loki (log files) instances are all mounted on the dedicated storage array. This is a RAID 6 PNY appliance with high resilience.

Backup scripts will be run daily to backup each data store to a /backups/ directory on the storage appliance. This should be backed up up by the Trust using their specific backup process, ideally overnight.

.. figure:: ../assets/support/flip_architecture-backups.drawio.png
   :align: center

   FLIP backups.

******
Access
******

Access to FLIP is granted by a FLIP administrator.

Access to FLIP will be reviewed annually, with dormant accounts being removed.

FLIP employs role based access control to permit functionality for accounts, managed through the FLIP API and UI. FLIP currently has three access profiles: **Admin**, **Researcher** and **Viewer**. For full details of permissions assigned to each role, see :ref:`rbac-roles`.
