##############################################
Packaging a trained model for inference (MAP)
##############################################

.. note::

   This page is a **working draft from a design spike**. The command sequence has been verified
   end to end on a development workstation, and the app-contract decisions it depends on are
   recorded in :ref:`map-open-questions`. What has *not* been settled is deployment: see
   :ref:`map-deepcos`. Treat this as a route that works locally, not as a deployment procedure.

.. contents:: On this page
   :local:
   :depth: 2


Why this page exists
====================

FLIP trains models federatedly across NHS Trusts, and what comes out of a completed run is a
PyTorch checkpoint: a file of weights. That is a research result, not something a hospital can
use. A radiology department does not run ``.pt`` files — it runs services that receive DICOM
studies from a PACS, produce a result, and send that result back to where a radiologist will see
it.

**This page covers the step in between**: taking a model that FLIP has finished training and
packaging it so it can be deployed for inference in a radiology suite.

The packaging format is the **MONAI Application Package (MAP)** — an OCI container that wraps an
inference pipeline as a graph of operators (DICOM series selection, preprocessing, inference,
postprocessing, DICOM output), together with a manifest describing what the container needs and
produces. Built with the `MONAI Deploy App SDK
<https://github.com/Project-MONAI/monai-deploy-app-sdk>`_, a MAP takes DICOM in and emits DICOM
out, which is what makes it deployable into an existing clinical imaging workflow rather than
something requiring bespoke integration per model.

The MAP is becoming an industry standard for clinical AI deployment, used by `deepc
<https://deepc.ai/insight/deepc-establishes-monai-compatibility-strengthening-its-commitment-to-open-source-collaboration-and-global-healthcare-transformation>`_
and `Siemens Healthineers <https://project-monai.github.io/successstories.html>`_ among others, so
packaging this way keeps a FLIP model portable rather than tied to one vendor.

The framing that matters most
------------------------------

**A MAP packages inference, not training.** FLIP produces trained weights; the MAP is the
deployment wrapper around them. Everything below follows from that handover point — in particular,
the fact that a MAP must carry its own preprocessing, because the training code that defined it
will not be present at inference time.


Bundle or MAP? They are different things
-----------------------------------------

Two MONAI artefacts are easily confused, and this guide produces **both** — a bundle in
:ref:`Step 3 <map-export-bundle>`, then a MAP in :ref:`Step 4 <map-package>`.

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
     - An operator graph, a manifest, the runtime, and usually a bundle inside it
   * - Speaks
     - Tensors and arrays — typically NIfTI or in-memory
     - **DICOM** — series selection in, DICOM SEG or SR out
   * - Runs
     - In a Python environment with MONAI installed
     - On any OCI runtime, as a container
   * - Built with
     - ``monai.bundle`` / ``torch.jit.save``
     - ``holoscan package`` (formerly ``monai-deploy package``)

The relationship is containment: **a MAP normally wraps a bundle.** The bundle answers "given an
array, how do I run this model correctly?" — architecture, preprocessing, postprocessing. The MAP
answers "how do I get that array out of a DICOM study, and turn the result back into a DICOM
object a PACS will accept?" Inside a segmentation MAP, ``MonaiBundleInferenceOperator`` is
literally the operator that runs the bundle.

A useful way to hold it: the bundle is the model plus its instruction manual; the MAP is the
deployable service built around it that speaks the hospital's protocol.

.. note::

   A bundle does not have to be a directory. It can also be a **single TorchScript file** with
   ``inference.json`` and ``metadata.json`` embedded as TorchScript *extra files* — which is what
   :ref:`Step 3 <map-export-bundle>` produces, and what the MONAI Deploy App SDK's own reference
   models use. That form is convenient here because a MAP then needs exactly one artefact
   passed to ``--models``.

Where the boundary sits
=======================

.. code-block:: text

   FLIP (training)                          MONAI Deploy (inference)
   ─────────────────────────────────        ────────────────────────────────────
   trainer.py / validator.py                DICOM series selection
   models.py :: get_model()          ──▶    preprocessing (must match training!)
   FL_global_model.pt (aggregated)          inference on the exported weights
                                            postprocessing
                                            DICOM SEG / DICOM SR output

The aggregated model exists only on the Central Hub; each Trust holds only its own
``local_model.pt``. Export therefore runs hub-side, where the weights have already arrived
legitimately, so packaging adds no new data egress from a Trust.

**Packaging is deliberately a separate step from training, not a stage inside it.** A training run
finishes and uploads its checkpoint; packaging is then run against that artefact afterwards, by
whoever is preparing the model for deployment. Nothing in the FL workflow invokes it.

That separation is a design decision, and it buys three things:

* **A packaging failure cannot affect a training run.** There is no path by which a bad transform
  or a broken bundle config marks a successful multi-Trust run as failed, because export never
  executes inside the job.
* **The FL runtime carries no deployment dependencies.** If export ran inside ``fl-server``, that
  image would have to carry the MONAI Deploy toolchain and reconcile it against the NVFLARE and
  torch pins already there. Those version sets do not currently agree — see
  :ref:`map-environment-traps` and :ref:`map-deepcos` — so coupling them would make both harder to
  move independently.
* **Packaging can be repeated, revised and re-run** against a checkpoint long after the run that
  produced it, including with a corrected preprocessing chain, without retraining anything.

The cost is that reuniting weights with their architecture and transforms is a manual step. That
is what the committed ``export/`` configs beside each tutorial exist to make reliable: the
specification is written once, at authoring time, by the person who knows the preprocessing.


Prerequisites
=============

Pin these versions. The MONAI Deploy tooling has changed substantially across releases and an
unpinned instruction rots quickly.

.. list-table::
   :header-rows: 1
   :widths: 32 22 46

   * - Component
     - Version
     - Notes
   * - ``monai-deploy-app-sdk``
     - ``3.5.0``
     - 4.x requires a CUDA 13 base image; see :ref:`map-environment-traps`.
   * - ``holoscan`` / ``holoscan-cli``
     - ``3.11.0``
     - SDK 3.5.0 uses ``holoscan.graphs``, removed in holoscan 4.x.
   * - ``monai``
     - ``1.5.0``
     - Declares ``torch<2.7.0``; see the Blackwell note below.
   * - ``torch``
     - ``2.11.0+cu128``
     - Install **last**, so MONAI's pin does not downgrade it.
   * - Python
     - ``3.12``
     - The MAP base image must be Ubuntu 24.04 to match.

You also need Docker with the NVIDIA Container Toolkit, and an NVIDIA driver appropriate to the
CUDA version of the MAP base image.


Step 1 — Obtain the aggregated checkpoint
=========================================

A completed FLIP training run uploads its results to S3 as a zip containing
``FL_global_model.pt``. This is an NVFLARE persistence-format checkpoint: an ``OrderedDict`` with
a ``model`` key holding the state dict, alongside ``train_conf`` and optionally ``meta_props``.

.. warning::

   FLIP persists the **last** global model by default. The ``standard`` job type wires a model
   selector only when ``config.json`` sets ``BEST_MODEL_METRIC`` — without it,
   ``best_FL_global_model.pt`` is never written. If your run's final round is not its best round,
   the exported model will reflect the final round.


Step 2 — Inspect and round-trip the checkpoint
==============================================

Before packaging anything, confirm the weights still load into the architecture the application
declares. This is the assumption everything downstream rests on.

.. code-block:: bash

   cd flip-utils
   uv run python scripts/inspect_checkpoint.py /path/to/FL_global_model.pt \
       --app-dir /path/to/your/app_files

The script reports the checkpoint's structure and then attempts
``load_state_dict(..., strict=True)`` against ``models.py::get_model()``. A clean run ends with
``strict load OK``.

.. note::

   Aggregation converts integer ``num_batches_tracked`` buffers to floating point, because every
   entry of the state dict is averaged as a float. ``load_state_dict`` casts them back on copy, so
   strict loading is unaffected — but do not assume dtypes survive a round of aggregation if you
   read the raw dictionary yourself.


.. _map-export-bundle:

Step 3 — Export a MONAI Bundle
==============================

The exported artefact is a single ``model.ts``: the TorchScript-compiled network carrying
``inference.json`` and ``metadata.json`` as TorchScript *extra files*. This is the shape
``MonaiBundleInferenceOperator`` consumes, and it is what breaks the dependency on FLIP
application code — a MAP built this way needs no ``models.py``.

Use :mod:`flip.export`:

.. code-block:: bash

   cd flip-utils
   uv run python -m flip.export \
       --checkpoint  /path/to/FL_global_model.pt \
       --app-dir     /path/to/your/app_files \
       --out         bundle/model.ts \
       --input-shape 1,1,96,96,96 \
       --model-id    <uuid> --project-id <uuid> --trusts GSTT,KCH

Or as a library, which is the same code path:

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

``flip.export`` requires PyTorch, which ships in the ``full`` extra rather than the base install
(``pip install "flip-utils[full]"``).

**It reads the bundle configuration rather than generating it.** ``inference.json`` and
``metadata.json`` are looked up in ``<app-dir>/../export/`` by default — written once by the app
author, who is the person who knows the preprocessing. The exporter's job is to load the weights,
compile them, embed those configs and stamp provenance.

``--input-shape`` is optional when scripting, but supplying it is worth the keystrokes: it enables
a numerical equivalence check against the eager model, and ``result.max_abs_delta`` of ``0.0`` is
positive evidence that compilation changed nothing. Without it the export succeeds but reports
that it was unverified.

Scripting is the default because it requires no example input. Tracing is available via
``--method trace`` for models that will not script, but then a shape is mandatory. Both FLIP
tutorial models script cleanly with exact equivalence.

.. note::

   PyTorch has begun deprecating TorchScript in favour of ``torch.export``, and torch 2.11 emits
   ``DeprecationWarning`` from ``torch.jit.save``/``load``/``trace``. TorchScript remains the
   format MONAI Deploy's bundle inference operator consumes, so it is the correct choice today —
   but this is a dependency worth watching, and a reason not to spread ``torch.jit`` calls across
   the codebase when they can live behind :func:`flip.export.export_bundle`.

.. warning::

   **The preprocessing declared in** ``inference.json`` **must match the transforms the model was
   trained with.** If it does not, the MAP still runs and still emits a plausible-looking result —
   it is simply wrong, with nothing to alert you. The FLIP spleen tutorial windows CT intensity at
   ``a_max=250``; the MONAI Model Zoo spleen bundle uses ``a_max=164``. Copying the zoo config
   without checking would silently degrade the output.


.. _map-inference-transforms:

Finding your inference transforms
---------------------------------

**By convention, a FLIP app declares its transforms in** ``transforms.py``. The shipped tutorials
follow this on both backends:

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

This is a **convention, not an enforced contract**. ``transforms.py`` is not in any job type's
required-file list, the function names differ legitimately between apps, and nothing validates
either. The convention exists so there is one predictable place to look — it does not remove the
need to read the code.

**Transcribing the chain is therefore a manual step, and it is the step most likely to be got
wrong silently.**

What you need to do, as the person who trained the model:

#. **Find the validation/inference chain, not the training one.** Training transforms usually
   include random augmentation (``RandCropByPosNegLabeld``, random flips, intensity jitter). Those
   must *not* appear in ``inference.json``. Use whichever function your app calls when it is
   evaluating rather than fitting.
#. **Transcribe the deterministic transforms in order.** Typically orientation, spacing/resampling,
   intensity windowing or normalisation, and channel handling. Each becomes an entry in the
   ``preprocessing`` list, using MONAI's ``_target_`` form with the same arguments and the same
   numeric values.
#. **Record the spatial size the network expects.** For patch-based models this is the sliding
   window ROI (the spleen app uses ``96 × 96 × 96``); for whole-image models it is the resize
   target (the xray app uses ``224 × 224``). It belongs both in the ``inferer`` and in the
   ``spatial_shape`` field of ``metadata.json``.
#. **State the output semantics.** Number of channels and what each one means — for the spleen
   model, channel 0 is background and channel 1 is spleen. A MAP that mislabels its channels
   produces an inverted mask that looks entirely plausible.
#. **Check the exported bundle against a known input.** Run the same volume through your training
   validation path and through the MAP, and compare. Agreement is the only real evidence the
   transcription was faithful; a MAP that merely runs proves nothing about correctness.

If you are adapting a bundle from the MONAI Model Zoo, do not assume its preprocessing matches
yours even when the architecture is identical — as the spleen intensity window above demonstrates,
it may not.

.. warning::

   **Orientation transforms are the exception to "transcribe it verbatim".** Training and the MAP
   reach the pixels through different loaders, and the orientation step is the one that is
   calibrated to its loader rather than to the model.

   MONAI's ``LoadImaged`` returns a DICOM's pixel array **transposed** — indexed
   ``(column, row)``, where ``PixelData`` is ``(row, column)``. Training chains routinely carry a
   rotation that exists to undo that transpose. The MAP's ``DICOMSeriesToVolumeOperator`` does not
   transpose, so copying the rotation across applies a correction to something that was never
   wrong, and you end up with a differently-wrong orientation.

   The xray tutorial is a worked example of getting this wrong. It applied ``Rotate90d(k=-1)``
   after ``LoadImaged`` — which does produce an upright radiograph, because the loaded image is
   sideways. But a transpose composed with a rotation is algebraically a **mirror**, so the chain
   was training on left-right flipped radiographs: anatomically plausible, visually undetectable,
   and wrong. It now uses ``Transposed(keys=["image"], indices=(0, 2, 1))``, which undoes the
   loader and nothing more. Because both paths then agree on the image as DICOM stores it,
   ``map-apps/classification/classifier_operator.py`` needs no orientation transform at all.

   Derive yours empirically — dump both arrays for one study and search the eight rotation/flip
   combinations for the one that matches, as ``map-apps/classification/README.md`` describes. Use a
   non-square image: on a square one, a transpose and a rotation cannot be told apart by shape.
   Nothing about this failure is loud. The MAP runs, the SR is written, and the numbers are simply
   worse than they should be.


.. _map-package:

Step 4 — Package the MAP
========================

Application templates live in ``map-apps/`` — ``map-apps/segmentation`` for models that emit a
mask, ``map-apps/classification`` for models that emit labels. They are backend-agnostic: a MAP
consumes trained weights, not federated-learning configuration, so the same template serves an app
trained under either NVFLARE or Flower.

.. code-block:: bash

   holoscan package map-apps/segmentation \
       --config   map-apps/segmentation/app.yaml \
       --models   <bundle-dir>/model.ts \
       --tag      my_flip_map:latest \
       --platform x86_64 \
       --sdk      monai-deploy \
       --source   map-apps/holoscan-source.json \
       --uid $(id -u) --gid $(id -g)

Two flags are load-bearing and are not in the upstream instructions:

``--source``
   Supplies the base-image manifest locally. The packager otherwise fetches it from GitHub, where
   only one version's manifest is currently published.

``--uid`` / ``--gid``
   The packager installs Python dependencies with ``pip install --user``, while the runner
   launches the container as the *host* user. If those identities differ, every import inside the
   MAP fails.


Step 5 — Run the MAP
====================

.. code-block:: bash

   holoscan run my_flip_map-x64-workstation-dgpu-linux-amd64:latest \
       -i /path/to/dicom_series \
       -o ./output

The MAP writes a DICOM Segmentation object referencing the source series. Verify it rather than
trusting the exit code — check ``Modality``, the segment coding, and that
``ReferencedSeriesSequence`` names the input series and the frame of reference matches.


.. _map-view-result:

Step 6 — View the result
========================

A DICOM SEG is only useful if it overlays correctly on the source study. The lightest way to check
this is a standalone Orthanc with the OHIF viewer plugin:

.. code-block:: bash

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

Both output types go into Orthanc the same way, and both are grouped with their source study
automatically, because the MAP writes the source ``StudyInstanceUID`` into its output. Confirm
that grouping first — it is the single check that catches a mis-referenced result:

.. code-block:: bash

   curl -s http://localhost:8142/dicom-web/studies \
     | python3 -c "import json,sys; [print(s.get('00080061',{}).get('Value')) for s in json.load(sys.stdin)]"

A correctly-written result reports **both** modalities on one study — ``['CT', 'SEG']`` for
segmentation, ``['CR', 'SR']`` for classification. Two separate studies means the output did not
inherit the source identifiers.

How you inspect the result then depends on which kind it is.

Segmentation — view the overlay
-------------------------------

Open ``http://localhost:8142/ohif/viewer?StudyInstanceUIDs=<StudyInstanceUID>``. A correctly
written SEG appears as a selectable segmentation layer over the source series. This is worth doing
even when the tag checks pass: an inverted mask, a wrong label order or a half-slice offset all
produce valid DICOM and are obvious the moment you look at them.

Classification — read the report
--------------------------------

A DICOM SR carries no pixel data, so there is nothing to overlay and the viewer is the wrong tool.
Read the report content back instead — from Orthanc's REST API, or interactively through the
Orthanc Explorer 2 tag browser:

.. code-block:: bash

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

   OHIF will *list* the SR series but is unlikely to render it usefully. Its structured-report
   support is built around TID 1500 measurement reports — the kind carrying coordinates and
   measurements — rather than the Basic Text SR a classification MAP produces. That is a viewer
   limitation, not a defect in the output.

   If a study appears to be missing from the viewer entirely, check you are using **that study's**
   ``StudyInstanceUIDs`` rather than another one's, and note that OHIF's study list may apply a
   default date filter that hides older studies. Both are far more common than a genuine
   rendering failure. To confirm the image data itself is retrievable, fetch a frame directly —
   an ``HTTP 200`` with a plausible byte count means the viewer has everything it needs:

   .. code-block:: bash

      curl -s -o /dev/null -w "%{http_code} %{size_download}\n" \
        -H "Accept: multipart/related; type=application/octet-stream" \
        "http://localhost:8142/dicom-web/studies/<study>/series/<series>/instances/<instance>/frames/1"

Use a standalone Orthanc for this rather than a Trust's PACS — the point is to validate the
artefact, not to put research output into an operational archive.


.. _map-dicom-gap:

From NIfTI training to DICOM deployment
========================================

This is the largest conceptual gap in the whole path, and it is easy to miss because the tooling
hides it.

**FLIP training is NIfTI-shaped.** A trainer calls
``flip.get_by_accession_number(...)``, which defaults to ``ResourceType.NIFTI``. What arrives is a
single, already-converted file: one volume, in a known orientation, with spacing and geometry
resolved. The trainer never sees a DICOM instance, never chooses between series, and never has to
decide what "the image" is.

**Deployment is DICOM-shaped.** A MAP receives a *study* — a folder of loose DICOM instances,
possibly several series, in arbitrary order — and must return a DICOM object a PACS will accept
and a viewer will overlay correctly. Everything between those two states is work the training
pipeline never did.

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
     - **Chooses which series to run on**, by rule — modality, series description, study
       description. Training was handed the right data by the platform; a MAP must decide for
       itself, and a rule that matches nothing produces no output while still exiting cleanly.
   * - ``DICOMSeriesToVolumeOperator``
     - Sorts instances into slice order and assembles a volume with correct spacing and
       orientation. This is the step that reconstructs what the NIfTI already was.
   * - ``DICOMSegmentationWriterOperator``
     - Writes a DICOM SEG: segment coding, referenced series and instances, frame of reference.
       Training wrote arrays.
   * - ``DICOMTextSRWriterOperator``
     - The classification equivalent — a DICOM Structured Report. See
       :ref:`the classification section <map-classification>`.

Concepts that only exist on the deployment side
------------------------------------------------

Each of these is a place where a MAP can emit *valid* DICOM that is nonetheless wrong, and none
has an analogue in the training code:

* **Series selection rules.** Radiographs are ``CR`` or ``DX``, not ``CT``. A rule copied from
  another app silently selects nothing.
* **Frame of reference and referenced instances.** These are what let a viewer lay the result over
  the source. Wrong or absent, the output is a valid orphan.
* **SOP classes.** A segmentation is Segmentation Storage; a report is Basic Text SR. The writer
  operator sets these, but the choice of writer is yours.
* **Coded terminology.** Segments and findings carry SNOMED CT concepts. A mislabelled segment is
  indistinguishable from a correct one without reading the codes.
* **Character sets.** DICOM defaults to a restricted repertoire, so non-ASCII text is silently
  mangled — see the note in :ref:`the classification section <map-classification>`.

The practical consequence
--------------------------

**A model that trains perfectly can still deploy wrongly**, and the failure will not look like a
failure. The MAP will run, exit zero and write a well-formed DICOM object. This is why
:ref:`Step 6 <map-view-result>` insists on inspecting the artefact rather than trusting the exit
code, and why the overlay check is worth doing even when every tag is correct: geometry errors are
obvious to the eye and invisible to a tag dump.

It is also why the preprocessing transcription in :ref:`Step 3 <map-export-bundle>` matters so
much. Training's NIfTI arrived pre-resampled; at inference the bundle's ``Spacingd`` and
``Orientationd`` transforms are what reproduce that, from whatever geometry the source study
happens to have.


.. _map-classification:

Classification models produce a different artefact
==================================================

The pipeline above is segmentation-shaped. A classification model does not produce a
segmentation, so it cannot use ``DICOMSegmentationWriterOperator``. It instead emits a **DICOM
Structured Report** via ``DICOMTextSRWriterOperator``:

.. code-block:: text

   segmentation:    ... → MonaiBundleInferenceOperator → DICOMSegmentationWriterOperator  → DICOM SEG
   classification:  ... → <custom classifier operator> → DICOMTextSRWriterOperator        → DICOM SR

Consequences for app authors:

* **There is no bundle-driven inference operator that emits a label.**
  ``MonaiBundleInferenceOperator`` maps image in to image out, so a classification MAP needs a
  small custom operator that runs the scripted network and formats the result text. The SDK's
  ``breast_density_classifier_app`` is the closest worked example.
* **The series selection rule must match your modality.** Radiographs arrive as ``CR`` or ``DX``,
  not ``CT``; a rule copied from a segmentation app selects nothing and the application produces no
  output while still exiting successfully.
* **Match the activation to the loss the model was trained with.** The FLIP xray tutorial
  optimises binary cross-entropy over two independent labels, so the outputs are read through a
  sigmoid and thresholded individually. Applying a softmax instead — the natural assumption for a
  two-output network — would silently convert two independent findings into one mutually exclusive
  choice.
* **Keep DICOM SR text ASCII.** The SR writer does not set ``SpecificCharacterSet``, so values
  default to the ISO-IR 6 repertoire. A non-ASCII character such as an em dash or a degree sign is
  stored as ``?``. This is easy to miss because the application logs the original string correctly
  and only the stored DICOM instance is affected.
* **DICOM SR carries no pixel data**, so there is nothing to overlay. Verification means reading
  the report content back, not viewing it on the images:

  .. code-block:: python

     import pydicom

     ds = pydicom.dcmread("output/<sr-instance>.dcm")
     assert ds.Modality == "SR"
     for item in ds.ContentSequence:
         if item.ValueType == "TEXT":
             print(item.TextValue)


.. _map-environment-traps:

Environment traps
=================

These were all encountered while establishing the sequence above. None are documented upstream.

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Symptom
     - Cause and remedy
   * - ``nvidia-container-cli: unsatisfied condition: cuda>=13.0``
     - App SDK 4.x builds CUDA 13 MAPs, which need driver ≥580. Use SDK 3.5.0, or upgrade the
       driver.
   * - ``monai-deploy: command not found``
     - SDK 4.x ships no console scripts; packaging moved to the ``holoscan`` CLI. The 4.x
       documentation has not caught up.
   * - ``holoscan package`` has no ``--models`` flag
     - ``holoscan-cli`` 4.3+ dropped the MAP interface. Use ≤4.2 or 3.x.
   * - ``ManifestDownloadError ... 404``
     - The base-image manifest is fetched from GitHub and most versions are absent. Pass a local
       manifest with ``--source``.
   * - ``E: Version '3.12.3-*' for 'python3' was not found``
     - The CLI pins Ubuntu 24.04 package versions; the base image in the manifest must be an
       Ubuntu 24.04 image.
   * - ``ModuleNotFoundError`` inside the MAP
     - UID mismatch between build and run. Pass ``--uid``/``--gid``.
   * - ``undefined symbol: cudaGetDriverEntryPointByVersion``
     - ``monai<=1.5.0`` forces torch 2.6.0, whose bundled CUDA 12.4 runtime shadows the base
       image's and lacks the symbol holoscan needs. Add ``nvidia-cuda-runtime-cu12>=12.8``.
   * - ``no kernel image is available for execution on the device``
     - torch <2.7 has no ``sm_120`` support, so it cannot use a Blackwell GPU — while
       ``torch.cuda.is_available()`` still returns ``True``, so it fails late. Force a newer torch
       or run on CPU.


.. _map-config-enforcement:

What FLIP actually validates in ``config.json``
================================================

It is worth being explicit about this, because it is easy to assume more checking happens than
does — and because the checking that *does* happen is split across two services, at two different
moments.

**The Central Hub API reads exactly one key:** ``job_type``. It opens the uploaded ``config.json``
solely to decide which base application template to bundle, and validates that value against the
per-backend manifest of known job types (``bundle_nvflare_application`` /
``bundle_flower_application`` in ``flip-api/src/flip_api/fl_services/services/fl_service.py``). An
unrecognised ``job_type`` is rejected; a missing one falls back to ``standard`` — as does a missing
``config.json``, which is a valid submission for a Flower app.

**For NVFLARE, the FL API then validates a fixed set of platform keys** when it assembles the job
(``validate_config`` in ``fl-services/nvflare/fl-api-base/fl_api/utils/prepare_config.py``). Several
of those failures are hard rejections that stop the job rather than silent fallbacks: an unknown
``AGGREGATOR``, an ``AGGREGATION_WEIGHTS`` weight outside 0–1, an uncompilable
``AGGREGATE_ONLY_REGEX``, or ``BEST_MODEL_METRIC`` on a job that runs a single round. The full set,
with accepted values and defaults, is documented under :ref:`fl-training-configuration`.

**Everything outside that set is passed through untouched**, for your own trainer, validator and
``models.py`` to read at runtime. ``LEARNING_RATE``, ``VAL_SPLIT``, ``net_config`` and any key you
invent are neither validated nor defaulted — some shipped tutorials declare them and some do not,
because they are conventions of particular apps rather than platform requirements.

**What *is* enforced is file presence.** Each job type declares a required file list, and a
submission missing any of them fails fast with ``FileNotFoundError`` before anything is uploaded.
The required set differs per job type — see :ref:`fl-required-files` for the manifests.

The practical consequence for packaging is unchanged, and it is why this section exists: **a typo in
an export-related key would fail silently today.** An invented key sits outside the validated set by
definition, so nothing rejects the submission — the value is simply absent at runtime and your code
falls back to whatever default it defines. Any export configuration added to ``config.json`` in
future should come with validation, or it will inherit this behaviour.

.. note::

   Two traps hide in the gap between *validated* and *enforced*.

   ``LOCAL_ROUNDS`` **is** read by ``validate_config``, but rejecting it changes nothing you would
   notice. The default is written only when the key is *absent*, so a present-but-out-of-range value
   survives into the deployed ``config.json`` and reaches your trainer verbatim —
   ``"LOCAL_ROUNDS": 5000`` really does run 5000 local iterations.

   On the Flower path, the silent-typo behaviour inverts. Run-config overrides live in
   ``config.toml``, and ``flwr`` **rejects** any key the app's ``pyproject.toml`` does not already
   declare, failing the run at submission rather than ignoring it.

.. _map-open-questions:

The FLIP app contract for export
================================

Two decisions govern what an app author must provide in order for a trained model to be
packageable. Both are FLIP-side, independent of any deployment target.

**The input specification uses MONAI's schema.** A bundle needs input shape, voxel spacing,
intensity window, channel semantics and output labels. Rather than inventing a FLIP-specific
block, FLIP adopts the ``network_data_format`` block that MONAI already defines in a bundle's
``metadata.json``. It covers exactly this information, it is what the MONAI tooling reads, and a
FLIP-specific alternative would have to be translated into it anyway.

Worked examples live alongside the tutorials they describe, in
``fl-tutorials/<backend>/<tutorial>/export/``.

**Inference transforms are declared by convention, in** ``transforms.py``. There is no reliable
way to derive the inference chain automatically — a free-form Python module can build its
transforms however it likes, and any rule strong enough to make derivation safe would be more
restrictive than app authors can live with. FLIP therefore standardises on *where to look* rather
than attempting to enforce a signature: an app declares its transforms in ``transforms.py``, and
the person exporting the model transcribes the validation chain from there.

This is a convention, not a validated contract. Nothing enforces it, and function names differ
legitimately between apps — the spleen tutorial exposes ``get_val_transforms()``, the xray
tutorials ``get_xray_transforms(is_validation=True)``. See
:ref:`map-inference-transforms` for the transcription procedure and the failure mode it guards
against.

.. note::

   **MONAI Deploy Express is not currently a safe dependency.** The platform repository
   (``Project-MONAI/monai-deploy``) and its workflow manager have had no commits since March 2025,
   though the App SDK itself is actively maintained. For validating a MAP, the SDK plus a
   standalone Orthanc as described in :ref:`Step 6 <map-view-result>` is the lower-risk route.


.. _map-deepcos:

Deploying to deepcOS
====================

Everything above produces a MAP that runs standalone. **deepcOS — the intended production runtime
— requires more than that**, and this section records what, and what it changes.

deepc has `publicly established MONAI compatibility on deepcOS
<https://deepc.ai/insight/deepc-establishes-monai-compatibility-strengthening-its-commitment-to-open-source-collaboration-and-global-healthcare-transformation>`_,
and documents a MONAI integration path in their engine-build SDK documentation:

    https://docs.one.deepc.pro/engines/build/sdk/support/monai

.. important::

   That documentation is **access-controlled and commercially confidential**. It is the
   authoritative source and it is not reproduced here. This section records only the consequences
   for FLIP; anyone implementing against deepcOS must read the vendor documentation directly and
   treat it, not this page, as normative.

What is established
-------------------

**A plain MAP is not sufficient.** The application structure stays standard MONAI — the
``Application`` class is unchanged — but the inference operator must additionally import deepc's
own library and emit a structured *engine report* alongside its DICOM output. Integration
therefore happens **inside the operator**, which means the ``map-apps/`` templates here are the
inner layer of a deepcOS submission, not the submission itself.

**Results are reported as structured clinical findings, not as pixel data.** The engine report
follows the DICOM hierarchy — patient, study, series, instance — and each level can carry
analysis results. Those results take three forms: *findings* (a coded clinical concept, optionally
with a confidence score and a present/absent state), *quantities* (a numeric value with coded
units and an anatomical attribute), and *regions of interest* (a polygon of two-dimensional points
tied to one referenced DICOM instance).

Concepts are SNOMED CT or RadLex terms. Where no standard term exists, the vendor documents an
escape hatch for expressing a locally-defined concept, so terminology gaps do not block an
integration.

**DICOM output is carried, and the answer to the original question is yes.** An engine writes
whatever artefacts it needs into the configured output directory, and DICOM files placed there are
collected and forwarded onward automatically. A DICOM SEG is therefore a perfectly acceptable
output — the concern that prompted this investigation does not apply. The engine report is
discovered by filename convention rather than by configuration.

**But pixel output alone is not sufficient.** The vendor is explicit that where an engine encodes
its result in pixel data, that same information must *also* be expressed in machine-readable form
in the engine report, so it can be extracted downstream. **A DICOM SEG is exactly that case.**
Emitting the mask without a corresponding report entry would satisfy the letter of "DICOM is
forwarded" while withholding the content that makes it useful.

**This reframes the segmentation question rather than answering it with a format.** The enriched
output model has no dense mask representation — the closest construct is a per-instance polygon —
so the machine-readable half of a segmentation result is not the mask restated. It is the mask's
*derived clinical content*: a segmented volume expressed as a quantity with coded units and an
anatomical attribute, optionally with regions of interest for display. The SEG carries the pixels;
the report carries the meaning.

.. warning::

   The engine report includes a field for asserting an **all-negative** result — a clinically
   valid statement that no abnormality was found, which can drive automatic negative reporting
   downstream. The vendor reserves it for engines holding the appropriate regulatory clearance.

   A FLIP research model is neither CE-marked nor FDA-cleared, so **leave that field null**, which
   is what the vendor's own sample report does. This is not the same as being unable to express a
   negative: reporting a finding with an explicit *absent* state is the supported way to say "this
   model looked for X and did not find it", and carries none of the same clearance implications.
   The distinction is between reporting what the model found and certifying that a study is clear.

**The report is found by filename, and three of its fields are mandatory.** It is written into the
same output directory as the DICOM artefacts, as JSON or YAML, and is recognised by carrying
``deepcreport`` somewhere in its filename — matched case-insensitively, so
``spleen-deepcReport.json`` qualifies while ``report_deepc.json`` does not. The job status, the
coded messages and the engine result are all required.

Job status deserves attention rather than being set to success at the end. The vendor points out
that a process exit code does not reliably indicate whether the *analysis* succeeded — which is
exactly the failure mode described in :ref:`Step 6 <map-view-result>`, where a MAP whose series
selection matched nothing exits zero having produced no output. The coded messages field is the
place to surface that kind of condition rather than letting it pass silently.

A validator for completed engine responses is available in the vendor's tooling, and is worth
running before any submission rather than discovering a schema problem at the far end.

**The supported dependency set is older than the one this guide pins.** deepc's MONAI-compatible
variant targets an earlier MONAI Deploy App SDK and holoscan release, an earlier Python, and
pydicom 2.x, on a CUDA 12 runtime. The CUDA 12 direction taken here is right; the specific
versions are not. **Do not assume the stack pinned in this guide is deployable to deepcOS** — it
was chosen to work on a development workstation, not to match the vendor's supported matrix.

What this changes for FLIP
--------------------------

* **Two build profiles, not one.** The stack pinned on this page is a development target; a
  deepcOS submission needs its own. Keeping inference logic free of SDK-version-specific code
  makes that cheap — the ``map-apps/`` templates already do, because the model arrives as a
  self-contained TorchScript bundle.
* **Classification is close to mechanical.** ``map-apps/classification`` already produces exactly
  what a finding needs: a per-label probability and a present/absent decision against a threshold.
  The remaining work is assigning coded concepts to the FLIP label names, which is a clinical
  terminology task rather than an engineering one, and is not blocked by a term being missing.
* **The DICOM SEG stays.** It is what a viewer overlays, what :ref:`Step 6 <map-view-result>`
  verifies against, what any platform in the AIDE lineage consumes, and it is forwarded by
  deepcOS as-is. Nothing about the segmentation template's output needs to change.
* **Segmentation gains a derived quantity it does not currently compute.** The DICOM SEG writer
  emits pixels and stops, so a deepcOS submission would need the mask's clinical content as well —
  a segmented volume, which means voxel counting against the volume's spacing. That is a genuine
  addition, and a small one, but it is new work rather than a reformatting of something already
  produced.
* **Listing every finding the model can produce is worth doing, not just the positive ones.** The
  vendor notes that reporting each supported finding with an explicit present or absent state lets
  downstream systems accept or reject individual findings, generate free-text reports, and run
  performance analytics. For the xray classifier this means reporting both labels every time,
  which is what ``map-apps/classification`` already does internally.

Still to establish
------------------

* Runtime constraints: network and persistent-storage policy, model size limits, timeouts.
* Who defines the DICOM-tag routing rule that triggers an engine — deepc or the site.
* The research / non-CE-marked pathway. A FLIP research model is neither CE-marked nor
  FDA-cleared, so this governs whether it may run at all outside a sandbox.

Prior art: how AIDE consumed MAPs
---------------------------------

AIDE — the AI Deployment Engine that preceded deepcOS, built by this Centre — deployed AI
applications as MAPs, and its developer documentation is the closest thing to a specification we
have for how a MAP is actually consumed in a clinical platform. Three points carry over regardless
of what replaces it.

**A MAP was not the whole submission.** Applications were packaged as MAPs and then *wrapped in an
Argo Workflow template*, which declared the container's input and output directories and the
command to run. Anything targeting a platform in this lineage should expect the MAP to be one
layer of a larger deployment descriptor rather than the deliverable in itself.

**The application container had no network access and no persistent disk.** Applications ran in an
isolated, transient context; files had to be written to the declared output path rather than saved
anywhere else. A MAP that tries to reach a licence server, fetch weights at runtime, or cache to
disk will fail in that environment — worth designing out now.

**Input arrived as a study tree, not a flat folder**:

.. code-block:: text

   study_uid/
   ├─ series_uid/
   │  ├─ slice_uid.dcm
   │  └─ slice_uid_metadata.json

Outputs were then optionally routed to a **Clinical Review** step, where a clinician assessed them
before they were exported back to PACS. That review interface is a platform feature, not something
a MAP provides — the MAP's responsibility ends at emitting a well-formed DICOM object.

Notably, AIDE's documentation also recommends MONAI Deploy Express as the local test environment,
with Orthanc standing in for the Trust PACS — the same shape as the workflow described on this
page, which is some reassurance that the approach is not idiosyncratic.
