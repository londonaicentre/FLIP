.. _flip-trust-apis:

##########
Trust APIs
##########

Three FastAPI services make up FLIP's footprint inside a Trust's Secure Enclave:

- ``trust-api`` — the Trust's only link to the Central Hub;
- ``imaging-api`` — drives the Trust's XNAT;
- ``data-access-api`` — runs cohort queries against the Trust's OMOP database.

They run as containers alongside XNAT, the OMOP database, the FL clients and the logging stack, either under
Docker Compose on a Trust host or from the Kubernetes Helm chart. This page is for engineers and Trust operators
who need to know what each service does, what it talks to and which privacy controls it enforces.

.. contents:: On this page
   :local:
   :depth: 2

**************
The Trust node
**************

**Nothing connects into a Trust.** The ``trust-api`` fetches work from the hub over outbound HTTPS, and every
other call stays on the Trust's private container network:

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

All of a Trust's configuration comes from one kit file, ``trust/.env.<CODE>.<env>``, written when the Trust is
registered.

.. _trust-authentication:

Trust authentication
====================

The hub identifies a Trust by its **API key**, not by IP address or hostname: any host whose kit file holds a
Trust's credentials can act as that Trust. Two keys protect two boundaries:

- The **Trust API key** authenticates the Trust to the hub. It lives only in the Trust's kit file, and as a hash
  on the hub.
- The **Trust-internal service key** authenticates every call inside the Trust, to ``imaging-api`` and
  ``data-access-api``. It never leaves the Trust. Only ``/health`` endpoints are exempt. The threat model is on
  :ref:`trust-internal-service-authentication`.

The kit file carries these settings for it:

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Setting
     - Purpose
   * - ``TRUST_API_KEY``
     - The Trust API key, sent on every call to the hub.
   * - ``TRUST_API_KEY_HEADER``
     - The header it is sent in. It must match the hub's; the platform default is ``Authorization``.
   * - ``CENTRAL_HUB_API_URL``
     - The public hub URL the Trust polls, e.g. ``https://app.flip.aicentre.co.uk``.
   * - ``AES_KEY_BASE64``
     - The key shared with the hub for task payloads (AES-256-GCM). It must match the hub's key exactly; a mismatch
       fails every task with ``failed authentication``. Payloads in the older CBC format are not accepted, so a
       hub and its Trusts move across that change together.
   * - ``TRUST_INTERNAL_SERVICE_KEY``
     - The Trust-internal service key.
   * - ``EXPECTED_TRUST_ID`` (optional)
     - A self-check: if the hub resolves this host's API key to a different Trust, trust-api stops instead of acting
       as the wrong one (see `Task poller`_).

A Trust must be **registered on the hub** before it can connect, by step 2 of :doc:`/deploy-flip/onboarding-a-trust`
or through the Add-Trust admin flow. Registration writes the API key and internal service key into the kit file. No
hub redeploy is needed: the hub reads its Trust list live on every request.

*********
Trust API
*********

The ``trust-api`` exposes a single route, ``GET /health``. Everything else it does is outbound, from two
background loops. If either loop dies, ``/health`` reports ``degraded`` and names it. ``/health`` also shows
what the hub reported on the last heartbeat: its release, and whether this Trust's ``AES_KEY_BASE64`` matches the
hub's.

Task poller
===========

Every ``POLL_INTERVAL_SECONDS`` (default 5) the poller:

1. sends a heartbeat to the hub, with the latest health snapshot (below) when there is one;
2. fetches the Trust's pending tasks;
3. decrypts each task, runs it and posts the result back, one task at a time. Posting a result is retried with
   backoff if the hub does not accept it.

The hub only ever answers this poll: a Trust never pushes progress on its own, and the hub never connects in.

On its first exchange with the hub, the poller checks that the hub's view of its identity matches
``EXPECTED_TRUST_ID`` in the kit file, and **exits if they differ**. A mis-copied kit file therefore cannot act
as another Trust.

.. list-table::
   :header-rows: 1
   :widths: 24 38 38

   * - Task type
     - What it does
     - Calls
   * - ``cohort_query``
     - Runs the project's cohort query and posts the aggregate statistics to the hub.
     - ``data-access-api`` ``POST /cohort``; hub ``POST /cohort/results``
   * - ``persist_cohort``
     - At approval, records the approved cohort's membership on this Trust (see
       `Approved cohort membership`_). Queued before ``create_imaging``.
     - ``data-access-api`` ``POST /cohort/snapshot``
   * - ``create_imaging``
     - Creates the project's XNAT project and starts retrieving its studies.
     - ``imaging-api`` ``POST /projects/create-project-from-central-hub-project``
   * - ``delete_imaging``
     - Removes the project's XNAT project.
     - ``imaging-api`` ``DELETE /projects/{id}``
   * - ``get_imaging_status``
     - Reports how many of the project's studies are queued, processing, imported and failed. The hub queues it
       whenever the project page asks for the imaging status.
     - ``imaging-api`` ``GET /retrieval/import_status_count/{id}``
   * - ``reimport_studies``
     - Retries the studies the PACS did not deliver.
     - ``imaging-api`` ``PUT /retrieval/reimport_imaging_project_studies/{id}``
   * - ``update_user_profile``
     - Propagates a hub user's profile change to their XNAT account.
     - ``imaging-api`` ``PUT /users``

Health collector
================

Every 30 seconds the collector checks the Trust's own services and grades each one ``healthy``, ``degraded``
(slow), ``down`` or ``unknown``:

- ``imaging-api`` and ``data-access-api``, through their health endpoints;
- XNAT;
- the PACS, through ``imaging-api``'s DICOM ping;
- the OMOP database, with a plain network connection check.

The snapshot travels with the next heartbeat and is what the hub's connection-status drawer shows. If a check
cycle fails or hangs, the snapshot is dropped rather than resent, so the hub shows *no data* instead of a stale
green. The PACS ping opens a connection to the hospital PACS, so it runs **at most every five minutes**; the
cycles in between reuse its last result.

***********
Imaging API
***********

The ``imaging-api`` is the only FLIP service that reads or changes anything in XNAT; ``trust-api`` only checks
that XNAT is up. Every route except ``/health`` requires the Trust-internal service key.

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Router
     - Purpose
   * - ``/projects``
     - Create, list, inspect and delete XNAT projects; the hub-driven create is
       ``POST /projects/create-project-from-central-hub-project``.
   * - ``/retrieval``
     - Import status counts and study re-import for a project. Counts come from XNAT's REST API;
       ``imaging-api`` has no access to XNAT's database.
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

The ``create_imaging`` task runs these steps:

1. Create the XNAT project and set its prearchive rules.
2. Subscribe the project to the XNAT event that converts new DICOM scans to NIfTI. The subscription is active
   only when the project has DICOM-to-NIfTI conversion on, and it fires on new scans only; scans already archived
   are converted with ``make -C trust reconvert-nifti`` (see :doc:`component-xnat`).
3. Create or add the hub project's members as XNAT users. The hub then emails each new user a link to set
   their XNAT password, and tells existing users they have been added.
4. Start retrieval in the background. ``imaging-api`` asks ``data-access-api`` for the cohort's **accession
   numbers only** and hands them to XNAT, which retrieves the studies from the Trust's PACS. ``imaging-api``
   never talks DICOM to the PACS itself.

.. seealso::

   Where studies end up, and how anonymisation and conversion run: :doc:`component-xnat`. The PACS side of
   retrieval: :doc:`component-pacs`.

Download cache
==============

The first time an FL client asks for an accession's resources, ``imaging-api`` pulls the study out of XNAT onto
the Trust host, under that net's own images directory. Later requests are served from disk: the next FL round
and, on Flower, the next job.

What this means for training code:

- Call ``flip.get_by_accession_number`` every round; **do not add your own caching**. Each call checks the
  study's file counts and sizes in XNAT and downloads it again if they have changed.
- Uploads through ``flip.add_resource`` refresh the cached copy automatically.
- Each FL client sees only its own net's directory, so a job cannot read another net's cached studies.

***************
Data Access API
***************

The ``data-access-api`` runs the project's cohort query against the Trust's OMOP database in three ways:

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Endpoint
     - Returns
   * - ``POST /cohort``
     - Aggregate statistics the researcher sees while refining the query.
   * - ``POST /cohort/dataframe``
     - The row-level table an FL client trains on (``flip.get_dataframe``).
   * - ``POST /cohort/accession-ids``
     - The accession numbers whose imaging is pulled into XNAT.

Query validation
================

This service is the **authoritative** check on cohort SQL. The hub's own check only gives the researcher fast
feedback; it is not a security control, and a Trust stays safe whatever the hub accepted. The query is parsed
once, checked, and **rebuilt from the checked version** before it reaches the database. A query is accepted only
if it:

1. is shorter than the configured maximum length;
2. is exactly one non-empty statement;
3. is a ``SELECT`` (no ``COPY``, ``EXPLAIN``, or statements that change data or schema);
4. contains no ``INSERT``, ``UPDATE``, ``DELETE`` or ``MERGE`` anywhere, including inside a ``WITH`` clause;
5. reads only the ``omop`` schema — tables without a schema are pointed at ``omop``, and only a short list of
   table-returning functions is allowed;
6. uses only literal integers for ``LIMIT`` and ``OFFSET``, which blocks one way of extracting data a little at
   a time.

There is **no keyword blocklist**. Instead, queries run as a **read-only database role** whose access is limited
to the ``omop`` schema, under both Compose and Kubernetes, so a statement that got past the checks still cannot
write. Rule 5 is enforced separately, on top of the role's limits.

.. seealso::

   The full rationale is in the :gh:`data-access-api README <trust/data-access-api/README.md#cohort-query-validation>`
   and on :ref:`security`.

Disclosure threshold
====================

``COHORT_QUERY_THRESHOLD`` (default 10), set in the kit file, is the Trust's own **disclosure floor**:

- Aggregate statistics are suppressed when the cohort has fewer distinct patients than the threshold, and
  smaller categories are folded into *Other*.
- Both row-level endpoints refuse a cohort with fewer distinct patients than the threshold. They use one fixed
  message, so a below-threshold cohort looks the same as an empty one.
- The hub cannot change it. A Trust's optional **governance document** can raise it, never lower it, and can
  also deny these routes for chosen projects; a denial looks the same as a below-threshold cohort. See the
  :gh:`Trust README <trust/README.md#trust-governance-policy-optional>`.

The threshold is checked at approval and again on every request. A cohort that shrinks below it stops being
served, and raising it affects projects that are already approved.

Approved cohort membership
--------------------------

The row-level endpoints serve only the cohort **as it stood at project approval**. At approval, each Trust runs
the approved query once (the ``persist_cohort`` task) and records which patients and studies it returned; it
stores identifiers only, not their data. Recording a cohort needs proof of the hub's AES key as well as the
Trust-internal key, so FL training code, which has no AES key, cannot change it. After that:

- ``/cohort/dataframe`` re-runs the approved query and keeps only the approved patients and studies.
- ``/cohort/accession-ids`` serves the approved studies that are still in OMOP.
- Both ignore any SQL the caller sends, and refuse a project with no recorded membership.

So a cohort can **shrink but never grow**. A patient removed from OMOP, for example after an opt-out, drops out
on the next request; no new patient or study can join. Only the membership is fixed, not the records: a new
measurement, visit or condition added later for an approved patient is served. The national data opt-out works
per patient, so it acts on membership. The Trust keeps no copy of the records outside OMOP.

Query cache
===========

Statistics queries are cached in memory, so that refining a query in the UI does not re-run earlier versions
against OMOP each time. By default an entry lasts up to 60 days, the cache holds at most 64 entries, and results
over 50,000 rows are never cached. The cache is per container and is lost on restart.

The cache is never invalidated by changes in OMOP: re-running the same statistics query within 60 days can
return the earlier result. The approval-time snapshot and the row-level endpoints always bypass it and read live
OMOP. The schema that queries run against is on :doc:`component-omop-database`.

*************
Observability
*************

All three services write single-line JSON logs with a per-request id. Grafana Alloy collects them into Loki,
and you view them in Grafana inside the Trust; see :doc:`component-logging-stack`. Each service's Swagger UI
(``/docs``) is enabled only outside production.

***************
Further reading
***************

- :gh:`trust/README.md` — bringing a Trust
  up, the kit file, standalone operation
- :gh:`trust-api <trust/trust-api/README.md>`,
  :gh:`imaging-api <trust/imaging-api/README.md>` and
  :gh:`data-access-api <trust/data-access-api/README.md>`
  READMEs — deployment, configuration and troubleshooting per service
- Module reference: :doc:`API reference </api-reference>`
