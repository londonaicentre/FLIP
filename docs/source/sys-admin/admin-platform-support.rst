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

.. figure:: ../assets/support/flip_architecture-flip_architecture.drawio.png
   :width: 600
   :align: center

   FLIP architecture.

Central Hub
============

The Central Hub is a cloud-hosted environment which provides researchers with the capability to identify a cohort and initiate requests to train models in a federated setting. Role based access controls ensure that users will only be able to access their specific data. 

Researchers can define the cohort of data they wish to use for training and testing based on data from the available Trusts, view statistics about the available data, tweak and refine their query, and ultimately decide on a dataset on which to train and test their model. Following this, the model is distributed, trained and tested within the Secure Enclave at each selected Trust before the resultant model is centrally aggregated.

.. figure:: ../assets/support/flip_architecture-central_hub.drawio.png
   :width: 600
   :align: center

   FLIP Central Hub.

The Central Hub implements a serverless architecture to take advantage of the natural horizontal scaling that cloud hosting provides. All business logic is implemented as serverless Lambdas, controlled by Step Functions.

Secure Enclave
===============

The Trust component of FLIP runs in a Secure Enclave at each Trust to facilitate the secure training and testing of the models on the high-performance GPU hardware.

As per the security principles, no personally identifying data will leave the Secure Enclave.

.. figure:: ../assets/support/flip_architecture-secure_enclave.drawio.png
   :width: 600
   :align: center

   FLIP Secure Enclave.


FLIP implements a microservice-based architecture. The Image Service, Import Service and Data Service are deployed as Dockerised microservices, running in Kubernetes, orchestrated by a FLIP API.

**********
Components
**********

Kubernetes
==========

All client-side services deployed into the Secure Enclave are running as Dockerised components in Kubernetes. These are deployed and configured using Terraform scripts for infrastructure and Ansible playbooks for installation and configuration.

OMOP
====

The `OMOP <https://www.ohdsi.org/data-standardization/>`_ Common Data Model describes a common format and representation of data that allows data from different systems that may have hugely different structures of data to be analysed more easily.

A Common Data Model is needed as Trust data sources will have different formats, structures and representations of data depending on their primary need. To allow for research, assessing and analysing data, a common data model is needed.

The OMOP CDM is implemented as a PostgreSQL database in the Data Centre at each Trust.

XNAT
====

`XNAT <https://wiki.xnat.org/documentation>`_ is an open-source platform for imaging research and processing. The primary functionality of XNAT is to provide a place to store and control access to imaging data such as DICOM series images. This includes user control, search and retrieval and archiving capabilities.

XNAT enables quality control procedures and provides secure access to storage of data.

XNAT includes a pipeline engine to allow complex workflows with multiple levels of automation. This can include things such as converting DICOM to NIfTI file formats.

NVIDIA FLARE
============

The Federated Learning functionality is provided by NVIDIA's `NVIDIA FLARE <https://github.com/NVIDIA/NVIDIA FLARE>`_ solution. NVIDIA FLARE is deployed in a collection of 'nets', with a net consisting of a central controller with a worker at each of the Trusts. Each net will have access to a GPU at each of the Trusts to accelerate the model training.

.. figure:: ../assets/support/nvflare_options-nvflare_phase2.drawio.png
   :width: 600
   :align: center

   NVIDIA NVIDIA FLARE.

Training jobs will be distributed to the NVIDIA FLARE controller in a net from a FLIP-administered queue, whereupon the worker nodes in the net will communicate with the controller and co-ordinate the training.

*********
Security
*********

All traffic between the Central Hub and the Secure Enclaves are secured via the means of tunnelling through a VPN tunnel, linking both the Central Hub and the Secure Enclaves. 

This VPN tunnel means that all traffic is encrypted with at least AES-256 encryption, while traversing between the locations.

.. figure:: ../assets/support/flip_architecture-flip_and_aide_network_architecture.drawio.png
   :width: 600
   :align: center

   FLIP and AIDE network architecture.

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
   :width: 600
   :align: center

   FLIP cohort query.

If a sufficient cohort of data can be utilised, the Model Developer will upload their training and validating algorithms to FLIP, along with any other collateral required for training and testing. The Model Developer will indicate which Trusts' data they require and 'stage' the project, awaiting approval from the Trusts that their data can be used for the project.

.. figure:: ../assets/support/flip_walkthrough-upload_collateral.drawio.png
   :width: 600
   :align: center

   File uploads.

Once a FLIP administrator has approved the project, FLIP will execute the cohort query at each of the selected Trusts to determine the DICOM series associated with the cohort and begin to copy the images from the Trust PACS system to the local XNAT cache.

.. figure:: ../assets/support/flip_walkthrough-approve_project.drawio.png
   :width: 600
   :align: center

   Approved project.

Once the DICOM series have been cached in the local XNAT in each Secure Enclave, the Model Developer will be notified and they can begin the optional process of enriching the data. All users associated with the FLIP project will be provided with XNAT accounts and will be able to log in locally and segment, align, label or otherwise enrich the data prior to providing it to the algorithm for training. Only those users in the original FLIP project will have access to the images in the XNAT repository.

.. figure:: ../assets/support/flip_walkthrough-enrich_images.drawio.png
   :width: 600
   :align: center

   Enriched images.

Once all images have been prepared, the Model Developer will be able to initiate the training process. The uploaded files will be deployed out to each of the Trusts and the algorithm will be provided with a dataframe containing the details of the selected cohort. The algorithm will be able to inspect the dataframe and request images from the XNAT cache for training purposes. Any image processing performed during the training process can potentially be written back to the XNAT project for future training cycles.

.. figure:: ../assets/support/flip_walkthrough-start_training_A.drawio.png
   :width: 600
   :align: center

   Training start.

Between training cycles, the weighted model will be sent back to the Central Hub to be aggregated and redistributed out to the workers.

.. figure:: ../assets/support/flip_walkthrough-aggregate_model.drawio.png
   :width: 600
   :align: center

   Aggregated model.

Once all training cycles are completed, the final weighted model and any recorded metrics will be made available to the Model Developer through the FLIP UI.

.. figure:: ../assets/support/flip_walkthrough-finish_training.drawio.png
   :width: 600
   :align: center

   Training finish.

*******
Logging
*******

Elasticsearch
=============

All logs created through the FLIP components running in Kubernetes within the Secure Enclave will be aggregated together into the Elasticsearch index hosted within each Trust Secure Enclave. These logs will be shipped to the Central Hub where Answer Support staff will be able to construct queries in Kibana to interrogate the logs.

Cloudwatch
==========

All AWS components in the Central Hub will output their logs to Cloudwatch. This will aggregate the logs and allow central querying. Logs will also be shipped to the central Elasticsearch index to allow for cross-source querying and reporting.

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

The data partitions for the PostgreSQL (OMOP), XNAT and Elasticsearch (log files) instances are all mounted on the dedicated storage array. This is a RAID 6 PNY appliance with high resilience.

Backup scripts will be run daily to backup each data store to a /backups/ directory on the storage appliance. This should be backed up up by the Trust using their specific backup process, ideally overnight.

.. figure:: ../assets/support/flip_aide_architecture-backups.drawio.png
   :width: 600
   :align: center

   FLIP and AIDE backups.


******
Access
******

How to set up new FLIP accounts
================================

Access to FLIP is granted by a FLIP administrator - these are currently:

TODO
.. list of FLIP contact details for administrators - Haleema / Jawad?

Access to FLIP will be reviewed annually, with dormant accounts being removed.

RBAC
====

FLIP employs role based access control to permit functionality for accounts, this is handled by AWS Cognito through the FLIP UI. FLIP currently has two access profiles, **Administrator** and **Model Developer**. 

Administrator
-------------

FLIP Administrators have permissions to:

- Create new FLIP user accounts
- Modify the permissions of existing user accounts
- Approve existing projects


Researcher
----------

Researchers are model developers with the following permissions:

- Create new projects
- Add existing users to projects
- Submit projects for approval
- Train models
