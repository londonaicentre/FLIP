##############
FLIP workflow
##############

This page follows one project from the first cohort query to a trained model. It explains what happens at each stage,
who acts, and what stays inside each trust. The step-by-step instructions for the web interface are in
:doc:`user-guides`.

The same flow covers **evaluating** an existing model, a special case of training with a single round (see
`Training and results`_).

.. list-table::
   :header-rows: 1
   :widths: 22 30 48

   * - Stage
     - Who acts
     - Outcome
   * - `Cohort query`_
     - Researcher
     - The researcher knows how many matching patients each trust holds.
   * - `Project approval`_
     - Researcher, then each trust
     - Approving trusts fix the cohort and pull its images into their own XNAT.
   * - `Image enrichment`_
     - Project members (optional)
     - Images carry the labels or segmentations the model needs.
   * - `File uploads`_
     - Researcher
     - The model's code and configuration are on the hub, checked.
   * - `Job start`_
     - Researcher
     - The code is running at the participating trusts, on each trust's data.
   * - `Training and results`_
     - Automatic
     - A trained model and its metrics are ready to download.

**How to read the diagrams.** Each diagram is a swimlane: one column per person or service, with time running
downwards. The numbers on the arrows match the numbered steps in the text. Services on the left run in the Central
Hub; services on the right run inside each trust.

.. important::

   **Nothing connects into a trust.** A trust's services fetch their work from the Central Hub by polling it, and its
   FL client opens its own connection to the FL server. Patient-level data never leaves the trust; only aggregate
   statistics, model updates, training metrics and status information reach the hub.

************
Cohort query
************

*Who: the project owner*

Before asking any trust to take part, the researcher checks that enough suitable patients exist. They describe the
cohort as an SQL query against the :term:`OMOP` data model, and FLIP runs it at every registered trust.

.. mermaid::
   :caption: Cohort query.

   sequenceDiagram
       autonumber
       actor R as Researcher
       box rgb(248, 234, 242) Central Hub
           participant Hub as Hub API and UI
       end
       box rgb(234, 237, 242) Each trust
           participant TA as trust-api
           participant DA as data-access-api
           participant O as OMOP
       end
       R->>Hub: Create project, write cohort query
       R->>Hub: Run on all trusts
       Hub->>Hub: Check query, queue a task per trust
       TA->>Hub: Poll for tasks
       Hub-->>TA: Cohort query task (encrypted)
       TA->>DA: Run the query
       DA->>O: Validated, read-only SQL
       O-->>DA: Matching rows
       DA-->>TA: Aggregate statistics only
       TA->>Hub: Post results
       Hub-->>R: Cohort size and summary, per trust and combined

**1–2.** The project owner creates the project, adds its members, writes the cohort query and runs it on all
registered trusts.

**3–5.** The hub checks the query, so obvious mistakes are reported at once, and queues one task per trust. Each trust
picks up its task the next time it polls the hub, a few seconds later.

**6–8.** At the trust, ``data-access-api`` checks the query again (a single read-only ``SELECT`` on the OMOP schema)
and runs it against the trust's own OMOP database.

**9–11.** The trust sends back **aggregate statistics only**, never patient rows. If the cohort has fewer patients
than the trust's minimum group size, the trust reports only that it is below the threshold; within the breakdowns,
small groups are merged. The researcher sees the results per trust and combined, and can refine and re-run the query
until the cohort is large enough.

.. seealso::

   :ref:`create-cohort-query`.

****************
Project approval
****************

*Who: the researcher stages the project; each trust's* :term:`Trust Admin` *(or a FLIP admin, if the trust has
none) approves or declines it*

Every trust decides for itself whether its patients' data may be used. Approval is also the moment the cohort is fixed
and, for imaging projects, the images are fetched.

.. mermaid::
   :caption: Project approval and image retrieval at one trust.

   sequenceDiagram
       autonumber
       actor R as Researcher
       actor A as Trust Admin or FLIP admin
       box rgb(248, 234, 242) Central Hub
           participant Hub as Hub API and UI
       end
       box rgb(234, 237, 242) Each trust
           participant TA as trust-api
           participant DA as data-access-api
           participant IA as imaging-api
           participant X as XNAT
           participant P as PACS
       end
       R->>Hub: Stage project at chosen trusts
       A->>Hub: Approve or decline (agreed with the trust's IG team)
       Hub->>Hub: Queue freeze-cohort, then create-imaging task
       TA->>Hub: Poll for tasks
       Hub-->>TA: Both tasks, run in order
       TA->>DA: Freeze cohort membership
       DA-->>TA: Membership stored at the trust
       TA->>Hub: Report frozen cohort
       TA->>IA: Create imaging project
       IA->>X: Create XNAT project, add project members
       TA->>Hub: XNAT project created
       Hub-->>R: XNAT invitation emails to members
       IA->>DA: Frozen cohort's accession numbers
       IA->>X: Find and import these studies
       X->>P: DICOM query / retrieve
       P-->>X: Studies
       R->>Hub: Open the project page
       Hub-->>TA: Import-status task (next poll)
       TA->>IA: Import counts
       TA->>Hub: Import progress
       Hub-->>R: Imaging status per trust

**1.** The researcher chooses the trusts whose data they need and **stages** the project. Only trusts whose query
returned a usable cohort can be chosen. Staging locks the project details and the cohort query.

**2.** Each staged trust decides in its own time. The decision is agreed offline with the trust's information
governance team, then recorded in FLIP, which notes who decided and when.

**3–5.** On approval, the hub queues two tasks for that trust: freeze the cohort, then create the imaging project. The
trust runs them in that order when it next polls.

**6–8.** **The cohort is frozen.** The trust checks its minimum group size again, runs the approved query once and
stores the list of patients and studies it returned, at the trust. From now on, training sees only those patients and
studies: a patient removed from OMOP drops out, but no new patient can join. If the cohort is now below the minimum,
nothing is frozen and no images are fetched.

**9–12.** ``imaging-api`` creates a project in the trust's :term:`XNAT` and adds the project members, creating XNAT
accounts where needed. The hub then emails each member: new users get a link to set their XNAT password, existing
users a notice that they have access.

**13–16.** **The images are fetched.** ``imaging-api`` takes the frozen cohort's accession numbers and asks XNAT to
find and import those studies from the trust's :term:`PACS`. The images stay in that trust's XNAT.

**17–21.** While the project page is open, the hub keeps asking each trust for its import progress. The trust picks up
each request on its next poll and posts back the counts.

How the decisions combine:

- The project is **approved** as soon as one trust approves it. A trust that approves later joins at that point.
- Decisions can change while the project is staged. Once it is approved they are final, and a trust that **declined**
  takes no further part: its data is never imported or trained on.
- If every trust declines, the project stays staged. A FLIP admin can unstage it so the researcher can amend it.

.. note::

   **Tabular-only projects.** A project created with **Includes imaging data** turned off only freezes its cohort
   (steps 6–8): approval creates no XNAT project and pulls nothing from PACS, there is nothing to enrich, and training
   reads the OMOP cohort data alone. Like **Convert DICOMs to NIfTI**, this option is fixed when the project is
   created.

.. seealso::

   :ref:`stage-project`, :ref:`project-approval` and :ref:`imaging-status`.

****************
Image enrichment
****************

*Who: project members (optional)*

Trust PACS hold images, not the labels or segmentations a model may learn from. Enrichment is where project members add
them, inside each trust's XNAT.

**Do you need it?** Only when the model uses data that is **not in OMOP**, such as segmentation masks or other
annotations drawn on the images. Labels that are already in OMOP, such as a lab result or a coded report finding,
reach the model as a column of the cohort query and need no enrichment.

.. mermaid::
   :caption: Image enrichment, inside each trust's XNAT.

   sequenceDiagram
       autonumber
       actor M as Project member
       box rgb(248, 234, 242) Central Hub
           participant Hub as Hub API and UI
       end
       box rgb(234, 237, 242) Each trust
           participant X as XNAT
       end
       Hub-->>M: Import progress per trust (project page)
       M->>X: Sign in to the trust's XNAT
       M->>X: Add segmentations, labels or annotations

**1.** Enrichment can start at a trust once the project page shows its studies imported (and converted to NIfTI, if
the project converts them).

**2.** Project members sign in to that trust's XNAT. New users first set their password through the link FLIP emailed
them at approval; others use their existing XNAT login. Only the project's members are given access to it.

**3.** They add the annotations, by hand in the XNAT web interface or for the whole cohort with the ``flip-xnat``
upload tool.

.. seealso::

   :ref:`data-enrichment` covers both routes.

************
File uploads
************

*Who: researcher*

The model is the code FLIP will run at each trust. The researcher writes it as an FL app and uploads it to the hub.

.. mermaid::
   :caption: File uploads.

   sequenceDiagram
       autonumber
       actor R as Researcher
       box rgb(248, 234, 242) Central Hub
           participant Hub as Hub API and UI
       end
       R->>Hub: Create a model in the project
       R->>Hub: Upload code and config.json
       Hub->>Hub: Check each file in the background
       Hub-->>R: Each file's status on the model page

**1.** The researcher creates a model in the approved project. A project can hold several models.

**2.** They upload the training code, its ``config.json`` and any other files the code needs. The hub refuses file
types it does not accept and files over the size limit before the upload starts.

**3.** The hub checks each file in the background. Model checkpoints are scanned for unsafe content, and a file that
fails is removed. Python files get an advisory code check that never blocks them.

**4.** The model page lists each file with its status and ticks off the files the job type requires. Training can
start once every required file is uploaded and checked.

.. seealso::

   :doc:`working-with-flip-apps` for how to write the code and which files each job type requires, and
   :ref:`upload-model-files`.

*********
Job start
*********

*Who: researcher*

Training starts once the images are in place and the code is uploaded. FLIP sends the same code to each participating
trust, where it runs next to the data.

.. mermaid::
   :caption: Job start.

   sequenceDiagram
       autonumber
       actor R as Researcher
       box rgb(248, 234, 242) Central Hub
           participant Hub as Hub API and UI
           participant S as FL server
       end
       box rgb(234, 237, 242) Each participating trust
           participant C as FL client
           participant DA as data-access-api
           participant IA as imaging-api
           participant X as XNAT
       end
       R->>Hub: Confirm dataset enriched, start training
       Hub->>Hub: Queue until an FL net is free
       Hub->>S: Submit job (uploaded code + FLIP template)
       S->>C: Send the app over the client's own connection
       C->>DA: Cohort data
       DA-->>C: Rows for the frozen cohort
       C->>IA: Images for each accession number
       IA->>X: Download study
       IA-->>C: Local folder with the images
       C->>IA: Derived files (optional)
       IA->>X: Upload to the XNAT project

**1.** On the model page, the researcher switches on **Dataset enriched** (required even when no enrichment was
needed), picks the participating trusts and starts training.

**2.** FLIP queues the job until one of its training networks (*FL nets*) is free.

**3–4.** The hub combines the uploaded code with FLIP's app template and hands it to the FL server of the free net.
Each trust's FL client is already connected out to that server and receives the code over that connection.

**5–6.** The code asks ``data-access-api`` for its data and gets OMOP rows for the frozen cohort, read fresh each time.

**7–9.** For imaging projects, ``imaging-api`` fetches each study from the trust's XNAT into a local folder kept for
that net, and gives the code its path.

**10–11.** The code can also send derived files, such as processed images, back to the XNAT project for later runs.

.. note::

   **On Flower, every trust connected to the net takes part**, not only the ones picked at step 1. NVIDIA FLARE sends
   the job to the picked trusts only.

.. seealso::

   :ref:`initiate-training`.

********************
Training and results
********************

*Who: automatic; the researcher collects the results*

Federated training runs in **rounds**. Each trust trains on its own data, and only the model changes travel to the
hub, where they are combined into one model.

.. mermaid::
   :caption: Training rounds and results.

   sequenceDiagram
       autonumber
       actor R as Researcher
       box rgb(248, 234, 242) Central Hub
           participant Hub as Hub API and UI
           participant S as FL server
       end
       box rgb(234, 237, 242) Each participating trust
           participant C as FL client
       end
       loop Every round
           S->>C: Current global model
           C->>C: Train on local data
           C->>S: Model update and metrics
           S->>Hub: Metrics
           S->>S: Combine the updates
       end
       S->>C: Final model
       C->>S: Test metrics on local data
       S->>Hub: Final model and results
       Hub-->>R: Metrics charts, Download Results

**1–2.** The FL server sends the current model to each trust, which trains it on local data.

**3.** Each trust sends back its **model update** with the metrics its code recorded. On NVIDIA FLARE the update is
always privacy-filtered before it leaves the trust, and a trust can enforce its own filter. On Flower it is filtered
only if the app adds FLIP's privacy mod, as all the tutorials do.

**4–5.** The FL server passes the metrics to the hub, where they appear as charts on the model page while training
runs, and combines the updates into the next global model.

**6–7.** After the last round, each trust tests the final model on its own data.

**8–9.** The FL server saves the final model and results to the hub and marks the model **Results uploaded**, which
frees the net. Project members download them with **Download Results**.

.. seealso::

   :ref:`training-metrics` and :ref:`view-results`.

.. note::

   **Evaluation** is a special case of training: one round, in which each trust runs an uploaded model on its own data
   and returns metrics only. No model update leaves the trust.
