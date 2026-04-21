################
Platform Support
################

************
Architecture
************

The overall FLIP solution comprises three main features:

1. A **Cloud-hosted Central Hub** providing researchers with the capability to define machine learning projects, discover appropriate datasets at participating Trusts and federate the testing and training of relevant models across Trusts, culminating in the aggregation of a consensus model.
2. A **Secure Enclave** hosted on premise at each individual Trust providing a highly secure environment designed to solely permit requests for training from the Central Hub. A set of FLIP microservices are hosted within the Secure Enclave to handle these requests, scheduling and managing workload for the bespoke compute resources.
3. A **high performance compute stack** designed specifically for the rapid testing and training of machine learning models. This consists of a number of powerful GPUs and a cluster of head nodes to receive requests from the FLIP microservices and orchestrate the compute resource.

.. figure:: ../assets/support/flip_architecture-flip_architecture.png
   :align: center

   FLIP architecture.

Central Hub
============

The Central Hub is a cloud-hosted environment which provides researchers with the capability to identify a cohort and initiate requests to train models in a federated setting. Role based access controls ensure that users will only be able to access their specific data. 

Researchers can define the cohort of data they wish to use for training and testing based on data from the available Trusts, view statistics about the available data, tweak and refine their query, and ultimately decide on a dataset on which to train and test their model. Following this, the model is distributed, trained and tested within the Secure Enclave at each selected Trust before the resultant model is centrally aggregated.

Secure Enclave
===============

The Trust component of FLIP runs in a Secure Enclave at each Trust to facilitate the secure training and testing of the models on the high-performance GPU hardware.

As per security principles, no personally identifying data leaves the Secure Enclave.

FLIP implements a microservice-based architecture. The ``imaging-api`` and ``data-access-api`` are deployed as Dockerised microservices, orchestrated by the ``trust-api``.

**********
Components
**********

Docker
======

All client-side services deployed into the Secure Enclave are running as Dockerised components.

OMOP
====

The `OMOP <https://www.ohdsi.org/data-standardization/>`_ Common Data Model describes a common format and representation of data that allows data from different systems that may have hugely different structures of data to be analysed more easily.

A Common Data Model is needed as Trust data sources will have different formats, structures and representations of data depending on their primary need. To allow for research, assessing and analysing data, a common data model is needed.

The OMOP CDM is implemented as a PostgreSQL database in the Data Centre at each Trust.

XNAT
====

The primary functionality of :term:`XNAT` is to provide a place to store and control access to imaging data such as DICOM series images. This includes user control, search and retrieval and archiving capabilities.

XNAT enables quality control procedures and provides secure access to storage of data.

XNAT includes a pipeline engine to allow complex workflows with multiple levels of automation. This can include things such as converting DICOM to NIfTI file formats.

FL nets
=======

The Federated Learning functionality is provided by either :term:`NVIDIA FLARE` or :term:`Flower Framework`.

The FL services are deployed in a collection of 'nets' or 'federations', with a net consisting of a central API and server with a client at each of the Trusts. 

Each net will have access to GPU resource at each of the Trusts to perform the model training.

FLIP jobs are distributed by the central hub FL scheduler to an available net.

.. figure:: ../assets/support/flip_components_nets_and_scheduler.png
   :align: center

   FL nets and scheduler.

*********
Security
*********

Trusts authenticate to the Central Hub using per-trust API keys. Each trust is identified by its ``TRUST_NAME`` and a secret ``TRUST_API_KEY`` — the hub verifies the SHA-256 hash of the key against its stored ``TRUST_API_KEY_HASHES``. Trust communication payloads are encrypted with a shared ``AES_KEY_BASE64``.

All trust communication is **outbound** — trusts poll the Central Hub for tasks over HTTPS (via the ALB). The hub never makes inbound connections to trusts. FL clients connect outbound to the FL server via the NLB. No inbound firewall rules or port forwarding are required on trust hosts.

Both the Central Hub and Trust EC2 instances run in private subnets with no open inbound ports. Operator access is via AWS Systems Manager Session Manager (SSH-over-SSM). XNAT, Orthanc, and the Trust API swagger docs are accessible via SSM port forwarding only (``make forward-trust``).

See the `Local Deployment Guide <../../../deploy/providers/local/README.md>`_ for details on trust provisioning and authentication setup.

.. figure:: ../assets/support/flip_architecture-flip_network_architecture.png
   :align: center

   FLIP network architecture.

The following is the list of ports required to be opened for the Secure Enclave communication:

.. figure:: ../assets/support/flip_architecture-flip_and_aide_network_architecture_ports.drawio.png
   :width: 600
   :align: center

   FLIP and AIDE required ports.

************
Process Flow
************

FLIP workflow
=============

Once a user has access to FLIP, they can construct a project, add project members and execute an SQL query at each of the consortium Trusts to determine data cohort sizes.

.. figure:: ../assets/support/flip_walkthrough-cohort_query.drawio.png
   :align: center

   FLIP cohort query.

If a sufficient cohort of data can be utilised, the Model Developer will upload their training and validating algorithms to FLIP, along with any other collateral required for training and testing. The Model Developer will indicate which Trusts' data they require and 'stage' the project, awaiting approval from the Trusts that their data can be used for the project.

.. figure:: ../assets/support/flip_walkthrough-upload_collateral.drawio.png
   :align: center

   File uploads.

Once a FLIP administrator has approved the project, FLIP will execute the cohort query at each of the selected Trusts to determine the DICOM series associated with the cohort and begin to copy the images from the Trust PACS system to the local XNAT cache.

.. figure:: ../assets/support/flip_walkthrough-approve_project.drawio.png
   :align: center

   Approved project.

Once the DICOM series have been cached in the local XNAT in each Secure Enclave, the Model Developer will be notified and they can begin the optional process of enriching the data. All users associated with the FLIP project will be provided with XNAT accounts and will be able to log in locally and segment, align, label or otherwise enrich the data prior to providing it to the algorithm for training. Only those users in the original FLIP project will have access to the images in the XNAT repository.

.. figure:: ../assets/support/flip_walkthrough-enrich_images.drawio.png
   :align: center

   Image enrichment using XNAT.

Once all images have been prepared, the Model Developer will be able to initiate the training process. The uploaded files will be deployed out to each of the Trusts and the algorithm will be provided with a dataframe containing the details of the selected cohort. The algorithm will be able to inspect the dataframe and request images from the XNAT cache for training purposes. Any image processing performed during the training process can potentially be written back to the XNAT project for future training cycles.

.. figure:: ../assets/support/flip_walkthrough-start_training_A.drawio.png
   :align: center

   Training start.

Between training cycles, the weighted model will be sent back to the Central Hub to be aggregated and redistributed out to the workers. 

Once all training cycles are completed, the final weighted model and any recorded metrics will be made available to the Model Developer through the FLIP UI.

.. figure:: ../assets/support/flip_walkthrough-finish_training.drawio.png
   :align: center

   Training finish.

*****************
Backup / Restore
*****************

Types of Data
=============

As a system, the FLIP solution handles the following types of data: 
1. Persistent OMOP Common Data Model data, covering demographic, diagnosis and imaging details 
2. Transient XNAT cached image data, potentially enriched locally with segmentation, labelling, etc., sourced from Trust PACS.
3. Log files including event logs and other information generated as part of the operation of the system. 

Backup
======

The data partitions for the PostgreSQL (OMOP), XNAT and Loki (log files) instances are all mounted on the dedicated storage array. This is a RAID 6 PNY appliance with high resilience.

Backup scripts will be run daily to backup each data store to a /backups/ directory on the storage appliance. This should be backed up up by the Trust using their specific backup process, ideally overnight.

.. figure:: ../assets/support/flip_aide_architecture-backups.drawio.png
   :align: center

   FLIP and AIDE backups.


******
Access
******

FLIP accounts
================================

Access to FLIP is granted by a FLIP administrator.

Access to FLIP will be reviewed annually, with dormant accounts being removed.

RBAC
====

FLIP employs role based access control to permit functionality for accounts, managed through the FLIP API and UI. FLIP currently has three access profiles: **Admin**, **Researcher** and **Observer**. For full details of permissions assigned to each role, see :ref:`rbac-roles`.

Admin
-----

FLIP Admins have all platform permissions including:

- Create and manage FLIP user accounts
- Approve and unstage projects
- Delete any project
- Manage deployment mode and site banner
- All Researcher capabilities

Researcher
----------

Researchers are model developers with the following permissions:

- Create and manage projects
- Run and save cohort queries
- Create models and upload files
- Initiate model training
- Stage projects for approval

Observer
--------

Observers have read-only access to projects they are assigned to. They cannot create, edit, or delete resources.
