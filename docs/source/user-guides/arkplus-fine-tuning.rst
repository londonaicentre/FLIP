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

.. warning::

   This page assumes you are familiar with the common FLIP workflow described in
   :doc:`/user-guides/user-common` and you already have a FLIP account with the
   ``researcher`` role.

This guide walks through running **federated fine-tuning of an Ark+ Swin Transformer foundation
model** for chest X-ray classification on the FLIP platform. It serves as a complete worked
example of the FLIP lifecycle — from project creation through cohort query, model upload, training
and result download — that you can adapt for your own model. The application is the same one used
in the :doc:`platform-overhead experiment <reproducing-platform-overhead>`; that page covers the
reproduction protocol for the published results.

.. contents:: :local:
   :depth: 2

************
What it does
************

The Ark+ app fine-tunes a pre-trained Swin Transformer backbone on five common chest X-ray lesion
labels:

=========== ====================================
Head index  Lesion
=========== ====================================
0           Effusion
1           Consolidation
2           Infiltration
3           Lung Nodule or Mass
4           Pneumothorax
=========== ====================================

Only the classifier head is trained — the ~759 MiB backbone stays frozen after the initial
broadcast. The app uses NVIDIA FLARE's Client API and is defined entirely in Python via
``flip-utils``' ``FlipFedAvgRecipe``; there are no hand-written server or client JSON configs
beyond the user-supplied ``config.json``.

Key training characteristics:

* **50 global rounds × 5 local epochs**, batch size 4
* **Head-only parameter exchange** (~6,885 parameters ≈ 27 KB per round) — the backbone is
  broadcast once at round 0, then cached locally
* **Percentile privacy filter** (10th percentile, gamma 0.01) applied to head updates
* **Teacher–student EMA** available as an optional consistency regularisation
* **In-training validation** — every ``validate_every`` steps within each round, inside the
  trainer's ``flare.is_train()`` branch
* **Cross-site evaluation** — after training, the server's ``CrossSiteModelEval`` workflow
  dispatches a one-off validate task to each client (the trainer's ``flare.is_evaluate()``
  branch) so every site's final model is scored against every other site's data — a separate,
  post-training workflow, not something that runs every round

The full source lives under ``fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/``
in the FLIP repository. For detailed technical reference (model architecture, finetuning
mechanisms, ``job.py`` internals) see the
`tutorial README <https://github.com/londonaicentre/FLIP/blob/develop/fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/README.md>`_.

*************
Prerequisites
*************

To follow this guide you need:

* A FLIP account with the ``researcher`` role (see :ref:`initial-login`)
* Access to at least one FLIP net with the NVIDIA FLARE backend (the platform default)
* For local testing: a Linux machine with an NVIDIA GPU, `uv <https://docs.astral.sh/uv/>`_ on
  ``PATH``, ~15 GB free disk space, and internet access
* The Ark+ backbone checkpoint — requested via
  `this form <https://forms.gle/qkoDGXNiKRPTDdCe8>`_ (see
  `process_tools/README.md <https://github.com/londonaicentre/FLIP/blob/develop/fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/process_tools/README.md>`_)

.. _arkplus-dataset-setup:

**************
Dataset setup
**************

The app expects a DECAF-formatted chest X-ray dataset: a per-site CSV dataframe with
``accession_id`` and lesion-label columns, plus DICOM images. The quickest path uses the
published reference dataset on Hugging Face:

.. code-block:: bash

   git clone https://github.com/londonaicentre/FLIP.git
   cd FLIP
   make -C fl-tutorials download-arkplus-finetuning-data

This pulls the site-1 / site-2 training splits from
`aicentreflip/tutorials-arkplus-cxr-classification <https://huggingface.co/datasets/aicentreflip/tutorials-arkplus-cxr-classification>`_
and lays them out under ``fl-tutorials/nvflare/data/arkplus/`` (gitignored).

To use your own data instead, point the per-site ``.env.app`` values
(``SITE{1,2}_IMAGES_DIR`` / ``SITE{1,2}_DATAFRAME``) at your directories. The trainer selects a
client's data by ``flare.get_site_name()``; on the real platform the cohort dataframe comes from
``flip.get_dataframe(project_id, query)`` and DICOM images from
``flip.get_by_accession_number(...)`` — the :ref:`query.sql <arkplus-query-sql>` file in the app
defines the cohort.

.. _arkplus-local-testing:

*************************
Local testing (simulator)
*************************

Before deploying to FLIP, test the app locally with NVFLARE's simulator. This runs both clients
on one GPU, so it's useful for smoke-testing code changes and hyperparameter sweeps.

.. code-block:: bash

   # From the repo root — 3 rounds is the default for a quick smoke test
   make -C fl-tutorials download-arkplus-finetuning-data   # one-time
   make -C fl-tutorials run-tutorial TUTORIAL=arkplus_fine_tuning

   # 50-round full replica (hours on GPU)
   make -C fl-tutorials run-tutorial TUTORIAL=arkplus_fine_tuning NUM_ROUNDS=50

Or directly from the tutorial directory:

.. code-block:: bash

   cd fl-tutorials/nvflare/image_classification/arkplus_fine_tuning
   make run RAW_CHECKPOINT=/path/to/Ark6_swinLarge768_ep50.pth.tar
   make run NUM_ROUNDS=50   # override the default 3 rounds

The checkpoint is prepared automatically by ``make run`` (a no-op if ``pretrained_weights.pt``
already exists). On a multi-GPU host pick a device with ``CUDA_VISIBLE_DEVICES=<n>``.

.. _arkplus-submit-to-flip:

*******************
Submitting to FLIP
*******************

Once the app works locally, deploy it to the FLIP platform. The steps below mirror the
:doc:`common FLIP workflow </flip-workflow>` — follow along with the Ark+ app as the concrete
payload.

.. _arkplus-create-project:

1. Create a project
===================

#. Log in to the `FLIP UI <https://app.flip.aicentre.co.uk>`_
#. From the Projects page, click **New Project**
#. Name the project and add any collaborators
#. Note the project ID — you will need it for the cohort query

.. _arkplus-cohort-query:

2. Run the cohort query
=======================

In the project's **Cohort Query** tab, paste the SQL query that defines the patient cohort.
The Ark+ app ships with a reference query at ``query.sql`` in the tutorial directory:

.. _arkplus-query-sql:

.. code-block:: sql
   :caption: query.sql

   SELECT
       accession_id,
       CASE WHEN effusion_label = 'Yes' THEN 1 ELSE 0 END AS "Effusion",
       CASE WHEN consolidation_label = 'Yes' THEN 1 ELSE 0 END AS "Consolidation",
       CASE WHEN infiltration_label = 'Yes' THEN 1 ELSE 0 END AS "Infiltration",
       CASE WHEN lung_nodule_or_mass_label = 'Yes' THEN 1 ELSE 0 END AS "Lung Nodule or Mass",
       CASE WHEN pneumothorax_label = 'Yes' THEN 1 ELSE 0 END AS "Pneumothorax",
       CASE WHEN lungs_in_normal_arrangement_label = 'Yes' THEN 1 ELSE 0 END AS "Lungs in normal arrangement"
   FROM
       chest_xray_labels

This query expects OMOP tables with the five lesion columns plus the negative-override flag
(``Lungs in normal arrangement``). Adapt it to your own OMOP schema if you are using custom
data. Click **Run Query** to see cohort sizes at each trust; the results help you decide which
trusts to include.

.. _arkplus-stage-project:

3. Stage & approve the project
===============================

#. Select the trusts whose data you need
#. Click **Stage Project** — this triggers DICOM pull from each trust's PACS to the local XNAT
   cache
#. A FLIP administrator approves the project — this is required before any files can be uploaded
   or training can start

Image pull can take minutes to hours depending on cohort size. You are notified when it
completes.

.. _arkplus-upload-files:

4. Upload the model files
=========================

Navigate to the project's **Models** tab and create a new model. Upload the application files —
the minimum set the FLIP platform expects is listed in
``fl-apps/nvflare/standard_client_api/required_files.json`` in the repository (this app is a
``standard_client_api`` job type — the plain ``standard`` manifest also requires a
``validator.py`` this app doesn't have). For the Ark+ app the key files are:

* ``config.json`` — model configuration (lesion labels, training hyperparameters, Ark+
  architecture settings, finetuning controls)
* ``trainer.py`` — the Client-API training loop (frozen backbone, teacher/student EMA, AMP,
  per-lesion metrics)
* ``models.py`` / ``arkplus_flat_models.py`` — the model factory and Swin Transformer
  architecture
* ``data_utils.py`` — data loading, per-site resolution, DICOM parsing
* ``query.sql`` — the cohort SQL (used when running on the platform; not needed for local
  simulator testing)
* ``requirements.txt`` — any extra Python dependencies (the Ark+ model imports ``timm``)

The backbone checkpoint (``pretrained_weights.pt``, ~759 MiB) is uploaded separately — the
platform stages it server-side and broadcasts it to clients at round 0, so it never travels
in the client app bundle. Set ``SERVER_CHECKPOINT`` in ``config.json`` to the filename:

.. code-block:: json
   :caption: config.json (relevant keys)

   {
     "job_type": "standard_client_api",
     "SERVER_CHECKPOINT": "pretrained_weights.pt",
     "AGGREGATE_ONLY_REGEX": "omni_heads",
     "GLOBAL_ROUNDS": 50,
     "LOCAL_ROUNDS": 5,
     "BATCH_SIZE": 4,
     "USE_TEACHER_STUDENT": false
   }

.. _arkplus-start-training:

5. Start training
=================

Once all files are uploaded:

#. In the model page, click **Start Training**
#. The platform packages your app, deploys it to each selected trust, and begins the FL rounds
#. Monitor progress in the FLIP UI — the model status transitions through ``INITIATED`` →
   ``PREPARED`` → ``RUNNING`` → ``RESULTS_UPLOADED``
#. Per-round metrics (loss, per-lesion AUC) appear in the UI as training progresses

A 50-round run with two sites on the Ark+ app takes approximately **11–12 hours** wall-clock,
gated by the slowest client's GPU. The round-0 broadcast of the backbone checkpoint adds a
one-off ~60-minute staging cost; every subsequent round exchanges only the classifier head
(~27 KB).

.. _arkplus-results:

6. Download results
====================

When training completes (``RESULTS_UPLOADED``):

#. Navigate to the model page
#. Click **Download Results** to fetch the final aggregated model weights, per-round metrics,
   and training logs

The results bundle includes the trained classifier head weights, which you can load alongside
the frozen backbone for inference or for another round of fine-tuning.

.. _arkplus-adapting:

**********************************
Adapting this guide for your model
**********************************

The Ark+ app is a worked example of the general FLIP workflow. To run your own model:

#. **Start from a FLIP app template.** The templates under ``fl-apps/nvflare/`` in the
   repository are the platform's entry points — copy the one matching your job type and replace
   the model, trainer, and data-loading code with your own.

#. **Keep the FLIP SDK calls.** Your data-loading code must call ``flip.get_dataframe(...)`` to
   retrieve the cohort and ``flip.get_by_accession_number(...)`` to pull DICOM images (model
   status transitions are handled platform-side for Client-API apps — your trainer does not
   call ``flip.update_status`` itself). The Ark+ app's ``data_utils.py`` exercises both calls —
   use it as a reference.

#. **Use ``AGGREGATE_ONLY_REGEX`` for large frozen backbones.** If your model has a frozen
   backbone you want to broadcast once and then exclude from every round, set this key in
   ``config.json`` to a regex matching the trainable parameter names (the Ark+ app uses
   ``omni_heads``). This is the single most impactful optimisation for large models.

#. **Test locally with the simulator first.** Run ``make -C fl-tutorials run-tutorial`` or use
   NVFLARE's ``SimulatorRunner`` directly from your own script — catch configuration and
   shape-mismatch issues before they cost a platform round-trip.

#. **Upload, train, iterate.** The FLIP UI's model lifecycle is designed for multiple attempts:
   you can upload a new version of your files and re-train under the same project and cohort
   without re-running the query or re-pulling images.

.. seealso::

   :doc:`/working-with-flip-apps/create-flip-app-from-flare`
      How to adapt a stock NVIDIA FLARE app for FLIP (SDK calls, app layout).

   :doc:`reproducing-platform-overhead`
      Step-by-step protocol to reproduce the platform-overhead experiment published with this app.
