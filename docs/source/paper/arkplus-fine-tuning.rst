..  Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

.. _arkplus-fine-tuning:

============================================
Ark+ chest X-ray classification on FLIP
============================================

.. admonition:: Before you start
   :class: note

   You need a FLIP account with the ``researcher`` role, and should know the common FLIP workflow in
   :doc:`/user-guides/user-common`.

This guide runs **federated fine-tuning of an Ark+ Swin Transformer foundation model** for chest X-ray classification
on FLIP. It is a complete worked example of the FLIP lifecycle — project, cohort query, model upload, training and
results — that you can adapt for your own model. The same app is used in the
:doc:`platform-overhead experiment <reproducing-platform-overhead>`; both are part of :ref:`the FLIP paper <paper>`.

.. contents:: :local:
   :depth: 2

************
What it does
************

The app fine-tunes a pre-trained Swin Transformer backbone on five chest X-ray lesion labels:

=========== ====================================
Head index  Lesion
=========== ====================================
0           Effusion
1           Consolidation
2           Infiltration
3           Lung Nodule or Mass
4           Pneumothorax
=========== ====================================

Only the **classifier head** is trained; the ~759 MiB backbone stays frozen. The app uses NVIDIA FLARE's Client API
and is built with ``flip-utils``' ``FlipFedAvgRecipe``, so ``config.json`` is the only configuration file you edit.

Training at a glance:

* **50 global rounds × 5 local epochs**, batch size 4.
* **Head-only exchange** (~6,885 parameters ≈ 27 KB per round). The backbone is sent once at round 0 and cached on
  each client.
* **Percentile privacy filter** (10th percentile, gamma 0.01) on head updates.
* **Teacher–student EMA**, optional, for consistency regularisation.
* **In-training validation** every ``VALIDATE_EVERY`` local epochs within each round.
* **Final evaluation** once training ends: each site scores the final aggregated model on its own data. This runs
  once, not every round.

The source is in ``fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/``. For the model architecture,
fine-tuning mechanisms and ``job.py`` see the
:gh:`tutorial README <fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/README.md>`.

*************
Prerequisites
*************

* A FLIP account with the ``researcher`` role (see :ref:`initial-login`).
* Access to at least one FLIP net running NVIDIA FLARE (the platform default).
* For local testing: Linux with an NVIDIA GPU, `uv <https://docs.astral.sh/uv/>`_ on your ``PATH``, ~15 GB free disk
  and internet access.
* The Ark+ backbone checkpoint, requested via `this form <https://forms.gle/qkoDGXNiKRPTDdCe8>`_ (see
  :gh:`process_tools/README.md <fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/process_tools/README.md>`).

.. _arkplus-dataset-setup:

**************
Dataset setup
**************

The app expects a DECAF-formatted chest X-ray dataset: a CSV per site with ``accession_id`` and lesion-label columns,
plus the DICOM images. The quickest start is the published reference dataset:

.. code-block:: shell

   git clone https://github.com/londonaicentre/FLIP.git
   cd FLIP
   make -C fl-tutorials download-arkplus-finetuning-data

This downloads the site-1 and site-2 training splits from
`aicentreflip/tutorials-arkplus-cxr-classification <https://huggingface.co/datasets/aicentreflip/tutorials-arkplus-cxr-classification>`_
into ``fl-tutorials/data/arkplus/``.

To use your own data, set the per-site values in ``.env.app`` — ``SITE1_IMAGES_DIR``, ``SITE1_DATAFRAME``,
``SITE2_IMAGES_DIR``, ``SITE2_DATAFRAME`` — to your directories.

On the platform, the app gets the cohort from ``flip.get_dataframe(project_id, query)`` and the images from
``flip.get_by_accession_number(...)``. The :ref:`query.sql <arkplus-query-sql>` file defines the cohort.

.. _arkplus-local-testing:

*************************
Local testing (simulator)
*************************

Test the app locally before deploying it. FLARE's simulator runs both clients on one GPU, which is enough to
smoke-test code changes and try hyperparameters.

From the repository root:

.. code-block:: shell

   make -C fl-tutorials download-arkplus-finetuning-data   # one-time
   make -C fl-tutorials run-tutorial TUTORIAL=arkplus_fine_tuning   # quick smoke test: 3 rounds, 128 studies per site

   # Full 50-round replica on the whole dataset (hours on a GPU)
   make -C fl-tutorials run-tutorial TUTORIAL=arkplus_fine_tuning NUM_ROUNDS=50 MAX_SAMPLES=0

Or from the tutorial directory:

.. code-block:: shell

   cd fl-tutorials/nvflare/image_classification/arkplus_fine_tuning
   make run RAW_CHECKPOINT=/path/to/Ark6_swinLarge768_ep50.pth.tar
   make run NUM_ROUNDS=50   # override the default 3 rounds
   make run MAX_SAMPLES=0   # train on every study instead of 128 per site

Simulator options:

* ``MAX_SAMPLES`` caps the studies each simulated site reads, using a fixed, class-balanced subset so validation and
  per-lesion AUCs still work. It applies **only to the simulator**; a job on FLIP trains on the full cohort.
* The number of local epochs per round comes from ``LOCAL_ROUNDS`` in ``config.json``, as on the platform.
* ``make run`` prepares the checkpoint automatically, and skips this if ``pretrained_weights.pt`` already exists.
* On a multi-GPU host, choose a device with ``CUDA_VISIBLE_DEVICES=<n>``.

.. _arkplus-submit-to-flip:

*******************
Submitting to FLIP
*******************

Once the app works locally, deploy it to FLIP. The steps follow the :doc:`common FLIP workflow </flip-workflow>`, with
the Ark+ app as the payload.

.. _arkplus-create-project:

1. Create a project
===================

#. Log in to the `FLIP UI <https://app.flip.aicentre.co.uk>`_.
#. On the Projects page, click **Create project**.
#. Name the project and add any collaborators.

.. _arkplus-cohort-query:

2. Run the cohort query
=======================

On the project page, click **Create Cohort Query** and paste the SQL that defines the cohort. The app ships a
reference query as ``query.sql`` in the tutorial directory:

.. _arkplus-query-sql:

.. literalinclude:: ../../../fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/query.sql
   :language: sql
   :caption: query.sql
   :lines: 12-

The query expects the five lesion columns plus the negative-override flag ``Lungs in normal arrangement``. Adapt it to
your own OMOP schema if you use custom data. Click **Run on all trusts** to see the cohort size at each trust, then decide
which trusts to include.

.. _arkplus-stage-project:

3. Stage & approve the project
===============================

#. Select the trusts whose data you need.
#. Click **Stage Project**. This locks the project.
#. Each staged trust's Trust Admin approves the project, or a FLIP administrator for a trust that has none. The
   project stays locked after approval.

**Approval starts the image pull**: each approving trust copies the cohort's DICOM studies from its PACS into its local
XNAT. You cannot upload files or start training before approval. The pull takes minutes to hours depending on cohort
size; follow it in the :ref:`imaging status <imaging-status>` on the project page.

.. _arkplus-upload-files:

4. Upload the model files
=========================

On the project page, click **Create Model** in the Models card (or open the **Models** page, filter it to the project
and create the model there). This app is a ``standard`` job (the Client-API template, which needs no
``validator.py``); the platform's minimum file set for it is listed in ``fl-apps/nvflare/standard/required_files.json``.

Upload these files:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - File
     - Contents
   * - ``config.json``
     - Model configuration: lesion labels, training hyperparameters, Ark+ architecture and fine-tuning settings.
   * - ``trainer.py``
     - The Client-API training loop (frozen backbone, teacher/student EMA, AMP, per-lesion metrics).
   * - ``models.py``, ``arkplus_flat_models.py``
     - The model factory and the Swin Transformer architecture.
   * - ``data_utils.py``
     - Data loading, per-site resolution and DICOM parsing.
   * - ``loss_and_metrics.py``
     - The loss and per-lesion metrics.
   * - ``meta.json``
     - Optional. The GPU request for each site.

Upload the backbone checkpoint (``pretrained_weights.pt``, ~759 MiB) **as a separate file**. The platform stages it
on the server and sends it to the clients at round 0, so it is never part of the client app bundle. Set
``SERVER_CHECKPOINT`` in ``config.json`` to its filename:

.. code-block:: json
   :caption: config.json (relevant keys)

   {
     "job_type": "standard",
     "SERVER_CHECKPOINT": "pretrained_weights.pt",
     "AGGREGATE_ONLY_REGEX": "omni_heads",
     "GLOBAL_ROUNDS": 50,
     "LOCAL_ROUNDS": 5,
     "BATCH_SIZE": 4,
     "ARKPLUS": {
       "USE_TEACHER_STUDENT": false
     }
   }

.. _arkplus-start-training:

5. Start training
=================

#. On the model page, click **Initiate Training**.
#. The platform packages your app, deploys it to each selected trust and starts the rounds.
#. Follow progress in the FLIP UI. The status moves through **Model Queued** → **Model Prepared** → **Running** →
   **Results Uploaded**.
#. Per-round metrics (loss, per-lesion AUC) appear in the UI as training runs.

A 50-round, two-site run takes about **11–12 hours**, set by the slowest client's GPU. Round 0 adds a one-off
~60 minutes to send the backbone; every later round exchanges only the ~27 KB classifier head.

.. _arkplus-results:

6. Download results
====================

When the status reaches **Results Uploaded**, open the model page and click **Download Results**. You get the final
aggregated weights, per-round metrics and training logs.

The trained classifier head can be loaded with the frozen backbone for inference or for further fine-tuning.

.. _arkplus-adapting:

**********************************
Adapting this guide for your model
**********************************

#. **Start from a FLIP app template.** Copy the template in ``fl-apps/nvflare/`` that matches your job type, then
   replace the model, trainer and data-loading code with your own.

#. **Keep the FLIP SDK calls.** Load the cohort with ``flip.get_dataframe(...)`` and the DICOM images with
   ``flip.get_by_accession_number(...)``. The Ark+ ``data_utils.py`` shows both. You do not call
   ``flip.update_status`` yourself; the platform handles status changes for Client-API apps.

#. **Set** ``AGGREGATE_ONLY_REGEX`` **for large frozen backbones.** Set it in ``config.json`` to a regex matching the
   trainable parameter names (Ark+ uses ``omni_heads``). The backbone is then sent once and left out of every later
   round — the single biggest saving for large models.

#. **Test locally first.** Run ``make -C fl-tutorials run-tutorial``, or FLARE's ``SimulatorRunner`` from your own
   script, to catch configuration and shape errors before they cost a platform run.

#. **Upload, train, iterate.** You can upload new versions of your files and re-train in the same project without
   re-running the query or re-pulling images.

.. seealso::

   :doc:`/working-with-flip-apps/create-flip-app-from-flare`
      How to adapt a stock NVIDIA FLARE app for FLIP (SDK calls, app layout).

   :doc:`reproducing-platform-overhead`
      Step-by-step protocol to reproduce the platform-overhead experiment published with this app.
