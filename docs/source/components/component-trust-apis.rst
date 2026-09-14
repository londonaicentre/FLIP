.. _flip-trust-apis:

##########
Trust APIs
##########

Three FastAPI services make up FLIP's footprint inside a Trust's Secure Enclave: the ``trust-api``, which is
the Trust's only link to the Central Hub; the ``imaging-api``, which drives that Trust's XNAT; and the
``data-access-api``, which runs cohort queries against that Trust's OMOP database. They run as containers next
to XNAT, the OMOP database, the FL clients and the logging stack — under Docker Compose on a Trust host, or
from the Kubernetes Helm chart.

.. contents:: On this page
   :local:
   :depth: 2

**************
The Trust node
**************

Nothing dials into a Trust. The ``trust-api`` fetches work from the hub over outbound HTTPS, and every other
call stays on the Trust's private container network:

.. code-block:: text

   Central Hub (flip-api)
        ^  outbound HTTPS, TRUST_API_KEY
        |
   trust-api  ---- TRUST_INTERNAL_SERVICE_KEY ---->  imaging-api  ---- REST ---->  XNAT  <-- DICOM -- PACS
        |                                                 |                        (web + DB)
        +------- TRUST_INTERNAL_SERVICE_KEY ---->  data-access-api  <---------------+
                                                          |
                                                    omop-db (read-only role)

   fl-client-net-N ---- TRUST_INTERNAL_SERVICE_KEY ---->  imaging-api / data-access-api
                        (no hub URL, no hub credential)

Two keys, two boundaries. The **Trust API key** authenticates the Trust to the hub and lives only in that
Trust's kit file and, hashed, on the hub. The **Trust-internal service key** authenticates every call inside
the Trust — from ``trust-api``, ``imaging-api`` and the FL clients to ``imaging-api`` and ``data-access-api`` —
and never leaves the Trust. Receivers compare it in constant time and only ``/health`` is exempt; the threat
model is on :ref:`trust-internal-service-authentication`. All of a Trust's configuration comes from one kit
file, ``trust/.env.<CODE>.<env>``, written when the Trust is registered.

*********
Trust API
*********

The ``trust-api`` exposes a single route, ``GET /health``. Everything else it does is outbound, from two
background loops started with the application; if either dies, ``/health`` stops reporting healthy.

Task poller
===========

Every ``POLL_INTERVAL_SECONDS`` (default 5) the poller:

1. sends a heartbeat to the hub, carrying the latest health snapshot (below) when there is one;
2. fetches the Trust's pending tasks;
3. decrypts each payload, runs the handler for its task type and posts the result back, retrying the
   report with exponential backoff.

On the first exchange it compares the identity the hub reports for its key with the ``EXPECTED_TRUST_ID`` in
its kit file and exits if they disagree, so a mis-copied kit cannot quietly act as another Trust.

.. list-table::
   :header-rows: 1
   :widths: 24 38 38

   * - Task type
     - What it does
     - Calls
   * - ``cohort_query``
     - Runs the project's cohort query and posts the aggregate statistics to the hub.
     - ``data-access-api`` ``POST /cohort``; hub ``POST /cohort/results``
   * - ``create_imaging``
     - Creates the project's XNAT project and starts retrieving its studies.
     - ``imaging-api`` ``POST /projects/create-project-from-central-hub-project``
   * - ``delete_imaging``
     - Removes the project's XNAT project.
     - ``imaging-api`` ``DELETE /projects/{id}``
   * - ``get_imaging_status``
     - Reports how many studies are queued, imported and failed.
     - ``imaging-api`` ``GET /retrieval/import_status_count/{id}``
   * - ``reimport_studies``
     - Retries the studies the PACS did not deliver.
     - ``imaging-api`` ``PUT /retrieval/reimport_imaging_project_studies/{id}``
   * - ``update_user_profile``
     - Propagates a hub user's profile change to their XNAT account.
     - ``imaging-api`` ``PUT /users``

Health collector
================

Every 30 seconds the collector probes the Trust's own services concurrently — ``imaging-api`` and
``data-access-api`` over ``/health``, XNAT anonymously, the PACS transitively through ``imaging-api``'s DICOM
ping, and the OMOP database with a plain TCP connect (the ``trust-api`` carries no database driver) — and
grades each ``healthy``, ``degraded`` (slow), ``down`` or ``unknown``. The snapshot rides on the next
heartbeat and is what the hub's connection-status drawer shows. A probe cycle that fails or hangs discards
the snapshot rather than resend a stale one, so the hub shows *no data* instead of a frozen green.

***********
Imaging API
***********

The ``imaging-api`` is the only FLIP service that speaks to XNAT. Every router except ``/health`` requires
the Trust-internal service key.

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Router
     - Purpose
   * - ``/projects``
     - Create, list, inspect and delete XNAT projects; the hub-driven create is
       ``POST /projects/create-project-from-central-hub-project``.
   * - ``/retrieval``
     - Import status counts and study re-import for a project.
   * - ``/imaging``
     - Ping the PACS, query it by accession number, queue an import.
   * - ``/download``
     - Fetch a study's resources out of XNAT for FL training (``POST /download/images/{net_id}``).
   * - ``/upload``
     - Write result files back into an XNAT experiment (``PUT /upload/images/{net_id}``).
   * - ``/users``
     - Create and update XNAT users and add them to projects.

Creating a project from the hub
===============================

The ``create_imaging`` task runs one sequence: create the XNAT project; set its prearchive rules; subscribe it
to the Container Service event that converts each new DICOM session to NIfTI with the pinned ``dcm2niix``
image when the project has DICOM-to-NIfTI conversion on; create or add the hub project's members as XNAT
users, emailing credentials to new ones; then start retrieval in the background. Retrieval asks the
``data-access-api`` for the cohort's **accession numbers only** and hands them to XNAT's DICOM query/retrieve
plugin, which does the DICOM work against the Trust's PACS — the ``imaging-api`` never opens a DICOM
association itself. Where studies end up, and how anonymisation and conversion run, is on
:doc:`component-xnat`; the PACS side of the retrieval is on :doc:`component-pacs`.

Download cache
==============

The first download of an accession's resources for a net pulls the study out of XNAT onto the Trust host
under that net's slice of the images directory; the ``imaging-api`` records completion with a sentinel file
per (assessor type, resource type) and serves later requests — the same fetch loop in the next FL round, the
next job on Flower — from disk. Training code should therefore call ``flip.get_by_accession_number`` every
round without its own caching; ``force_refresh=true`` re-downloads after the content changed in XNAT, and
uploads through ``flip.add_resource`` invalidate the entry automatically. Each FL client mounts only its own
net's slice, so a job cannot read another net's cached studies.

***************
Data Access API
***************

The ``data-access-api`` runs the project's cohort query against the Trust's OMOP database, three ways:

- ``POST /cohort`` — the aggregate statistics the researcher sees while refining the query;
- ``POST /cohort/dataframe`` — the row-level table an FL client trains on (``flip.get_dataframe``);
- ``POST /cohort/accession-ids`` — the accession numbers whose imaging is pulled into XNAT.

Query validation
================

This service is the **authoritative** validator of cohort SQL: the hub's own check is fast feedback for the
researcher, not a security control, and a Trust stays safe whatever the hub did. The query is parsed once,
validated on the syntax tree and **re-emitted** from that tree before it reaches the database. A query is
accepted only if it:

1. is shorter than the configured maximum length;
2. parses as exactly one non-empty statement;
3. is a ``SELECT`` at the top level (no ``COPY``, ``EXPLAIN`` or DDL/DML statements);
4. contains no ``INSERT``, ``UPDATE``, ``DELETE`` or ``MERGE`` anywhere in the tree — a writable CTE parses
   as a ``SELECT``, so the top-level check alone would miss it;
5. references only the ``omop`` schema — unqualified tables are rewritten to ``omop.<table>`` rather than
   left to the search path, and set-returning functions outside a short allowlist are rejected;
6. gives every ``LIMIT`` and ``OFFSET`` as a literal integer, which closes the blind-extraction route.

No layer uses a keyword denylist: the connection runs as the read-only ``data_analyst_reader`` role, so a
statement the parser let through still cannot write. On Kubernetes that role currently carries
``pg_read_all_data`` (wider than the Compose grant of ``SELECT`` on ``omop`` alone), so rule 5 is the only
schema barrier there; narrowing it is tracked in
`FLIP#904 <https://github.com/londonaicentre/FLIP/issues/904>`__. The full rationale is in the
`data-access-api README <https://github.com/londonaicentre/FLIP/blob/develop/trust/data-access-api/README.md#cohort-query-validation>`_
and on :ref:`security`.

Disclosure threshold
====================

``COHORT_QUERY_THRESHOLD`` (default 10) is the Trust's own disclosure floor, set in its kit file. Aggregate
statistics fold small categories below it, and both row-level routes refuse outright when the live cohort is
smaller than it — with one fixed message, so a below-threshold cohort is indistinguishable from an empty one.
The hub can pass a threshold, but only a higher one: the Trust's value is a floor the hub cannot lower.
Because FLIP stores a cohort only as its SQL and re-runs it at every stage, a project that imported cleanly
can start refusing later if the data changes.

Query cache
===========

Statistics are cached in-process, keyed by a hash of the normalised query and its parameters, for up to 60
days and at most 64 entries; results above 50 000 rows are never cached. The cache exists so that refining a
query in the UI does not re-run the previous version against OMOP each time. The schema that queries run
against is on :doc:`component-omop-database`.

*************
Observability
*************

All three services write single-line JSON logs with a per-request id, collected by Grafana Alloy into Loki
and viewed in Grafana inside the Trust — see :doc:`component-logging-stack`. Their Swagger UIs are enabled
outside production and reachable only from the Trust host (or through SSM port forwarding for the AWS-hosted
mock Trusts).

***************
Further reading
***************

- `trust/README.md <https://github.com/londonaicentre/FLIP/blob/develop/trust/README.md>`_ — bringing a Trust
  up, the kit file, standalone operation
- `trust-api <https://github.com/londonaicentre/FLIP/blob/develop/trust/trust-api/README.md>`_,
  `imaging-api <https://github.com/londonaicentre/FLIP/blob/develop/trust/imaging-api/README.md>`_ and
  `data-access-api <https://github.com/londonaicentre/FLIP/blob/develop/trust/data-access-api/README.md>`_
  READMEs — deployment, configuration and troubleshooting per service
- Module reference: :doc:`API reference </api-reference>`
