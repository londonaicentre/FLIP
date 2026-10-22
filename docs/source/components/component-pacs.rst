.. _flip-pacs:

####
PACS
####

The Picture Archiving and Communication System (PACS) is the clinical system that stores a trust's imaging. FLIP
holds no copy of it. Imaging is retrieved from the trust's own PACS on demand, only for studies in an approved
project's cohort, and lands in that trust's :doc:`XNAT <component-xnat>`.

This page is for the operator deploying a trust node and for the trust's PACS and network teams. It covers how
retrieval works, what each side configures, and how to check the connection.

.. contents:: On this page
   :local:
   :depth: 1

DICOM networking in brief
=========================

Three ideas are enough to follow the rest of the page.

**AE title.** A DICOM system's name on the network, at most 16 characters. A connecting system announces "I am *X*,
calling *Y*". The receiver checks that *Y* is its own name and that *X* is one it accepts. The IP and port are
configured alongside the AE title, not derived from it.

**SCU and SCP.** Client and server. An SCU (Service Class *User*) opens connections; an SCP (Service Class
*Provider*) listens for them. XNAT is an SCU when it queries the PACS and an SCP when it receives the images.

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

   **C-MOVE does not return the images on the connection that asked for them.** XNAT names a destination. The PACS
   looks that name up in its *own* table and opens a **new connection in the opposite direction** to deliver the
   study. Each of these consequences has broken a real integration:

   * The destination must be registered on the PACS in advance. It cannot deliver to a name it does not know.
   * The AE title and port XNAT advertises must match that registration exactly. XNAT rejects an association
     addressed to another name, and its query-retrieve plugin will not issue a C-MOVE to a destination that is not
     one of its own receivers.
   * The return connection needs its own firewall rule. Every other FLIP connection is outbound, so this one is easy
     to miss.

How retrieval works
===================

FLIP **pulls**: the PACS is never set up to push into XNAT. XNAT's
`DICOM Query-Retrieve (DQR) plugin <https://wiki.xnat.org/xnat-tools/dicom-query-retrieve-plugin>`_ does the
retrieval, driven over REST by the trust's imaging API when the project's XNAT project is created:

1. The trust's data access API returns the **accession numbers** of the cohort frozen when the trust approved the
   project, and nothing else. Each study is named once, even when the cohort has one row per series.
2. For each accession number, XNAT sends a **C-FIND** to the PACS at STUDY level, matching on Accession Number
   ``(0008,0050)``, to find the Study Instance UID. An accession number that matches no study, or more than one, is
   skipped.
3. The imaging API queues **one import request** for all the studies found. DQR works through it during the PACS's
   availability window, sending a **C-MOVE** that names XNAT itself as the destination.
4. The PACS **C-STOREs** each study back to XNAT's DICOM receiver. XNAT's site-wide anonymisation script runs on
   receipt, and the session goes straight into the project's archive.

Only accession numbers in an approved project's cohort are ever requested. There is no standing forward rule and no
bulk transfer.

.. important::

   Step 4 is a connection **from the PACS to XNAT**. Without a firewall rule for it, queries succeed but retrievals
   time out without an error.

.. warning::

   The anonymisation script is based on the DICOM PS3.15 Basic Profile but does not conform to it, and has only been
   run against synthetic data. Read its limits (nested UIDs, burned-in annotation, faces) in
   :ref:`DICOM Anonymization <dicom-anonymization>` before connecting a real PACS.

What the PACS team must register
================================

The PACS team registers FLIP's XNAT as a DICOM node, allowed to:

* send **C-FIND** (Study Root query/retrieve) as an SCU;
* be a **C-MOVE destination**, so retrieved studies can be returned to it;
* send and answer **C-ECHO**, for testing in both directions.

FLIP supplies its own AE title, host and DICOM port for the registration. In return, ask the trust for the items
below. The first three are needed before anything can be configured; the rest are the questions that most often
turn out to matter.

.. list-table::
   :widths: 32 68
   :header-rows: 1

   * - What to ask for
     - Why it matters
   * - AE title, host/IP and query/retrieve port of the PACS
     - The details XNAT dials. The port must be reachable from the XNAT container
   * - Whether C-MOVE is served by a **different AE** to the query service
     - Some PACS separate the query and retrieve roles; if so, both must be known
   * - Confirmation that STUDY-level matching on Accession Number ``(0008,0050)`` is supported
     - This is the only key FLIP queries by. Without it, nothing resolves
   * - Whether relational queries / extended negotiation are supported
     - Enabled by default; a PACS that does not support it rejects the association, and needs
       ``PACS_SUPPORTS_EXTENDED_NEGOTIATIONS=false``
   * - The transfer syntax studies will be sent in
     - Compressed or transcoded data still has to be readable by XNAT once archived
   * - Any per-connection or per-session limit on how much may be retrieved
     - A production PACS may refuse further associations after a certain volume; see
       `Scheduling and throughput`_
   * - Whether DICOM TLS is required
     - FLIP does not currently support TLS on the DIMSE connection, so this needs to be established
       before committing to a design
   * - Which port they want used for bulk retrieval
     - A port already used for teleradiology may be configured for single studies rather than the
       sustained retrieval FLIP performs

.. tip::

   A trust PACS has many AE titles configured, so choose one that names the platform, for example ``FLIPXNAT``. It
   must be the same on both sides: the PACS opens the C-STORE with the AE title it has registered, and XNAT's
   receiver rejects any other.

.. note::

   **Start the registration early.** The PACS team often cannot add a node on their own. Depending on how the PACS
   is managed, they may need to raise a call with the supplier to enable the connection and issue credentials. This
   usually sets the lead time.

   If FLIP has been connected to this PACS before, ask for old FLIP or XNAT node entries to be removed at the same
   time. A retrieval addressed to a stale entry for a decommissioned host goes nowhere, without an error.

Example: Sectra PACS
====================

These values show the shape of the exchange with a Sectra PACS. **They are examples, not defaults**: each trust
supplies its own.

From the trust's PACS team:

.. code-block:: text

   Query/Retrieve node (PACS)
     AE Title:  QR_SCP_EXAMPLE
     IP:        10.0.0.10
     Port:      8059

From FLIP, to register on the PACS as a destination:

.. code-block:: text

   Destination node (FLIP XNAT SCP receiver)
     AE Title:  FLIPXNAT
     IP:        10.0.0.20
     Port:      8104

Firewall rules, in both directions:

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

   If the PACS is vendor-managed, opening the trust's own firewall may not be enough. The vendor may run a separate
   firewall that must also allow the connection; raise it through the trust's service desk as a request to the PACS
   supplier.

Configuring FLIP
================

These are FLIP's own settings. The operator deploying the trust node sets them: in the trust's kit file for a
Compose deployment, or in the Helm values for Kubernetes. The PACS team supplies the values they feed from (see
above), not the settings themselves.

.. important::

   The defaults below describe the **mocked PACS that ships with FLIP for development**. Replace every one with the
   real details when connecting to a trust PACS.

.. list-table::
   :widths: 25 22 33 20
   :header-rows: 1

   * - Kit setting
     - Helm value
     - Description
     - Development default
   * - ``XNAT_AETITLE``
     - ``xnat.web.dicomAet``
     - XNAT's own AE title, used for the DICOM receiver, the DQR calling AE and the C-MOVE destination
     - ``XNAT``
   * - ``XNAT_PORT``
     - ``xnat.web.dicomPort``
     - DICOM receiver port
     - ``8104``
   * - ``PACS_AETITLE``
     - ``pacs.aeTitle``
     - AE title of the trust PACS
     - ``ORTHANC``
   * - ``PACS_HOST``
     - ``pacs.host``
     - Hostname or IP of the trust PACS
     - ``orthanc``
   * - ``PACS_QR_PORT``
     - ``pacs.qrPort``
     - Query/retrieve port on the trust PACS. Must be reachable *from the XNAT container*; this is not a
       host-published port
     - ``4242``
   * - ``XNAT_WEB_PORT``
     - none (Compose only)
     - Host-published port for XNAT's web UI and REST API. Unrelated to DICOM, but it must differ from
       ``XNAT_PORT`` because the receiver is host-published too. The deploy refuses a collision
     - none; every kit must set it (``8105`` in the kit template)
   * - ``PACS_SUPPORTS_EXTENDED_NEGOTIATIONS``
     - ``pacs.supportsExtendedNegotiations``
     - Whether the PACS supports relational queries / extended negotiation. A capability of the PACS, not a
       preference (see the table above)
     - ``true``
   * - ``PACS_AVAILABILITY_DAYS`` / ``_START`` / ``_END``
     - ``pacs.availability.days`` / ``.start`` / ``.end``
     - When retrieval may run: days as a comma-separated list (``MONDAY`` to ``SUNDAY``, in capitals), and a daily
       window whose start is before its end (see `Scheduling and throughput`_)
     - all week, ``00:00``–``24:00``
   * - ``PACS_THREADS`` / ``PACS_UTILIZATION_PERCENT``
     - ``pacs.availability.threads`` / ``.utilizationPercent``
     - How hard to drive the PACS during that window
     - ``1`` / ``100``
   * - ``DQR_MAX_PACS_REQUEST_ATTEMPTS`` / ``DQR_RETRY_WAIT_SECONDS``
     - ``pacs.dqr.maxRequestAttempts`` / ``.retryWaitSeconds``
     - How many times, and how far apart, to retry a study the PACS did not deliver
     - ``100`` / ``300``

.. seealso::

   :gh:`trust/xnat/README.md` (PACS configuration) for every PACS setting and how a configure run applies them.

Exposing the DICOM receiver
===========================

The PACS must be able to reach XNAT's DICOM receiver. How the receiver is exposed depends on the deployment.

.. warning::

   **Use the same DICOM port number everywhere:** the port XNAT binds, the port on its receiver, the port advertised
   as the C-MOVE destination, and the port the PACS connects to. DQR matches the C-MOVE destination to a receiver by
   exact AE title and port, so any translation between these layers breaks retrieval.

Compose
-------

The receiver is **always published on the host**, next to the web UI, so a development deployment uses the same
wiring as a trust with a real PACS. That is why ``XNAT_WEB_PORT`` and ``XNAT_PORT`` must differ.

The publication binds **every interface** of the host, not just loopback, and a host firewall rule does not close
it: XNAT runs as a Docker Swarm service, and Docker forwards the port before the host firewall sees the traffic.
Restrict the port on the network the host sits on.

- The cloud-hosted mock trusts accept no inbound traffic at all, so the port reaches no further than the instance.
- An on-prem trust host or a developer machine exposes the receiver to whatever network it is on.

.. warning::

   **The receiver accepts an association from any calling AE.** DICOM networking carries no authentication, and FLIP
   registers the receiver with its calling-AE whitelist off and archives what it receives directly. Only the
   surrounding network confines C-STORE to the trust's own PACS. Treat the DICOM port as an unauthenticated write
   path into the archive when writing its firewall rules.

Kubernetes
----------

These Helm values are closed by default (a ``ClusterIP`` Service and empty rule lists):

.. code-block:: yaml

   xnat:
     web:
       # The DICOM receiver has its own Service (xnat-web-dicom). Exposing it never exposes the
       # web console, which stays ClusterIP.
       dicomService:
         type: NodePort
       # A fixed port gives the PACS a stable destination. It must be inside the API server's
       # --service-node-port-range. If you cannot widen that range, use type: LoadBalancer and omit this.
       dicomNodePort: 8104

   networkPolicies:
     # The C-STORE return leg. Scope it to the PACS itself, never the whole trust network.
     allowedIngressCIDRsWithPorts:
       - cidrs: ["10.0.0.10/32"]
         port: 8104
     # The outbound query. Without it, C-FIND never leaves the cluster.
     allowedEgressCIDRsWithPorts:
       - cidrs: ["10.0.0.10/32"]
         port: 8059

When ``pacs.host`` names a real PACS (anything but the mocked ``orthanc``), the chart refuses to render a
configuration that would leave either direction closed or the inbound path too wide:

- ``dicomService.type`` left at ``ClusterIP``, or ``NodePort`` without ``dicomNodePort``;
- no egress rule to the PACS's query/retrieve port;
- no ingress entry on ``xnat.web.dicomPort`` (the failure where queries succeed and retrievals time out);
- an ingress entry for ``0.0.0.0/0``;
- an externally exposed web console (``xnat.web.service.type`` other than ``ClusterIP``).

It also refuses any ingress entry with no ``port``, which Kubernetes reads as *all* ports.

The ingress ``port`` is the pod's container port (``xnat.web.dicomPort``, default 8104), not ``dicomNodePort``: a
NetworkPolicy matches after the node has translated the NodePort back.

.. note::

   Setting ``dicomService.type`` to ``NodePort`` or ``LoadBalancer`` also sets ``externalTrafficPolicy: Local`` on
   that Service. Under the default ``Cluster`` policy, the node rewrites the PACS's source address before the pod
   sees it, so the ingress rule would never match and the C-STORE would be dropped.

   ``Local`` keeps the source address only behind a pass-through load balancer (MetalLB, an AWS NLB). A proxying
   load balancer rewrites it regardless, so scope the rule to the proxy's address instead. ``Local`` also means only
   nodes running the pod answer, which suits the single-pod trust deployment.

Scheduling and throughput
=========================

A production PACS may limit how much can be retrieved before it refuses further connections, and bulk retrieval
competes with clinical use. Agree a retrieval schedule with the trust's PACS manager, then set it in FLIP:

- the DQR retry settings control how failed studies are retried;
- the PACS's availability schedule sets the days, a daily window, a thread count and a utilisation percentage.

If the trust has a test or pre-production PACS, connect FLIP to that first. This is usually a separate service
request.

.. important::

   **The kit sets the schedule.** Every configure run (bringing XNAT up or upgrading it, or ``helm upgrade``) makes
   the registered PACS's schedule match the kit. A change made on XNAT's DQR administration page is reverted, and the
   log names what it replaced. Put the window agreed with the PACS manager in the kit.

.. warning::

   **A window cannot cross midnight.** DQR would run a ``19:00``–``07:00`` window inverted, during the day, so FLIP
   refuses it. For out-of-hours retrieval use ``19:00``–``24:00``, ``00:00``–``07:00`` or whole days.

Verification
============

Work outwards from the network layer:

1. **C-ECHO in both directions**: from the PACS to XNAT's AE title and port, and from XNAT to the PACS. This proves
   both firewall directions before any images move.
2. **Ping the PACS from XNAT**: ``GET /xapi/pacs/{id}/status`` should report the PACS as reachable and enabled.
3. **Query one accession number**: ``POST /xapi/dqr/query/studies`` should return the matching study.
4. **Import one study**, and check it archives into the expected project with anonymisation applied.
5. **Run a full project import**, watching the import status counts.

.. note::

   XNAT holds its DICOM port while it runs. To test from the command line with a tool such as ``storescp`` on the
   same port, stop XNAT first.

Development: the mocked PACS
============================

FLIP ships an `Orthanc <https://orthanc.uclouvain.be/>`_ DICOM server that stands in for the trust PACS in
development and testing. It is seeded with synthetic studies whose accession numbers match the mocked OMOP database,
so a cohort query resolves to real studies and the whole retrieval path can run without a hospital PACS.

Orthanc is a genuine DICOM node: it answers C-FIND and C-MOVE as a production PACS would and returns studies by
C-STORE. Only the peer differs. That is why the development defaults name it:

* ``PACS_HOST=orthanc``: the container name on the FLIP network
* ``PACS_AETITLE=ORTHANC``: Orthanc's AE title
* ``PACS_QR_PORT=4242``: Orthanc's DICOM port

The one material difference is the network. Orthanc shares the container network with XNAT; a real PACS is outside
it. Publishing the receiver on every Compose deployment (see above) keeps the mocked setup on the same wiring.

.. note::

   The mocked PACS is for development and testing only. In a trust deployment the imaging comes from the trust's own
   PACS, and Orthanc is either absent or confined to the FLIP node: its DICOM port is not published to the wider
   network.
