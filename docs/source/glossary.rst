.. _glossary:

########
Glossary
########

.. glossary::

    **AE Title**
      Application Entity Title: a DICOM system's name on the network, at most 16 characters. A receiver accepts a
      connection only if the caller names the receiver's own AE title; the IP address and port are configured
      alongside it. See :doc:`components/component-pacs`.

    **C-ECHO / C-FIND / C-MOVE / C-STORE**
      The DIMSE operations FLIP uses: ``C-ECHO`` checks connectivity, ``C-FIND`` searches a PACS (in FLIP, by
      accession number), ``C-MOVE`` asks the PACS to send a study to a named destination, and ``C-STORE`` transfers
      the images. Because C-MOVE names a destination rather than returning data inline, the C-STORE arrives on a
      *new* connection that the PACS opens back to that destination.

    **Digital Imaging and Communications in Medicine (DICOM)**
      The standard data format for medical imaging and related data. It lets imaging devices and picture archiving
      and communication systems (PACS) work together.

    **DIMSE**
      DICOM Message Service Element: the classic DICOM network protocol, as opposed to the newer HTTP-based DICOMweb.
      FLIP retrieves imaging over DIMSE.

    **DQR**
      DICOM Query-Retrieve: the XNAT plugin that runs the C-FIND and C-MOVE operations against a trust's PACS on
      FLIP's behalf.

    **Federated learning**
      A machine learning technique that trains a model across several sites that each hold their own data, without
      exchanging that data.

    **FL kit slot**
      The name an FL client is known by inside a federation (``Trust_1``, ``Trust_2``, …), fixed by its provisioned
      participant kit: a certificate on FLARE, a SuperNode key pair on Flower. Slots are provisioned once per
      :term:`FL net`; a trust claims one when it registers with the Central Hub, and that name, not the trust's
      hub-side name, is what appears in FL logs and in an app's ``AGGREGATION_WEIGHTS`` keys. See
      :ref:`flip-fl-nets`.

    **FL net**
      One federation unit in FLIP: an FL API and an FL server on the Central Hub, plus one FL client at each
      participating trust. The hub's scheduler assigns each queued training job to a free net. See
      :ref:`flip-fl-nets`.

    **Flower Framework**
      An open-source framework for building federated learning systems, and one of the two FL backends FLIP
      supports. See its `official documentation <https://flower.ai/docs/framework/>`_.

    **Landing Zone Accelerator (LZA)**
      AWS's reference implementation of a multi-account estate with centrally managed network, guardrails and
      logging. FLIP's Central Hub deploys onto such an estate: the workload account uses the network the estate
      provides and ingress arrives through the estate's edge. See :ref:`deploy-central-hub-aws-lza`.

    **MAP**
      MONAI Application Package: a container, built with the MONAI Deploy App SDK, that wraps a trained model as a
      DICOM-in, DICOM-out inference pipeline. FLIP uses it to take a model from a completed federated run to
      something deployable in a clinical imaging workflow. See :doc:`working-with-flip-apps/wrap-model-for-deployment`.

    **MI-CDM**
      Medical Imaging Common Data Model: the OHDSI extension that adds imaging to the OMOP CDM, through the
      ``image_occurrence`` and ``image_feature`` tables. FLIP adds an ``accession_id`` column to
      ``image_occurrence`` so that a cohort row can be matched to its DICOM study in the trust's :term:`PACS`. See
      :ref:`omop-schema`.

    **NVIDIA FLARE**
      NVIDIA Federated Learning Application Runtime Environment, an open-source SDK for adapting machine learning
      workflows to federated learning, and one of the two FL backends FLIP supports (written FLARE elsewhere in
      these docs). See its `documentation <https://nvflare.readthedocs.io/en/main/index.html>`_.

    **OMOP**
      Observational Medical Outcomes Partnership. In these docs it usually means the OMOP Common Data Model (CDM),
      the standard structure FLIP uses for clinical data in federated cohort queries.

    **PACS**
      Picture Archiving and Communication System: the clinical system that stores and retrieves medical imaging
      studies, such as DICOM series.

    **RBAC**
      Role-based access control: what each user can do in FLIP is set by their role. See :ref:`rbac-roles`.

    **SCU / SCP**
      Service Class User and Service Class Provider: the side of a DICOM service that requests it and the side that
      provides it. The roles are per operation, not per system: a PACS is the SCP for C-MOVE, then the SCU of the
      C-STORE it opens back to the destination, and XNAT is an SCU when it queries a PACS and an SCP when it
      receives the images.

    **Secure enclave**
      The FLIP node at a partner trust: dedicated storage and compute inside the trust's firewall, holding that
      trust's imaging and OMOP data and running the FL client that trains on it. Patient data stays within the
      enclave; only model updates and aggregate cohort statistics leave. See :doc:`overview`.

    **TRE**
      Trusted Research Environment: a governed, access-controlled analysis environment holding an approved subset of
      a trust's data. FLIP can be deployed inside one instead of on-premises, in which case cohort queries run on the
      pre-loaded subset rather than the trust's full population. See :ref:`tre-deployment`.

    **Trust Admin**
      A FLIP user who approves or declines projects on behalf of one participating trust, and otherwise has
      Researcher access. While a trust has a Trust Admin, only its Trust Admins decide its projects; a trust without
      one is decided by a Central Hub administrator. See :ref:`trust-admin-guide`.

    **XNAT**
      An open-source imaging informatics platform that FLIP uses to store, manage and access imaging data for
      research. See the `XNAT documentation <https://wiki.xnat.org/documentation>`_.
