.. _data-enrichment:

################
Data Enrichment
################

.. admonition:: Before you start
   :class: note

   You need to be signed in to FLIP, be a member of the project, and have access to the :term:`XNAT` instance at each
   participating Trust (see :ref:`receiving-xnat-credentials`).

*Data enrichment* is where you add whatever your app needs on top of the images FLIP has pulled into each Trust's XNAT
— segmentation masks, ROI annotations, contours. Trust :term:`PACS` supply images only, so anything else has to be added
here.

Enrichment happens **once per project**, inside XNAT. Both :term:`NVIDIA FLARE` and the :term:`Flower Framework` read
the same enriched data, so the FL backend makes no difference.

.. _enrichment-do-i-need-it:

**************************************
Do you need it? Where labels come from
**************************************

Supervised models need a label for every image, but **most labels do not arrive through enrichment**. There are two
routes; picking the wrong one creates work you never needed.

.. list-table::
   :header-rows: 1
   :widths: 22 44 34

   * - Route
     - Use when
     - How the app reads it
   * - **Cohort query (OMOP)**
     - The label already exists as structured clinical data — a lab result (e.g. a COVID PCR result for a chest X-ray), a diagnosis, an observation, or a coded finding from a radiology report.
     - Select it as a column in the cohort query. It arrives in the dataframe from ``flip.get_dataframe(...)``, one row per accession.
   * - **Data enrichment (XNAT)**
     - The label is not in :term:`OMOP` — typically anything **spatial or image-derived**: segmentation masks, ROI contours, landmarks. Also labels made for the project, such as a re-read or expert correction.
     - Upload it into XNAT beside the image, as this page describes. ``flip.get_by_accession_number(...)`` downloads it with the image.

**Use the cohort query whenever the data allows.** A label in OMOP needs no upload, no per-Trust visit and no re-run
when the cohort changes — it is just another column of SQL.

* The **chest X-ray classification** tutorial uses the cohort query: its ``query.sql`` selects ``Effusion``, ``Edema``
  and ``Lungs in normal arrangement`` as columns. It does **no enrichment**.
* The **spleen segmentation** tutorials use enrichment: a 3D segmentation mask has no place in OMOP, so it is uploaded
  beside the image. If the masks have not been drawn yet, see :ref:`enrichment-draw-labels`.

.. note::

   The platform cannot tell which route your model needs, so it always asks you to confirm this stage is complete
   (see :ref:`confirm-enrichment`). If your labels come from the cohort query, that confirmation is all you need to do.

A project that needs enrichment and skips it pulls its images successfully, then fails at training time with no usable
samples.

.. _enrichment-when:

**********************
When to run it
**********************

Run enrichment only **after both of these have finished** at each Trust:

1. **The image pull.** The cohort's studies must be imported into the Trust's XNAT; until then there is nothing to
   attach files to.
2. **DICOM-to-NIfTI conversion.** FLIP converts each pulled series into a NIfTI volume named
   ``input_<something>.nii.gz``. Enrichment files are named after that volume.

.. important::

   Running too early is the most common mistake, and it fails quietly: the upload reports that it skipped every scan.
   Wait for conversion to finish, then run it again.

.. _enrichment-contract:

*******************************
Where enrichment files must go
*******************************

Your app only sees an enrichment file if it is **in the same scan resource as its image**. For the standard NIfTI
workflow:

- **Resource**: ``NIFTI`` — the resource that holds the converted image. Not a new resource, not a separate assessor.
- **Filename**: the image's filename with the prefix swapped, so ``input_spleen_2.nii.gz`` pairs with
  ``label_spleen_2.nii.gz``.

Apps find labels by this pairing. The spleen tutorials, for example, replace ``/input_`` with ``/label_`` in the image
path. Always take the name from the image that is actually in XNAT rather than assuming it.

.. warning:: Do not upload an enrichment file under the image's own name. It will overwrite the imaging data for that scan.

.. note:: Masks saved from the XNAT viewer (for example with MONAI Label) are the exception: they live in ROI collections, not in the scan's ``NIFTI`` resource, and an app reads them with ``ResourceType.SEGMENTATION``. See :ref:`monai-label-labelling`.

.. _enrichment-manual:

*******************************
Option 1: the XNAT web UI
*******************************

For a handful of scans, or for work done by hand (drawing a segmentation, correcting an annotation), use XNAT directly
at each Trust:

1. Connect to the Trust network and sign in to that Trust's XNAT.
2. Open the project. Its XNAT name differs per Trust; search for the FLIP project ID, which is stored as the XNAT
   project's **secondary ID**.
3. Go to the subject, then the session, then the scan.
4. Open the scan's ``NIFTI`` resource and note the ``input_*.nii.gz`` filename.
5. Upload your file into the same resource with the matching name (``label_*.nii.gz``).
6. Repeat for every scan in the cohort, at **every** participating Trust — each Trust holds only its own studies.

.. _enrichment-scripted:

**********************************
Option 2: the ``flip-xnat`` CLI
**********************************

For a whole cohort, use the ``flip-xnat`` upload tool from the ``flip-utils`` package. It finds the XNAT project from
your FLIP project ID, derives each filename from the image already in XNAT, and never overwrites anything unless you
ask it to.

**Step 1 — Install the tool** on a machine that can reach the Trust network:

.. code-block:: shell

   pip install flip-utils

**Step 2 — Write a manifest**, a CSV pairing each accession with a local file:

.. code-block:: text

   accession_id,file_path
   FAK60462657,labels/label_spleen_2.nii.gz
   FAK76435891,labels/label_spleen_3.nii.gz

Relative paths are resolved from the manifest's own folder. An optional third column, ``target_filename``, overrides
the derived name.

**Step 3 — Provide your XNAT credentials**, as environment variables or as a JSON file (``--credentials-file``):

.. code-block:: shell

   export XNAT_HOST=https://xnat.trust.example
   export XNAT_USER=your-username
   export XNAT_PASS=your-password

.. note::

   These are **your own** XNAT login, not a deployment secret. Use a personal account where you have one, so uploads
   are audited under your name; the ``XNAT_SERVICE_USER`` account belongs to FLIP's imaging service. If you administer
   the Trust and read values from its kit file (``trust/.env.<CODE>.<env>``), ``XNAT_URL`` maps to ``XNAT_HOST``,
   ``XNAT_ADMIN_USER`` to ``XNAT_USER`` and ``XNAT_ADMIN_PASSWORD`` to ``XNAT_PASS``. The same variables are set inside
   XNAT Container Service jobs, so the command also runs unchanged as a container job.

**Step 4 — Do a dry run.** It reports exactly what a real run would do, and is the quickest way to catch an upload that
is too early or aimed at the wrong place:

.. code-block:: shell

   flip-xnat upload --flip-project-id <project-uuid> --manifest manifest.csv --dry-run

**Step 5 — Upload.** When the counts look right, run it again without ``--dry-run``:

.. code-block:: shell

   flip-xnat upload --flip-project-id <project-uuid> --manifest manifest.csv

Useful options:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Option
     - Purpose
   * - ``--dry-run``
     - Resolve and report; upload nothing.
   * - ``--credentials-file``
     - Read ``server`` / ``user`` / ``password`` from a JSON file instead of the environment. Repeat it to cover
       several Trusts.
   * - ``--project-id``
     - Address the XNAT project directly, if you know its XNAT ID.
   * - ``--resource``
     - Target a resource other than ``NIFTI``.
   * - ``--rename SOURCE:TARGET``
     - Change the prefix swap used to derive filenames (default ``input_:label_``).
   * - ``--overwrite``
     - Replace enrichment files already present. Off by default, so re-running is safe.
   * - ``--allow-no-op``
     - Accept a run that matched no scan at all (see below).
   * - ``--require-full-coverage``
     - Also fail unless every scan in the project got its file and every Trust in the list found the project.

**Enrich every Trust in one run.** A project is enriched only when every participating Trust has been, so repeat
``--credentials-file`` once per Trust:

.. code-block:: shell

   flip-xnat upload --flip-project-id <project-uuid> --manifest manifest.csv \
     --credentials-file gstt.json --credentials-file kch.json

Send the same manifest to every Trust; there is no need to split it. Each accession exists at exactly one Trust, and
the others report it as *skipped (no matching scan)*. A Trust that has not pulled the project is skipped with a
warning.

.. note::

   **A run that matches nothing exits with an error.** If every scan is skipped — wrong project, wrong Trust, or
   conversion not finished — the tool fails, so an automated pipeline does not go on to train without labels.

   - Pass ``--allow-no-op`` when an empty run is expected, for example a partial manifest sent to a Trust that holds
     none of it.
   - If **no** Trust in the list holds the project at all, the image pull never ran, and the run fails whatever flags
     you pass.

.. _enrichment-example:

*******************************
Worked example: spleen labels
*******************************

The spleen segmentation tutorials ship a runnable version of this workflow. It downloads the public MSD spleen dataset,
pairs each label with its accession and uploads them. On the dev stack:

.. code-block:: shell

   make -C fl-tutorials download-spleen-data NUM_CASES=41
   make -C fl-tutorials upload-spleen-labels FLIP_PROJECT_ID=<project-uuid> \
     XNAT_URLS="http://127.0.0.1:8105 http://127.0.0.1:8107" DRY_RUN=1

Remove ``DRY_RUN=1`` to do the upload. Things to know:

- **Both Trusts in one run.** The two URLs are the dev XNATs, GSTT on 8105 and KCH on 8107. These are the XNAT web
  ports; 8104 and 8106 are DICOM receiver ports and will not answer.
- **Per-Trust logins**: pass ``XNAT_CREDENTIALS_FILES`` instead of ``XNAT_URLS``.
- **Download all 41 cases.** The label mapping covers 41 accessions; a smaller download enriches only part of the
  cohort, and the command warns you.
- **Optional** ``TRUST=N`` keeps only accessions whose OMOP ``source_trust`` is ``N`` (on the dev stack ``1`` is GSTT,
  ``2`` is KCH). This numbering is the OMOP data partition, unrelated to the ``Trust_N`` FL kit slot names the hub
  assigns at registration.

See the tutorial READMEs under ``fl-tutorials/`` for the full walkthrough.

.. _enrichment-draw-labels:

*************************************
Drawing labels with MONAI Label
*************************************

When the masks do not exist yet, they can be drawn at the Trust in the XNAT viewer, with MONAI Label
proposing them. They are saved as ROI collections rather than in the scan's ``NIFTI`` resource, so the
app reads them differently; the page below covers drawing, saving and reading them.

.. toctree::
   :maxdepth: 1

   data-enrichment/monai-label

.. _confirm-enrichment:

**********************************
Confirming enrichment is complete
**********************************

FLIP will not start training until you confirm this stage is finished. The confirmation is your declaration that the
data is ready; the platform cannot check it for you.

1. Open the model from the project page.
2. On the right-hand side, switch on **Dataset enriched**.
3. Continue to :ref:`initiate-training`.

.. note::

   You must confirm the data enrichment step is complete **even if no enrichment was required** and none was performed.

.. _enrichment-troubleshooting:

****************
Troubleshooting
****************

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Symptom
     - Likely cause
   * - Training fails with ``No image/label pairs found``
     - Enrichment did not run, ran against the wrong project, or ran before conversion finished. The message reports how many images were found against how many pairs.
   * - Training fails with ``num_samples=0``
     - The same problem, reported by an older app that predates the clearer error.
   * - Upload reports *skipped (no image in resource)* for every scan
     - Ran before DICOM-to-NIfTI conversion completed. There is no ``input_*.nii.gz`` yet to derive a name from. Wait and re-run.
   * - Upload reports *skipped (no matching scan)*
     - Those accessions are not held at this Trust. Expected when uploading a whole cohort to one site; each Trust holds only its own studies.
   * - ``No XNAT project has secondary_ID=...``
     - The image pull has not run at this Trust, or the project id is wrong. Reported per Trust and
       tolerated while another Trust holds the project; when no Trust does, the run exits non-zero.
   * - Upload reports *skipped (already present)*
     - The enrichment file is already in place. Pass ``--overwrite`` only if you intend to replace it.
   * - Upload exits non-zero with *Nothing was resolved*
     - No scan anywhere matched. Check the project id, check you pointed at the right Trust, and check conversion has finished. Pass ``--allow-no-op`` if an empty run is genuinely expected.
   * - Only some cases uploaded, and the command warned about a truncated dataset
     - The local label set does not cover the whole mapping. For the spleen tutorial, re-download with ``NUM_CASES=41``; the Flower snapshot ships a fixed 6-case subset, so point ``--labels-dir`` at the FLARE download instead.
   * - Training still fails after a clean-looking upload at one Trust
     - The other Trusts were never enriched. Repeat ``--credentials-file`` (or ``XNAT_URLS``) so one run covers every Trust in the project.
