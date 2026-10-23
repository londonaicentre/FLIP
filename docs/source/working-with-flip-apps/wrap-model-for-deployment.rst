.. _wrap-model-for-deployment:

####################################
Wrapping a FLIP model for deployment
####################################

.. note::

   This page is a **working draft from a design spike**. The commands have been verified end to end on a
   development workstation, and the app-contract decisions they rely on are in :ref:`wrap-open-questions`.
   Deployment is **not** settled yet (see :ref:`wrap-deepcos`): treat this as a route that works locally, not as a
   deployment procedure.

.. contents:: On this page
   :local:
   :depth: 2


Why this page exists
====================

A completed FLIP training run produces a PyTorch checkpoint: a file of weights. That is a research result, not
something a hospital can use. A radiology department does not run ``.pt`` files. It runs services that receive DICOM
studies from a PACS, produce a result, and send it back to where a radiologist will see it.

**This page covers the step in between**: packaging a model FLIP has finished training so it can be deployed for
inference in a radiology department.

The format is the **MONAI Application Package (MAP)**: an OCI container, built with the `MONAI Deploy App SDK
<https://github.com/Project-MONAI/monai-deploy-app-sdk>`_, that wraps an inference pipeline — DICOM series selection,
preprocessing, inference, postprocessing, DICOM output — with a manifest of what it needs and produces. Because a MAP
takes DICOM in and emits DICOM out, it fits an existing clinical imaging workflow without bespoke integration per
model.

MAP is becoming an industry standard for clinical AI deployment — `deepc
<https://deepc.ai/insight/deepc-establishes-monai-compatibility-strengthening-its-commitment-to-open-source-collaboration-and-global-healthcare-transformation>`_
and `Siemens Healthineers <https://project-monai.github.io/successstories.html>`_ use it, among others — so packaging
this way keeps a FLIP model portable rather than tied to one vendor.

.. important::

   **A MAP packages inference, not training.** FLIP produces the trained weights; the MAP is the deployment wrapper
   around them. In particular, the MAP must carry its own preprocessing, because the training code that defined it
   is not present at inference time.

Bundle or MAP? They are different things
-----------------------------------------

Two MONAI artefacts are easily confused, and this guide produces **both**: a bundle in
:ref:`Step 3 <wrap-export-bundle>`, then a MAP in :ref:`Step 4 <wrap-package>`.

.. list-table::
   :header-rows: 1
   :widths: 22 39 39

   * -
     - MONAI Bundle
     - MONAI Application Package (MAP)
   * - What it is
     - A **model**, packaged with everything needed to run it
     - An **application**, packaged as an OCI container
   * - Contents
     - Weights plus ``inference.json`` (preprocessing, inferer, postprocessing) and
       ``metadata.json`` (input/output schema)
     - An operator graph, a manifest, the runtime, and usually a bundle
   * - Speaks
     - Tensors and arrays — typically NIfTI or in-memory
     - **DICOM** — series selection in, DICOM SEG or SR out
   * - Runs
     - In a Python environment with MONAI installed
     - On any OCI runtime, as a container
   * - Built with
     - ``monai.bundle`` / ``torch.jit.save``
     - ``holoscan package`` (formerly ``monai-deploy package``)

**A MAP normally wraps a bundle.** The bundle answers "given an array, how do I run this model correctly?" —
architecture, preprocessing, postprocessing. The MAP answers "how do I get that array out of a DICOM study, and turn
the result back into a DICOM object a PACS will accept?" In short: the bundle is the model plus its instruction
manual; the MAP is the deployable service around it that speaks the hospital's protocol.

.. note::

   A bundle does not have to be a directory. It can be a **single TorchScript file** with ``inference.json`` and
   ``metadata.json`` embedded in it. That is what :ref:`Step 3 <wrap-export-bundle>` produces by default, and what the
   MONAI Deploy App SDK's own reference models use: the MAP then needs exactly one file passed to ``--models``.
   :ref:`Both forms are available <wrap-bundle-forms>`; the directory form avoids ``torch.jit``.

Where the boundary sits
=======================

.. code-block:: text

   FLIP (training)                          MONAI Deploy (inference)
   ─────────────────────────────────        ────────────────────────────────────
   trainer.py                               DICOM series selection
   models.py :: get_model()          ──▶    preprocessing (must match training!)
   FL_global_model.pt (aggregated)          inference on the exported weights
                                            postprocessing
                                            DICOM SEG / DICOM SR output

The aggregated model exists only on the Central Hub; a Trust only ever holds the local weights it trained itself.
Export therefore runs hub-side, where the weights already are, so packaging adds no new data egress from a Trust.

**Packaging is a separate step, run after training** by whoever prepares the model for deployment. Nothing in the
FL workflow runs it. That separation means:

* **A packaging failure cannot fail a training run.** Export never runs inside the job.
* **The FL images carry no deployment toolchain.** The MONAI Deploy versions and the FL runtime's versions do not
  currently agree (see :ref:`map-environment-traps`), so keeping them apart lets each move independently.
* **You can re-package a checkpoint at any time**, for example with corrected preprocessing, without retraining.

The cost is that you reunite the weights with their architecture and transforms by hand. The ``export/`` configs
committed beside each tutorial make that reliable: the person who knows the preprocessing writes them once, when
authoring the app.


Prerequisites
=============

* **A GPU host with NVIDIA driver 580 or later**, the same on every host that will run the MAP. Check with
  ``nvidia-smi`` before you start.
* **Docker with the NVIDIA Container Toolkit.**
* **A Python 3.12 environment with the pinned MONAI Deploy toolchain**: ``monai-deploy-app-sdk`` 4.0.0,
  ``holoscan-cu13`` and ``holoscan-cli`` 4.2.0, ``monai>=1.6.0`` and ``torch`` 2.13.0. The MONAI Deploy tooling
  changes a lot between releases, so pin every version; :ref:`map-toolchain-pins` explains each one.

.. important::

   **If a deployment site's driver is below 580**, do not loosen these pins one at a time. Use the
   :ref:`previous generation <map-previous-generation>` as a whole set; it produces the same DICOM SEG.

.. seealso::

   :doc:`map-toolchain-reference` — the full version table, the error messages you may hit while installing,
   packaging or running a MAP, and the GPU compatibility measurements behind the pins.


Step 1 — Obtain the aggregated checkpoint
=========================================

A completed FLIP training run uploads its results as a zip containing ``FL_global_model.pt``. On FLARE this is a
checkpoint dictionary whose ``model`` key holds the state dict, alongside ``train_conf`` and optionally
``meta_props``. On Flower it is a plain state dict.

.. warning::

   By default FLIP saves the **last** global model. ``best_FL_global_model.pt`` is written only when ``config.json``
   sets ``BEST_MODEL_METRIC`` (on Flower, when ``config.toml`` sets ``best-model-metric``). Without it, if the final
   round is not the best one, the exported model reflects the final round.


Step 2 — Inspect and round-trip the checkpoint
==============================================

Before packaging anything, confirm that the weights still load into the architecture your app declares. Everything
downstream depends on this.

.. code-block:: shell

   cd flip-utils
   uv run python scripts/inspect_checkpoint.py /path/to/FL_global_model.pt \
       --app-dir /path/to/your/app_files

The script reports the checkpoint's structure, then loads it strictly into ``get_model()`` from your
``models.py``. A clean run ends with ``strict load OK``.

.. note::

   Aggregation turns integer ``num_batches_tracked`` buffers into floats. Strict loading casts them back, so it is
   unaffected — but if you read the raw dictionary yourself, do not assume dtypes survive aggregation.


.. _wrap-export-bundle:

Step 3 — Export a MONAI Bundle
==============================

By default the export is a single ``model.ts``: the TorchScript-compiled network with ``inference.json`` and
``metadata.json`` embedded. A MAP built from it needs **no FLIP application code** — not even ``models.py``.
:ref:`A second form <wrap-bundle-forms>` writes a bundle directory instead and needs no ``torch.jit``.

Run :mod:`flip.export` from the command line:

.. code-block:: shell

   cd flip-utils
   uv run python -m flip.export \
       --checkpoint  /path/to/FL_global_model.pt \
       --app-dir     /path/to/your/app_files \
       --out         bundle/model.ts \
       --input-shape 1,1,96,96,96 \
       --model-id    <uuid> --project-id <uuid> --trusts GSTT,KCH

or as a library (the same code path):

.. code-block:: python

   from pathlib import Path

   from flip.export import Provenance, export_bundle

   result = export_bundle(
       checkpoint=Path("FL_global_model.pt"),
       app_dir=Path("app_files"),
       out=Path("bundle/model.ts"),
       provenance=Provenance(model_id="…", participating_trusts=["GSTT", "KCH"]),
       example_input_shape=(1, 1, 96, 96, 96),
   )
   assert result.max_abs_delta == 0.0

``flip.export`` needs PyTorch, which is in the ``full`` extra (``pip install "flip-utils[full]"``).

What the exporter does and does not do:

- **It reads your bundle configuration; it does not generate it.** It looks for ``inference.json`` and
  ``metadata.json`` in ``<app-dir>/../export/`` by default, written once by you as the app author. The exporter
  loads the weights, compiles them, carries those configs and stamps provenance.
- **Pass** ``--input-shape`` **to verify the export.** It enables a numerical check against the uncompiled model; a
  ``max_abs_delta`` of ``0.0`` shows compilation changed nothing. Without it, the export succeeds but reports that
  it is unverified.
- **Scripting is the default**, because it needs no example input. Use ``--method trace`` for a model that will not
  script; a shape is then required. Both FLIP tutorial models script cleanly with exact equivalence.

.. warning::

   **The preprocessing in** ``inference.json`` **must match the transforms the model was trained with.** If it does
   not, the MAP still runs and still produces a plausible-looking result — it is simply wrong, and nothing warns you.
   For example, the FLIP spleen tutorial windows CT intensity at ``a_max=250``, while the MONAI Model Zoo spleen
   bundle uses ``a_max=164``. Copying the zoo config unchecked would silently degrade the output. See
   :ref:`wrap-inference-transforms`.

.. _wrap-bundle-forms:

Two bundle forms, and why the default is the one it is
------------------------------------------------------

``--form`` selects what is written. The choice is a genuine trade-off:

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - -
     - ``torchscript`` (default)
     - ``directory``
   * - What ``--out`` is
     - A file, conventionally ``model.ts``
     - A directory
   * - The weights
     - TorchScript-compiled network
     - Plain state dict at ``models/model.pt``
   * - The configs
     - Embedded in the TorchScript file
     - Plain files under ``configs/``
   * - Ships your application code
     - **No** — this is why it exists
     - **Yes**, the whole app directory, under ``scripts/``
   * - The architecture
     - Frozen into the compiled graph at export
     - Rebuilt at inference by your ``get_model()``
   * - Uses ``torch.jit``
     - To write and to read
     - **Nowhere**
   * - Usable in a packaged MAP
     - Yes, both templates
     - **Classification only** — see the warning below

Use ``--form directory`` only if your model will not script, or ``torch.jit`` has stopped working. Otherwise keep
the default: a MAP built from it needs no FLIP application code at all.

.. warning::

   **A directory bundle does not work with the segmentation template today.** Packaging succeeds and the MAP starts,
   but inference fails with ``ItemNotExistsError: A predictor of the model is not set``. The cause is in the MONAI
   Deploy App SDK (unchanged in 4.0.0), not in the bundle — see :ref:`map-directory-bundle-limitation`.

   Use the TorchScript form for a segmentation MAP. The directory form currently suits a model that will not script,
   driving a bundle directly from Python, and the **classification** template.

.. warning::

   **The directory form ties your MAP to the MONAI version your model was trained with.** The TorchScript form
   freezes the network, so the MAP never rebuilds it. The directory form calls your ``get_model()`` inside the MAP,
   under whatever MONAI that container has. If a MONAI release renames part of the network you build, the strict
   load fails outright — which is at least the loud failure.

   Before choosing this form, check the MAP's ``requirements.txt`` against the MONAI you trained with, and prefer a
   version range that includes it. The MAP templates and FLIP's training environment are kept on the same MONAI
   (``>=1.6.0`` in both); keep them aligned when either moves.

To write a directory bundle:

.. code-block:: shell

   uv run python -m flip.export \
       --checkpoint /path/to/FL_global_model.pt \
       --app-dir    /path/to/your/app_files \
       --out        bundle \
       --form       directory \
       --input-shape 1,1,96,96,96

which produces::

   bundle/
     configs/inference.json     <- yours, plus a network_def entry
     configs/metadata.json      <- yours, plus provenance
     models/model.pt            <- plain state dict
     scripts/                   <- your app_files, verbatim
     flip_network.py            <- generated: the bundle's entry point to get_model()
     .gitignore                 <- generated: a bundle is a build artefact

- The exporter adds one thing to your ``inference.json``: a ``network_def`` naming ``flip_network.get_model``, so
  the bundle can rebuild the architecture. If you already declare ``network`` or ``network_def``, yours is kept and
  the export warns.
- **The whole app directory is copied, verbatim** — not just ``models.py`` — because ``get_model()`` may read a
  sidecar ``config.json`` or import sibling modules. The generated ``flip_network.py`` makes those imports resolve
  as they do during training. Nothing is written into your own source; caches are left out.
- The bundle has its own ``.gitignore``, because it contains a second copy of your app and ``--out`` can point
  inside your repository.

.. note::

   **Why not** ``torch.export``\ **?** PyTorch is phasing out TorchScript and points users at ``torch.export``, but
   no ``monai-deploy-app-sdk`` release up to 4.0.0 (the version the templates pin) can load its output. The way off
   ``torch.jit`` is therefore the directory form, not a new file format. When MONAI Deploy supports ``torch.export``
   (MONAI core tracks this in `Project-MONAI/MONAI#8632 <https://github.com/Project-MONAI/MONAI/issues/8632>`_), a
   third form becomes possible.

   The pressure is lower than it looks: the App SDK only supports Python below 3.14, so no part of this toolchain
   runs on the Python versions where ``torch.jit`` warns it may break.


.. _wrap-inference-transforms:

Finding your inference transforms
---------------------------------

**By convention, a FLIP app declares its transforms in** ``transforms.py``. The tutorials follow this on both
backends:

.. list-table::
   :header-rows: 1
   :widths: 30 34 36

   * - Tutorial
     - Where the transforms live
     - Validation entry point
   * - ``3d_spleen_segmentation``
     - ``app_files/transforms.py``
     - ``get_val_transforms()`` plus ``get_sliding_window_inferer()``
   * - ``xray_classification``
     - ``app_files/transforms.py``
     - ``get_xray_transforms(is_validation=True)``
   * - ``flower/xray_classification``
     - ``app/transforms.py``
     - ``get_xray_transforms(is_validation=True)``

This is a **convention, not an enforced contract**: ``transforms.py`` is not a required file, function names
differ between apps, and nothing validates either. It gives you one predictable place to look; you still have to
read the code. **Transcribing the chain is a manual step, and the one most likely to go wrong silently.**

As the person who trained the model:

#. **Use the validation/inference chain, not the training one.** Training transforms usually include random
   augmentation (``RandCropByPosNegLabeld``, random flips, intensity jitter), which must *not* appear in
   ``inference.json``. Use the function your app calls when evaluating rather than fitting.
#. **Transcribe the deterministic transforms in order** — typically orientation, spacing/resampling, intensity
   windowing or normalisation, and channel handling. Each becomes an entry in the ``preprocessing`` list, in MONAI's
   ``_target_`` form, with the same arguments and values.
#. **Record the spatial size the network expects.** For patch-based models this is the sliding-window ROI (the
   spleen app uses ``96 × 96 × 96``); for whole-image models it is the resize target (the X-ray app uses
   ``224 × 224``). It goes both in the ``inferer`` and in ``spatial_shape`` in ``metadata.json``.
#. **State the output semantics**: the number of channels and what each one means. For the spleen model, channel 0
   is background and channel 1 is spleen. A MAP that mislabels its channels produces an inverted mask that looks
   entirely plausible.
#. **Check the exported bundle against a known input.** Run the same volume through your training validation path
   and through the MAP, and compare. Agreement is the only real evidence the transcription is faithful; a MAP that
   merely runs proves nothing.

If you adapt a bundle from the MONAI Model Zoo, do not assume its preprocessing matches yours even when the
architecture is identical — as the spleen intensity window above shows.

.. warning::

   **Do not copy orientation transforms across.** Training and the MAP load pixels differently, and an orientation
   step in a training chain usually exists to correct its loader, not for the model.

   By default MONAI's ``LoadImaged`` returns a DICOM's pixel array **transposed**. Training chains often add a
   rotation to undo that. The MAP's DICOM loader does not transpose, so copying the rotation across "corrects"
   something that was never wrong and leaves a different wrong orientation.

   The X-ray tutorial once made exactly this mistake: a transpose plus a rotation is a **mirror**, so it was training
   on left-right flipped radiographs — plausible, visually undetectable, and wrong. The tutorials now load with
   ``LoadImaged(keys=["image"], reader="PydicomReader", swap_ij=False)``, which returns the array as DICOM stores it,
   so neither path needs an orientation transform. Pinning the reader also stops another installed reader (such as
   ITK's) from silently changing the axis order.

   If your chain differs, derive the right transform empirically: dump both arrays for one study and find which of
   the eight rotation/flip combinations matches, using a **non-square** image. Nothing about this failure is loud —
   the MAP runs, the SR is written, and the numbers are just worse than they should be.

.. seealso::

   ``map-apps/classification/README.md`` describes the orientation check step by step. ``fl-tutorials/tests/`` holds
   the same check for the tutorial apps, run in CI.


.. _wrap-package:

Step 4 — Package the MAP
========================

Application templates live in ``map-apps/``: ``map-apps/segmentation`` for models that output a mask,
``map-apps/classification`` for models that output labels. A MAP consumes trained weights, not FL configuration, so
the same templates serve apps trained with either FLARE or Flower.

.. code-block:: shell

   holoscan package map-apps/segmentation \
       --config   map-apps/segmentation/app.yaml \
       --models   <bundle-dir>/model.ts \
       --tag      my_flip_map:latest \
       --platform x86_64 \
       --cuda     13 \
       --sdk      monai-deploy \
       --uid $(id -u) --gid $(id -g)

Two flags need explaining:

``--cuda``
   Selects the base image's CUDA generation. 13 is already the default in ``holoscan-cli`` 4.x, but this is the flag
   to change if you build the CUDA-12 variant. It must agree with the ``holoscan-cu13`` / ``holoscan-cu12`` pin in
   ``requirements.txt``.

``--uid`` / ``--gid``
   **Required**, although the upstream instructions omit them. The packager installs Python dependencies for the
   build user, while the runner starts the container as the *host* user. If the two differ, every import inside the
   MAP fails.


Step 5 — Run the MAP
====================

.. code-block:: shell

   holoscan run my_flip_map-x64-workstation-dgpu-linux-amd64:latest \
       -i /path/to/dicom_series \
       -o ./output

The MAP writes a DICOM Segmentation object that references the source series. **Check it rather than trusting the
exit code**: confirm ``Modality``, the segment coding, that ``ReferencedSeriesSequence`` names the input series, and
that the frame of reference matches.


.. _wrap-view-result:

Step 6 — View the result
========================

A DICOM SEG is only useful if it overlays correctly on the source study. The lightest way to check is a standalone
Orthanc with the OHIF viewer plugin:

.. code-block:: shell

   docker run -d --name map-viewer -p 8142:8042 \
       -e ORTHANC__AUTHENTICATION_ENABLED=false \
       -e DICOM_WEB_PLUGIN_ENABLED=true \
       -e OHIF_PLUGIN_ENABLED=true \
       orthancteam/orthanc:latest

   # upload the source series and the MAP output
   for f in dicom_series/*.dcm; do
       curl -s -X POST http://localhost:8142/instances --data-binary @"$f" \
            -H "Content-Type: application/dicom" > /dev/null
   done
   curl -s -X POST http://localhost:8142/instances --data-binary @output/*.dcm \
        -H "Content-Type: application/dicom" > /dev/null

.. warning::

   Use a standalone Orthanc, not a Trust's PACS. The point is to validate the artefact, not to put research output
   into an operational archive.

The MAP copies the source study's identifiers into its output, so Orthanc groups the result with its source study.
**Confirm that grouping first** — it is the one check that catches a mis-referenced result:

.. code-block:: shell

   curl -s http://localhost:8142/dicom-web/studies \
     | python3 -c "import json,sys; [print(s.get('00080061',{}).get('Value')) for s in json.load(sys.stdin)]"

A correct result shows **both** modalities on one study: ``['CT', 'SEG']`` for segmentation, ``['CR', 'SR']`` for
classification. Two separate studies means the output did not inherit the source identifiers.

Then inspect the result according to its kind.

Segmentation — view the overlay
-------------------------------

Open ``http://localhost:8142/ohif/viewer?StudyInstanceUIDs=<StudyInstanceUID>``. A correct SEG appears as a
selectable segmentation layer over the source series. Look even when the tag checks pass: an inverted mask, a wrong
label order or a half-slice offset are all valid DICOM, and all obvious the moment you see them.

Classification — read the report
--------------------------------

A DICOM SR has no pixel data, so there is nothing to overlay. Read the report text back instead, from Orthanc's REST
API (or interactively, in the Orthanc Explorer 2 tag browser):

.. code-block:: shell

   # find the SR instance, then print its text
   SRID=$(curl -s http://localhost:8142/tools/find -X POST \
       -d '{"Level":"Instance","Query":{"Modality":"SR"}}' \
       | python3 -c "import json,sys; print(json.load(sys.stdin)[0])")

   curl -s "http://localhost:8142/instances/$SRID/tags?simplify" \
     | python3 -c "
   import json,sys
   for item in json.load(sys.stdin).get('ContentSequence', []):
       if item.get('ValueType') == 'TEXT':
           print(item['TextValue'])
   "

.. note::

   **Viewer quirks.** OHIF lists the SR series but is unlikely to render it usefully: its report support targets
   measurement reports with coordinates, not the plain-text SR a classification MAP produces. That is a viewer
   limitation, not a defect in the output.

   If a study seems to be missing from the viewer, check that you used **that study's** ``StudyInstanceUIDs``, and
   that OHIF's study list is not hiding older studies behind a default date filter. Both are far more common than a
   real rendering failure. To confirm the image data is retrievable, fetch one frame — an ``HTTP 200`` with a
   plausible byte count means the viewer has what it needs:

   .. code-block:: shell

      curl -s -o /dev/null -w "%{http_code} %{size_download}\n" \
        -H "Accept: multipart/related; type=application/octet-stream" \
        "http://localhost:8142/dicom-web/studies/<study>/series/<series>/instances/<instance>/frames/1"


.. _wrap-dicom-gap:

From NIfTI training to DICOM deployment
========================================

This is the largest conceptual gap in the whole path, and the tooling makes it easy to miss.

**FLIP training is NIfTI-shaped.** ``flip.get_by_accession_number(...)`` defaults to ``ResourceType.NIFTI``: the
trainer receives one already-converted volume, in a known orientation, with spacing and geometry resolved. It never
sees a DICOM instance or chooses between series.

**Deployment is DICOM-shaped.** A MAP receives a *study* — a folder of loose DICOM instances, possibly several
series, in any order — and must return a DICOM object that a PACS will accept and a viewer will overlay correctly.
Everything in between is work the training pipeline never did.

Operators the MAP needs that training never touched
----------------------------------------------------

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Operator
     - What it does, and why training had no equivalent
   * - ``DICOMDataLoaderOperator``
     - Reads a study of loose instances into memory. Training received one file.
   * - ``DICOMSeriesSelectorOperator``
     - **Chooses which series to run on**, by rule — modality, series description, study description. Training was
       handed the right data; a MAP must decide for itself, and a rule that matches nothing produces no output while
       still exiting cleanly.
   * - ``DICOMSeriesToVolumeOperator``
     - Sorts instances into slice order and assembles a volume with correct spacing and orientation — reconstructing
       what the NIfTI already was.
   * - ``DICOMSegmentationWriterOperator``
     - Writes a DICOM SEG: segment coding, referenced series and instances, frame of reference. Training wrote
       arrays.
   * - ``DICOMTextSRWriterOperator``
     - The classification equivalent: a DICOM Structured Report. See
       :ref:`the classification section <wrap-classification>`.

Concepts that only exist on the deployment side
------------------------------------------------

At each of these, a MAP can emit *valid* DICOM that is nonetheless wrong, and none has an equivalent in the training
code:

* **Series selection rules.** Radiographs are ``CR`` or ``DX``, not ``CT``. A rule copied from another app silently
  selects nothing.
* **Frame of reference and referenced instances.** These let a viewer lay the result over the source. Wrong or
  missing, the output is a valid orphan.
* **SOP classes.** A segmentation is Segmentation Storage; a report is Basic Text SR. The writer sets these, but you
  choose the writer.
* **Coded terminology.** Segments and findings carry SNOMED CT concepts. A mislabelled segment looks identical to a
  correct one unless you read the codes.
* **Character sets.** DICOM defaults to a restricted character set, so non-ASCII text is silently mangled — see
  :ref:`the classification section <wrap-classification>`.

The practical consequence
--------------------------

**A model that trains perfectly can still deploy wrongly, and the failure will not look like one.** The MAP runs,
exits zero and writes well-formed DICOM. That is why :ref:`Step 6 <wrap-view-result>` insists on inspecting the
result rather than trusting the exit code, and why the overlay check is worth doing even when every tag is correct:
geometry errors are obvious to the eye and invisible in a tag dump.

It is also why the preprocessing transcription in :ref:`Step 3 <wrap-export-bundle>` matters so much. Training's
NIfTI arrived already resampled; at inference, the bundle's ``Spacingd`` and ``Orientationd`` transforms must
reproduce that from whatever geometry the source study has.


.. _wrap-classification:

Classification models produce a different artefact
==================================================

The pipeline above is segmentation-shaped. A classification model has no mask to write, so instead of
``DICOMSegmentationWriterOperator`` it emits a **DICOM Structured Report** through ``DICOMTextSRWriterOperator``:

.. code-block:: text

   segmentation:    ... → MonaiBundleInferenceOperator → DICOMSegmentationWriterOperator  → DICOM SEG
   classification:  ... → <custom classifier operator> → DICOMTextSRWriterOperator        → DICOM SR

What this means for you:

* **You need a small custom operator.** The bundle inference operator maps image in to image out and cannot emit a
  label. A classification MAP needs an operator that runs the scripted network and formats the result text. The
  SDK's ``breast_density_classifier_app`` is the closest worked example.
* **Match the series selection rule to your modality.** Radiographs arrive as ``CR`` or ``DX``, not ``CT``. A rule
  copied from a segmentation app selects nothing, and the MAP produces no output while still exiting successfully.
* **Match the activation to the training loss.** The FLIP X-ray tutorial trains with binary cross-entropy over two
  independent labels, so outputs are read through a sigmoid and thresholded one by one. A softmax — the natural
  assumption for a two-output network — would silently turn two independent findings into one exclusive choice.
* **Keep SR text ASCII.** The SR writer does not set a character set, so a non-ASCII character such as an em dash or
  a degree sign is stored as ``?``. The application log shows the original string correctly; only the stored DICOM
  is affected.
* **Verify by reading the report**, since there is no pixel data to overlay:

  .. code-block:: python

     import pydicom

     ds = pydicom.dcmread("output/<sr-instance>.dcm")
     assert ds.Modality == "SR"
     for item in ds.ContentSequence:
         if item.ValueType == "TEXT":
             print(item.TextValue)


.. _wrap-config-enforcement:

What FLIP actually validates in ``config.json``
================================================

It is easy to assume FLIP checks more of ``config.json`` than it does. What is checked:

- **The Central Hub reads** ``job_type``, to choose which template to bundle. An unknown ``job_type`` is
  rejected; a missing one defaults to ``standard``. On FLARE it also reads ``SERVER_CHECKPOINT`` (or, for
  ``evaluation``, each ``models[*].checkpoint``) to keep those checkpoints on the server instead of shipping them to
  every client.
- **On FLARE, FLIP validates a fixed set of platform keys** when it assembles the job. Some failures stop the job
  rather than falling back silently: an unknown ``AGGREGATOR``, an ``AGGREGATION_WEIGHTS`` value outside 0–1, an
  invalid ``AGGREGATE_ONLY_REGEX``, or ``BEST_MODEL_METRIC`` on a single-round job. The full set is under
  :ref:`fl-training-configuration`.
- **Every other key passes through untouched**, for your own code to read. ``LEARNING_RATE``, ``VAL_SPLIT``,
  ``net_config`` and any key you invent are neither validated nor defaulted.
- **File presence is enforced.** Each job type has a required-file list, and a submission missing a file fails
  before anything is uploaded. See :ref:`fl-required-files`.

.. warning::

   **A typo in an export-related key fails silently.** An invented key is outside the validated set, so nothing
   rejects it: the value is just absent at run time, and your code falls back to its own default. Any export
   setting added to ``config.json`` in future should come with validation.

.. note::

   Two related traps:

   - A present but out-of-range ``LOCAL_ROUNDS`` is **not** corrected: the default is written only when the key is
     absent, so ``"LOCAL_ROUNDS": 5000`` really runs 5000 local iterations.
   - On Flower the typo behaviour is reversed. Run-config overrides live in ``config.toml``, and Flower **rejects**
     any key the app's ``pyproject.toml`` does not declare, failing the run at submission.

.. _wrap-open-questions:

The FLIP app contract for export
================================

Two decisions set what an app author must provide for a trained model to be packageable. Both are on the FLIP side
and independent of any deployment target.

**The input specification uses MONAI's schema.** A bundle needs input shape, voxel spacing, intensity window,
channel meanings and output labels. FLIP uses the ``network_data_format`` block that MONAI already defines in a
bundle's ``metadata.json`` rather than inventing its own: it covers exactly this, and it is what the MONAI tooling
reads. Worked examples sit beside each tutorial in ``fl-tutorials/<backend>/<tutorial>/export/``.

**Inference transforms are declared by convention, in** ``transforms.py``. A free-form Python module can build its
transforms however it likes, so the inference chain cannot be derived automatically, and any rule strict enough to
make that safe would be too restrictive for app authors. FLIP standardises *where to look* instead; the person
exporting the model transcribes the validation chain from there. See :ref:`wrap-inference-transforms` for the
procedure.

.. note::

   **MONAI Deploy Express is not currently a safe dependency.** Its repository (``Project-MONAI/monai-deploy``) and
   workflow manager have had no commits since March 2025, though the App SDK itself is actively maintained. To
   validate a MAP, use the SDK plus a standalone Orthanc, as in :ref:`Step 6 <wrap-view-result>`.


.. _wrap-deepcos:

Deploying to deepcOS
====================

Everything above produces a MAP that runs standalone. **deepcOS, the intended production runtime, needs more than
that.** This section records what, and what it changes for FLIP.

deepc has `publicly established MONAI compatibility on deepcOS
<https://deepc.ai/insight/deepc-establishes-monai-compatibility-strengthening-its-commitment-to-open-source-collaboration-and-global-healthcare-transformation>`_,
and documents a MONAI integration path in its engine-build SDK documentation:

    https://docs.one.deepc.pro/engines/build/sdk/support/monai

.. important::

   That documentation is **access-controlled and commercially confidential**, and it is not reproduced here. This
   section records only the consequences for FLIP. Anyone implementing against deepcOS must read the vendor
   documentation directly and treat it, not this page, as authoritative.

What is established
-------------------

**A plain MAP is not enough.** The application structure stays standard MONAI, but the inference operator must also
import deepc's library and write a structured *engine report* alongside its DICOM output. The ``map-apps/``
templates are therefore the inner layer of a deepcOS submission, not the submission itself.

**Results are reported as structured clinical findings, not pixel data.** The engine report follows the DICOM
hierarchy (patient, study, series, instance), and each level can carry results in three forms:

- *findings* — a coded clinical concept, optionally with a confidence score and a present/absent state;
- *quantities* — a numeric value with coded units and an anatomical attribute;
- *regions of interest* — a 2D polygon tied to one referenced DICOM instance.

Concepts are SNOMED CT or RadLex terms. Where no standard term exists, the vendor documents a way to express a
locally defined concept, so terminology gaps do not block an integration.

**DICOM output is forwarded.** DICOM files the engine writes to its output directory are collected and passed on
automatically, so a DICOM SEG is an acceptable output.

**But pixel output alone is not enough.** Where an engine encodes its result in pixel data, the vendor requires the
same information in machine-readable form in the engine report — and **a DICOM SEG is exactly that case**. The
report has no dense mask format (the closest is a per-instance polygon), so the machine-readable half of a
segmentation is the mask's *derived clinical content*: for example the segmented volume as a quantity with coded
units, optionally with regions of interest for display. The SEG carries the pixels; the report carries the meaning.

.. warning::

   The engine report has a field for asserting an **all-negative** result — a clinical statement that no
   abnormality was found, which can drive automatic negative reporting downstream. The vendor reserves it for
   engines with the appropriate regulatory clearance.

   A FLIP research model is neither CE-marked nor FDA-cleared, so **leave that field null**, as the vendor's own
   sample report does. You can still report a negative: a finding with an explicit *absent* state says "this model
   looked for X and did not find it", without the clearance implications. The difference is between reporting what
   the model found and certifying that a study is clear.

**Report file and required fields:**

- Write the report as JSON or YAML in the same output directory as the DICOM files.
- Its filename must contain ``deepcreport``, matched case-insensitively: ``spleen-deepcReport.json`` qualifies,
  ``report_deepc.json`` does not.
- The job status, the coded messages and the engine result are all mandatory.

**Set the job status deliberately**, not to success by default at the end. The vendor notes that a process exit
code does not reliably show whether the *analysis* succeeded — the same failure as in
:ref:`Step 6 <wrap-view-result>`, where a MAP whose series selection matched nothing exits zero with no output. Use
the coded messages field to report that kind of condition.

The vendor's tooling includes a validator for completed engine reports. Run it before any submission.

**The supported dependency set is older than the one this guide pins.** deepc's MONAI-compatible variant targets an
earlier App SDK and Holoscan release, an earlier Python and pydicom 2.x, on a CUDA 12 runtime. **Do not assume the
stack pinned on this page deploys to deepcOS** — it was chosen for a development workstation, not to match the
vendor's supported versions.

What this changes for FLIP
--------------------------

* **Two build profiles, not one.** The stack on this page is a development target; a deepcOS submission needs its
  own. The ``map-apps/`` templates keep inference logic free of SDK-version-specific code, because the model
  arrives as a self-contained TorchScript bundle, so that stays cheap.
* **Classification is close to mechanical.** ``map-apps/classification`` already produces what a finding needs: a
  per-label probability and a present/absent decision against a threshold. What remains is assigning coded
  concepts to the FLIP label names — a clinical terminology task, not an engineering one.
* **The DICOM SEG stays.** It is what a viewer overlays, what :ref:`Step 6 <wrap-view-result>` checks, what any
  platform in the AIDE lineage consumes, and deepcOS forwards it as-is.
* **Segmentation needs a derived quantity it does not compute yet.** The SEG writer outputs pixels only, so a
  deepcOS submission also needs the segmented volume — voxel counting against the volume's spacing. Small, but new
  work.
* **Report every finding the model can produce, not just the positive ones.** An explicit present or absent state
  for each lets downstream systems accept or reject individual findings, generate free-text reports and run
  performance analytics. For the X-ray classifier this means reporting both labels every time, which
  ``map-apps/classification`` already does internally.

Still to establish
------------------

* Runtime constraints: network and persistent-storage policy, model size limits, timeouts.
* Who defines the DICOM-tag routing rule that triggers an engine — deepc or the site.
* The research (non-CE-marked) pathway. A FLIP research model is neither CE-marked nor FDA-cleared, so this decides
  whether it may run at all outside a sandbox.

Prior art: how AIDE consumed MAPs
---------------------------------

AIDE, the AI Deployment Engine this Centre built before deepcOS, deployed applications as MAPs. Its developer
documentation is the closest thing to a specification of how a clinical platform consumes a MAP. Three points carry
over:

- **A MAP was not the whole submission.** Each MAP was wrapped in an Argo Workflow template declaring the container's
  input and output directories and the command to run. Expect the MAP to be one layer of a larger deployment
  descriptor.
- **The container had no network access and no persistent disk.** Files had to go to the declared output path. A
  MAP that reaches a licence server, fetches weights at run time or caches to disk will fail there — design that out
  now.
- **Input arrived as a study tree, not a flat folder:**

  .. code-block:: text

     study_uid/
     ├─ series_uid/
     │  ├─ slice_uid.dcm
     │  └─ slice_uid_metadata.json

Outputs could then go to a **Clinical Review** step, where a clinician assessed them before export back to PACS.
That is a platform feature: the MAP's job ends at a well-formed DICOM object.

AIDE's documentation also recommended MONAI Deploy Express for local testing, with Orthanc standing in for the Trust
PACS — the same shape as the workflow on this page.

.. seealso::

   :doc:`map-toolchain-reference` for the version pins, environment traps and GPU measurements;
   :gh:`map-apps/README.md` and :gh:`flip-utils/flip/export/README.md` for the template and exporter detail.
