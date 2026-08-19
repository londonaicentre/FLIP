.. _glossary:

########
Glossary
########

.. glossary::

    **Digital Imaging and Communications in Medicine (DICOM)**
      This is the standard data format for medical imaging information and related data, and enables integration between medical imaging devices and picture archiving and communication systems (PACS).

    **Federated learning**
      Federated learning is a machine learning (ML) technique that trains an algorithm across multiple decentralized edge devices or servers holding local data samples, without exchanging them.

    **Flower Framework**
      Flower is an open-source framework for building federated learning systems. It provides tools and libraries to facilitate the development and deployment of federated learning applications. For more information, please see its `official documentation <https://flower.ai/docs/framework/>`_.

    **NVIDIA FLARE**
      NVIDIA Federated Learning Application Runtime Environment is a domain-agnostic, open-source, extensible SDK that allows researchers and data scientists to adapt existing ML/DL workflows to a federated paradigm.
      For more information, please see the documentation `here <https://nvflare.readthedocs.io/en/main/index.html>`_.

    **OMOP**
      Observational Medical Outcomes Partnership (OMOP). In FLIP documentation, this typically refers to the OMOP Common Data Model (CDM) used to standardise clinical data for federated cohort queries.

    **PACS**
      Picture Archiving and Communication System (PACS), the clinical system used to store and retrieve medical imaging studies (such as DICOM series).

    **AE Title**
      Application Entity Title. A DICOM system's name on the network, at most 16 characters. When one system connects to another it announces which AE title it is calling and which it is calling from, and the receiver accepts the connection only if the called title is its own. AE titles are names rather than addresses: the IP and port are configured alongside them. See :doc:`components/component-pacs`.

    **SCU / SCP**
      Service Class User and Service Class Provider — DICOM's terms for the two sides of a service. The SCU requests it; the SCP provides it. The roles are per operation, not per system, and can swap mid-exchange: a PACS is the SCP for C-MOVE, then becomes the SCU of the C-STORE it opens back to the destination. XNAT is likewise an SCU when it queries a PACS and an SCP when it receives the images.

    **DIMSE**
      DICOM Message Service Element, the classic DICOM network protocol (as opposed to the newer HTTP-based DICOMweb). FLIP retrieves imaging over DIMSE.

    **C-ECHO / C-FIND / C-MOVE / C-STORE**
      The DIMSE operations FLIP uses. ``C-ECHO`` is a connectivity check. ``C-FIND`` searches a PACS, in FLIP's case by accession number. ``C-MOVE`` asks the PACS to send a study to a named destination. ``C-STORE`` is the image transfer itself — and because C-MOVE names a destination rather than returning data inline, the C-STORE arrives on a *new* connection opened by the PACS back to that destination.

    **DQR**
      DICOM Query-Retrieve, the XNAT plugin that performs the C-FIND and C-MOVE operations against a trust PACS on FLIP's behalf.

    **RBAC**
      Role Based Access Control (RBAC) defines what users are able to access within the FLIP platform.

    **XNAT**
      XNAT is an open-source imaging informatics platform used in FLIP to store, manage and access imaging data for research workflows. For more information on XNAT, please see the `documentation <https://wiki.xnat.org/documentation>`_.
