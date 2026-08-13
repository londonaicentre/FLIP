.. _data-enrichment:

################
Data Enrichment
################

.. warning:: Must be logged into FLIP, be assigned to the project, and have access to the :term:`XNAT` instance at each participating Trust. See :ref:`receiving-xnat-credentials`.

*Data enrichment* is the stage where a model developer adds whatever their app needs on top of the imaging data FLIP has pulled into each Trust's XNAT. Trust :term:`PACS` supply images only, so anything the app expects alongside them — segmentation masks, ROI annotations, contours — has to be added at this stage.

Enrichment happens **once per project**, inside XNAT. It is independent of which federated learning backend the project uses — both :term:`NVIDIA FLARE` and the :term:`Flower Framework` read the same enriched data.

.. _enrichment-do-i-need-it:

**************************************
Do you need it? Where labels come from
**************************************

Supervised models need a label for every image, but **enrichment is not how most labels arrive**. There are two routes, and choosing the wrong one creates work that was never necessary.

.. list-table::
   :header-rows: 1
   :widths: 22 44 34

   * - Route
     - Use when
     - How the app reads it
   * - **Cohort query (OMOP)**
     - The label already exists as structured clinical data — a lab result (e.g. a COVID PCR result for a chest X-ray), a diagnosis, an observation, or a coded finding from a radiology report.
     - Project it as a column in the project's cohort query. It arrives in the dataframe returned by ``flip.get_dataframe(...)``, one row per accession, and the app reads it directly.
   * - **Data enrichment (XNAT)**
     - The label does not exist in :term:`OMOP` — typically anything **spatial or image-derived**: segmentation masks, ROI contours, landmark annotations. Also labels created specifically for the project, such as a re-read or expert correction.
     - Upload it into XNAT beside the image, as described on this page. It is downloaded with the image by ``flip.get_by_accession_number(...)``.

**Prefer the cohort query wherever the data supports it.** A label that is already in OMOP needs no upload, no per-Trust visit, and no re-run when the cohort changes — it is just another column of SQL.

The chest X-ray classification tutorial is the worked example of the first route: its ``query.sql`` joins ``image_feature`` to ``observation`` to project ``Effusion``, ``Edema`` and ``Lungs in normal arrangement`` as columns, and the app reads those per row. It performs **no enrichment at all**.

The spleen segmentation tutorials are the worked example of the second: a segmentation mask is a 3D volume, so there is nowhere in OMOP for it to live and it has to be uploaded alongside the image.

.. note::

   The platform cannot tell which route your model needs, which is why it asks you to confirm this stage is complete (see :ref:`confirm-enrichment`) even when nothing needed adding. If your labels come from the cohort query, that confirmation is all this stage requires of you.

A project that *does* need enrichment and skips it will pull its imaging data successfully and then fail at training time with no usable samples.

.. _enrichment-when:

**********************
When to run it
**********************

Enrichment must happen **after two earlier steps have finished**:

1. **The image pull.** The project's cohort query has to have run and the matching studies must have been imported into each Trust's XNAT. Until then there are no scans to attach anything to.
2. **DICOM-to-NIfTI conversion.** FLIP converts each pulled DICOM series into a NIfTI volume named ``input_<something>.nii.gz``. Enrichment files are named after that converted image, so running before conversion completes silently attaches nothing.

Running too early is the single most common mistake, and it does not announce itself — the upload reports that it skipped every scan. If that happens, wait for conversion to finish and run it again.

.. _enrichment-contract:

*******************************
Where enrichment files must go
*******************************

The FL client downloads imaging data per accession by resource, so an enrichment file is only visible to your app if it sits **in the same scan's resource as the image it belongs to**.

For the standard NIfTI workflow that means:

- **Resource**: ``NIFTI`` — the same resource holding the converted image, not a new one and not a separate assessor.
- **Filename**: the image's own filename with its prefix swapped, i.e. ``input_spleen_2.nii.gz`` pairs with ``label_spleen_2.nii.gz``.

The apps rely on that filename pairing directly. The spleen tutorials, for example, find each label by taking the image path and substituting ``/input_`` with ``/label_``. Deriving the label name *from the image already in XNAT* — rather than assuming it — is what keeps the pair matched whatever the conversion produced.

.. warning:: Do not upload an enrichment file under the image's own name. It will overwrite the imaging data for that scan.

.. _enrichment-manual:

*******************************
Option 1: the XNAT web UI
*******************************

For a handful of scans, or for enrichment done by hand (drawing a segmentation, correcting an annotation), work directly in XNAT at each Trust:

1. Connect to the Trust network and sign in to that Trust's XNAT instance.
2. Open the project. Its XNAT name will differ per Trust — the FLIP project id is stored in the XNAT project's **secondary ID**, so search on that if the name is not obvious.
3. Navigate to the subject, then the session, then the scan you want to enrich.
4. Open the scan's ``NIFTI`` resource and note the existing ``input_*.nii.gz`` filename.
5. Upload your file into that same resource, named to match (``label_*.nii.gz``).
6. Repeat for every scan in the cohort, at **every** participating Trust.

Because each Trust holds only its own studies, this has to be done once per Trust.

.. _enrichment-scripted:

**********************************
Option 2: the ``flip-xnat`` CLI
**********************************

For a whole cohort, use the upload tool that ships with the ``flip-utils`` package. It resolves the XNAT project from your FLIP project id, derives each target filename from the image already in XNAT, and refuses to overwrite anything unless you ask it to.

Install it on a machine with access to the Trust network:

.. code-block:: bash

   pip install flip-utils

Describe what to upload in a **manifest** — a CSV pairing each accession with a local file:

.. code-block:: text

   accession_id,file_path
   FAK60462657,labels/label_spleen_2.nii.gz
   FAK76435891,labels/label_spleen_3.nii.gz

Relative paths resolve against the manifest's own directory. An optional third column, ``target_filename``, overrides the derived name when you need something other than the default prefix swap.

Provide XNAT credentials either as environment variables or as a JSON file:

.. code-block:: bash

   export XNAT_HOST=https://xnat.trust.example
   export XNAT_USER=your-username
   export XNAT_PASS=your-password

Then check what would happen before changing anything:

.. code-block:: bash

   flip-xnat upload --flip-project-id <project-uuid> --manifest manifest.csv --dry-run

A dry run reports exactly what a real run would do, and is the fastest way to catch a mistimed or misaddressed upload. When the counts look right, drop ``--dry-run``:

.. code-block:: bash

   flip-xnat upload --flip-project-id <project-uuid> --manifest manifest.csv

Useful options:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Option
     - Purpose
   * - ``--dry-run``
     - Resolve and report, upload nothing.
   * - ``--credentials-file``
     - Read ``server`` / ``user`` / ``password`` from a JSON file instead of the environment.
   * - ``--project-id``
     - Address the XNAT project directly, when you already know its XNAT id.
   * - ``--resource``
     - Target a resource other than ``NIFTI``.
   * - ``--rename SOURCE:TARGET``
     - Change the prefix swap used to derive filenames (default ``input_:label_``).
   * - ``--overwrite``
     - Replace enrichment files that are already present. Off by default, so re-running is safe.

Run the command once per Trust, pointing ``XNAT_HOST`` at each in turn.

.. _enrichment-example:

*******************************
Worked example: spleen labels
*******************************

The spleen segmentation tutorials ship a complete, runnable version of this workflow. It downloads the public MSD spleen dataset, pairs each label with the right accession, and uploads them:

.. code-block:: bash

   make -C fl-tutorials download-spleen-data NUM_CASES=41
   make -C fl-tutorials upload-spleen-labels FLIP_PROJECT_ID=<project-uuid> TRUST=1 DRY_RUN=1

Drop ``DRY_RUN=1`` to perform the upload, and repeat with ``TRUST=2`` (and the second Trust's XNAT credentials) for the other site. See the tutorial READMEs under ``fl-tutorials/`` for the full walkthrough.

.. _confirm-enrichment:

**********************************
Confirming enrichment is complete
**********************************

FLIP will not start training until you confirm this stage is finished. The confirmation is a declaration that the data is ready, not a check the platform can make for you.

1. Navigate to the project page
2. On the right-hand side, toggle the button to confirm the dataset has been enriched
3. Continue to :ref:`training-configuration`

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
     - The image pull has not run at this Trust, or the project id is wrong.
   * - Upload reports *skipped (already present)*
     - The enrichment file is already in place. Pass ``--overwrite`` only if you intend to replace it.
