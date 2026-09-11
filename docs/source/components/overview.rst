.. _components-overview:

########
Overview
########

************
Architecture
************

The overall FLIP solution comprises three parts:

1. A **cloud-hosted Central Hub** providing researchers with the capability to define machine learning
   projects, discover appropriate datasets at participating Trusts and federate the testing and training of
   models across Trusts, culminating in the aggregation of a consensus model.
2. A **Secure Enclave** (a *FLIP node*) hosted at each individual Trust, designed to permit only requests for
   training that the Trust itself has fetched from the Central Hub. A set of FLIP microservices runs inside the
   enclave to serve those requests, alongside the Trust's imaging archive and its :term:`OMOP` database.
3. **GPU compute at each Trust**, used by that Trust's FL client and nothing else: model training and evaluation
   run on the Trust's own hardware, against the Trust's own data, and only model updates leave.

.. figure:: ../assets/support/flip_architecture-flip_architecture.png
   :align: center

   FLIP architecture.

Central Hub
===========

The Central Hub is a cloud-hosted environment which provides researchers with the capability to identify a
cohort and initiate requests to train models in a federated setting. Role based access controls ensure that
users will only be able to access their specific data.

Researchers can define the cohort of data they wish to use for training and testing based on data from the
available Trusts, view statistics about the available data, tweak and refine their query, and ultimately decide
on a dataset on which to train and test their model. Following this, the model is distributed, trained and
tested within the Secure Enclave at each selected Trust before the resultant model is centrally aggregated.

The hub is the only internet-facing part of FLIP. Its services are described on :doc:`component-central-hub`;
the AWS shape they take, in either of the two supported deployment modes, on :ref:`deploy-central-hub`.

Secure Enclave
==============

The Trust component of FLIP runs in a Secure Enclave at each Trust to facilitate the secure training and
testing of models on the Trust's own GPU hardware. As per security principles, no personally identifying data
leaves the Secure Enclave.

FLIP implements a microservice-based architecture on the trust side too. Three FastAPI services — the
``trust-api``, ``imaging-api`` and ``data-access-api`` — run as containers next to XNAT, the OMOP database
and one FL client per net, either under Docker Compose or from the Kubernetes Helm chart. Nothing on the hub
dials into a Trust: the ``trust-api`` polls the hub for work over outbound HTTPS and the FL client opens the
outbound connection to the FL server, so a Trust needs no inbound firewall rule for FLIP itself. The three
services are described on :doc:`component-trust-apis`.

**********
Components
**********

Each component below has its own page; this section is the map.

Central Hub
===========

The web UI (``flip-ui``), the central API (``flip-api``) with its PostgreSQL database, and the hub half of every
FL net, with Cognito for authentication and S3 for model files and results. See :doc:`component-central-hub`
for the services and :ref:`deploy-central-hub` for how they are laid out on AWS.

FL nets
=======

The Federated Learning functionality is provided by either :term:`NVIDIA FLARE` or :term:`Flower Framework`,
deployed as a collection of *nets*: each net is an FL API and FL server on the hub plus one FL client at each
Trust, and the hub's scheduler assigns queued training jobs to whichever net is free. The page also covers
job types, required files, training configuration and the privacy filters applied to model updates. See
:doc:`component-fl-nets`.

Trust APIs
==========

The three trust-side services: ``trust-api`` fetches tasks from the hub and reports results; ``imaging-api``
drives XNAT (project creation, retrieval from PACS, conversion, downloads for training); ``data-access-api``
runs validated cohort queries against OMOP. See :doc:`component-trust-apis`.

OMOP Database
=============

The `OMOP <https://www.ohdsi.org/data-standardization/>`_ Common Data Model gives every Trust's electronic
health data the same shape, so one cohort query can run unchanged at each site. FLIP extends it with the
MI-CDM imaging tables that link records to accession numbers. The OMOP CDM is implemented as a PostgreSQL
database inside each Trust's enclave. See :doc:`component-omop-database`.

XNAT
====

:term:`XNAT` stores and controls access to the imaging data retrieved for a project — DICOM series and the
NIfTI files derived from them — and runs the anonymisation and DICOM-to-NIfTI pipelines on receipt. One XNAT
instance runs at each Trust. See :doc:`component-xnat`.

PACS
====

The Trust's own Picture Archiving and Communication System, from which XNAT retrieves the studies belonging to
an approved cohort by DICOM query/retrieve. FLIP never holds a copy of the PACS; for development a mocked
Orthanc server stands in for it. See :doc:`component-pacs`.

Logging Stack
=============

Structured JSON logs from the trust services, collected by Grafana Alloy into Loki and viewed in Grafana, all
inside the Trust. See :doc:`component-logging-stack`.

Deployment targets
==================

The Central Hub is provisioned on AWS with Terraform/OpenTofu, self-contained in one account or on a Landing
Zone Accelerator estate — :ref:`deploy-central-hub`. A Trust node runs
as a Docker Compose stack on a host inside the Trust (:doc:`/deploy-flip/deploy-flip-node-on-prem`), inside a
Trusted Research Environment (:doc:`/deploy-flip/deploy-flip-node-in-tre`), or on Kubernetes via the Helm chart
at `deploy/providers/kubernetes/ <https://github.com/londonaicentre/FLIP/blob/develop/deploy/providers/kubernetes/README.md>`_.
The AWS-hosted mock Trusts used for staging are the same Compose stack on an EC2 instance.
