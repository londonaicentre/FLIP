.. _flip-pacs:

####
PACS
####

The Picture Archiving and Communication System (PACS) is the clinical system that stores a trust's
imaging. FLIP does not hold a copy of it: imaging is retrieved from the trust's own PACS, on demand,
for the studies belonging to an approved project cohort, and lands in that trust's
:doc:`XNAT <component-xnat>` instance.

This page describes how that retrieval works, what a trust's PACS and network teams need to
configure, and how to verify the connection.

DICOM Networking in Brief
=========================

Three ideas are enough to follow the rest of this section.

**AE Title.** A DICOM system's *name* on the network — like a hostname, but specific to DICOM, and
at most 16 characters. When one system connects to another it announces "I am *X*, calling *Y*". The
receiver checks that *Y* is its own name and that *X* is one it has been told to accept. Names are
separate from addresses: the IP and port are configured alongside the AE title, not derived from it.

**SCU and SCP.** Client and server. An SCU (Service Class *User*) opens connections; an SCP (Service
Class *Provider*) listens for them. XNAT is both, at different moments — an SCU when it queries the
PACS, an SCP when it receives the images.

**The four operations**, in the order FLIP uses them:

.. list-table::
   :widths: 15 55 30
   :header-rows: 1

   * - Operation
     - What it does
     - Direction
   * - ``C-ECHO``
     - A DICOM ping. Confirms two systems can reach and accept each other
     - either way, for testing
   * - ``C-FIND``
     - Search — "which study has accession number ABC123?"
     - XNAT to PACS
   * - ``C-MOVE``
     - "Send that study to the system called ``FLIPXNAT``"
     - XNAT to PACS
   * - ``C-STORE``
     - The image transfer itself
     - **PACS to XNAT**

.. important::

   C-MOVE does not return the images on the connection that asked for them. XNAT names a
   destination, the PACS looks that name up in its *own* table to find an address, and opens a
   **new connection in the opposite direction** to deliver the study.

   Three consequences follow, and each has caused a failed integration in practice:

   * The destination must be registered on the PACS in advance — a name it does not know cannot be
     delivered to.
   * The AE title and port XNAT advertises must match that registration exactly. XNAT rejects an
     association addressed to a different name, and the DQR plugin refuses to issue a C-MOVE whose
     destination does not correspond to one of its own configured receivers.
   * The return connection needs its own firewall rule. Every other FLIP connection is outbound, so
     this is the one reviewers overlook.

How Retrieval Works
===================

FLIP **pulls**; the PACS is never configured to push into XNAT. XNAT's
`DICOM Query-Retrieve (DQR) plugin <https://wiki.xnat.org/xnat-tools/dicom-query-retrieve-plugin>`_
performs the retrieval, driven over REST by the imaging API:

1. The cohort query is re-run against the trust's OMOP database and returns **accession numbers
   only**.
2. For each accession number, XNAT issues a **C-FIND** to the PACS at STUDY level, matching on
   Accession Number ``(0008,0050)``, to resolve the Study Instance UID.
3. XNAT issues a **C-MOVE** to the PACS, naming itself as the move destination.
4. The PACS **C-STOREs** the study back to XNAT's DICOM SCP receiver, where the site-wide
   anonymisation script runs on receipt, before the session is archived — see
   :ref:`DICOM Anonymization <dicom-anonymization>`.

Only accession numbers belonging to an approved project cohort are ever requested. There is no
standing forward rule and no bulk transfer.

.. important::

   Step 4 is a connection **from the PACS to XNAT**. It is easy to overlook when specifying firewall
   rules, because every other FLIP connection is outbound. Without it, queries succeed and
   retrievals silently time out.

What the PACS Team Must Register
================================

The FLIP XNAT instance must be registered on the PACS as a DICOM node, permitted to:

* issue **C-FIND** (Study Root query/retrieve) as an SCU;
* act as a **C-MOVE destination**, so retrieved studies can be returned to it;
* issue and answer **C-ECHO**, for verification in both directions.

The trust must supply, and FLIP must be configured with, the PACS AE title, host and query/retrieve
port. FLIP in turn supplies its own AE title, host and DICOM port.

.. note::

   The AE title FLIP presents is one of many configured on a trust PACS, so it should identify the
   platform — for example ``FLIPXNAT``. It must match on both sides: the PACS opens the C-STORE
   association using the AE title it has registered, and XNAT's SCP receiver rejects an association
   addressed to a different AE title.

Configuring FLIP
================

These are FLIP's own settings, applied by the operator deploying the trust node — in the trust's kit
file for a Compose deployment, or in the Helm values for Kubernetes. They are not something the
trust's PACS team supplies; what they supply is covered above.

.. important::

   The defaults below describe the **mocked PACS that ships with FLIP for development**, not values
   a trust should use. Every one of them is replaced with the real details when connecting to a
   trust PACS.

.. list-table::
   :widths: 30 45 25
   :header-rows: 1

   * - Setting
     - Description
     - Development default
   * - ``XNAT_AETITLE``
     - XNAT's own AE title, used for the DICOM SCP receiver, the DQR calling AE, and the C-MOVE
       destination
     - ``XNAT``
   * - ``XNAT_PORT``
     - DICOM SCP receiver port
     - ``8104``
   * - ``PACS_AETITLE``
     - AE title of the trust PACS
     - ``ORTHANC``
   * - ``PACS_HOST``
     - Hostname or IP of the trust PACS
     - ``orthanc``
   * - ``PACS_QR_PORT``
     - Query/retrieve port on the trust PACS. Must be reachable *from the XNAT container* — this is
       not a host-published port
     - ``4242``
   * - ``XNAT_WEB_PORT``
     - Host-published port for XNAT's web UI and REST API. Unrelated to DICOM; separate from
       ``XNAT_PORT`` so the DICOM receiver can be published independently. Defaults to ``XNAT_PORT``
     - ``XNAT_PORT``
   * - ``PACS_AVAILABILITY_DAYS`` / ``_START`` / ``_END``
     - When retrieval may run, as a comma-separated day list and a daily window
     - all week, ``00:00``–``24:00``
   * - ``PACS_THREADS`` / ``PACS_UTILIZATION_PERCENT``
     - How hard to drive the PACS during that window
     - ``1`` / ``100``
   * - ``DQR_MAX_PACS_REQUEST_ATTEMPTS`` / ``DQR_RETRY_WAIT_SECONDS``
     - How many times, and how far apart, to retry a study the PACS did not deliver
     - ``100`` / ``300``

The mocked PACS those defaults describe is covered below.

Development: the Mocked PACS
============================

FLIP ships an `Orthanc <https://orthanc.uclouvain.be/>`_ DICOM server that stands in for the trust
PACS during development and testing. It is seeded with synthetic DICOM studies whose accession
numbers match the mocked OMOP database, so a cohort query resolves to real studies and the full
retrieval path can be exercised without a hospital PACS.

Orthanc is a genuine DICOM node, so it answers C-FIND and C-MOVE exactly as a production PACS would
and returns studies by C-STORE. The retrieval path under test is therefore the same one used against
a trust PACS — only the peer differs. It is why the configuration defaults above name ``orthanc``:

* ``PACS_HOST=orthanc`` — the container name on the FLIP network
* ``PACS_AETITLE=ORTHANC`` — Orthanc's AE title
* ``PACS_QR_PORT=4242`` — Orthanc's DICOM port

Because both sit on the same container network, Orthanc reaches XNAT's SCP receiver directly and no
host port needs publishing. A real PACS is outside that network, which is the one material
difference between the two setups and the reason the receiver must be explicitly exposed.

.. note::

   The mocked PACS is for development and testing only. In a trust deployment the imaging comes from
   the trust's own PACS, and Orthanc is either absent or confined to the FLIP node — its DICOM port
   is deliberately not published to the wider network.

.. warning::

   The DICOM port must be **the same number everywhere** — the port XNAT binds, the port recorded on
   its SCP receiver, the port advertised as the C-MOVE destination, and the port the PACS connects
   to. DQR matches the C-MOVE destination against a registered SCP receiver by exact AE title and
   port, so any translation between these layers causes retrieval to fail.

Example: Sectra PACS
====================

The values below illustrate the shape of the exchange with a Sectra PACS. **They are examples, not
defaults** — each trust supplies its own.

Provided by the trust's PACS team:

.. code-block:: text

   Query/Retrieve node (PACS)
     AE Title:  QR_SCP_EXAMPLE
     IP:        10.0.0.10
     Port:      8059

Provided by FLIP, to be registered on the PACS as a destination:

.. code-block:: text

   Destination node (FLIP XNAT SCP receiver)
     AE Title:  FLIPXNAT
     IP:        10.0.0.20
     Port:      8104

Firewall rules required, in both directions:

.. list-table::
   :widths: 40 20 40
   :header-rows: 1

   * - Connection
     - Direction
     - Purpose
   * - XNAT host → PACS query/retrieve port
     - Outbound
     - C-ECHO, C-FIND, C-MOVE requests
   * - PACS → XNAT host DICOM port
     - **Inbound**
     - C-STORE of the retrieved studies

.. important::

   Where the PACS is vendor-managed, opening the trust's own firewall may not be sufficient. The
   vendor may operate a separate firewall that must also whitelist the connection, raised through
   the trust's service desk as a request to the PACS supplier.

Scheduling and Throughput
=========================

A production PACS may limit how much can be retrieved before it refuses further connections, and
bulk retrieval competes with clinical use. Agree a retrieval schedule with the trust's PACS manager,
then configure XNAT to match: the DQR settings control retry behaviour, and each registered PACS
carries an availability schedule with a per-day window, a thread count and a utilisation percentage.

Where a trust has a test or pre-production PACS, connecting FLIP to that first is recommended, and
is usually raised as a separate service request.

.. note::

   The availability schedule is applied when the PACS is first registered. The DQR plugin pre-creates
   the intervals, and rejects a later write to a day that already has one, so changing the window on
   an already-configured instance requires deleting the existing intervals through XNAT's
   administration UI first. The values above therefore take effect on a fresh deployment; on a
   running one, check what is actually configured rather than assuming the setting was applied.

Verification
============

Work outwards from the network layer:

1. **C-ECHO in both directions** — from the PACS to the FLIP XNAT AE title and port, and from XNAT
   to the PACS. This confirms both firewall directions before any DICOM data moves.
2. **Ping the PACS from XNAT** — ``GET /xapi/pacs/{id}/status`` should report the PACS as reachable
   and enabled.
3. **Query a single accession number** — ``POST /xapi/dqr/query/studies`` should return the matching
   study.
4. **Import a single study**, and confirm it archives into the expected project with anonymisation
   applied.
5. **Run a full project import**, monitoring the import status counts.

.. note::

   XNAT must be restarted for changes to its DICOM configuration to take effect. XNAT also holds the
   DICOM port while running, so command-line testing with a tool such as ``storescp`` on the same
   port requires stopping XNAT first.
