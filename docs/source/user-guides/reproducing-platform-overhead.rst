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

.. _reproducing-platform-overhead:

=======================================================
Reproducing the FLIP platform-overhead experiment
=======================================================

.. note::

   This page describes the reproduction protocol for the FLIP platform-overhead
   experiment. A link to the published paper will be added here when it becomes
   available. The experiment itself uses the :doc:`Ark+ fine-tuning app <arkplus-fine-tuning>`
   — read that guide first if you are unfamiliar with the application.

This guide walks through reproducing the platform-overhead measurement that quantifies how much
wall-clock time the FLIP orchestration layer adds to federated training versus bare-metal local
execution. It is both a concrete reproduction protocol and a worked example of how to extract and
analyse round-level timing metrics from any FLIP run — the same approach works for your own
models.

.. contents:: :local:
   :depth: 2

**********
Background
**********

The experiment compares two runs of the **identical** federated-learning application
(:ref:`Ark+ fine-tuning <arkplus-fine-tuning>`) under two execution regimes:

=============  ========================================================  ======================================================
               **Platform run**                                          **Local simulator replica**
=============  ========================================================  ======================================================
Where           FLIP production, cross-continental (UK + Thailand)       Single machine, NVIDIA RTX 5090, both clients on one GPU
Rounds          50 global rounds × 5 local epochs                        50 global rounds × 5 local epochs
Round payload   Head-only after round 0 (~27 KB)                        Head-only after round 0 (~27 KB)
Source          CloudWatch logs from the production ECS fl-server         NVFLARE SimEnv server log
=============  ========================================================  ======================================================

Three host-independent metrics isolate the platform's contribution from site compute and WAN
transfer:

* **Aggregation time** — how long the server spends merging client weight updates each round
* **Inter-round gap** — idle time between one round ending and the next beginning
* **Round 0 initialisation** — the one-off cost of staging the backbone checkpoint and
  broadcasting the full model to all clients

The steady-state round duration (rounds ≥ 1) bundles site GPU time and WAN transfer — it is
**not** a clean platform-overhead reading on its own, but the simulator replica provides a
baseline that lets you separate hardware from orchestration.

.. _overhead-prerequisites:

*************
Prerequisites
*************

To reproduce the full experiment you need access to both the live FLIP platform and a local
GPU machine:

===========================  =====================================================================
Requirement                  Purpose
===========================  =====================================================================
FLIP account (``researcher``) Submit and monitor the platform run
AWS prod credentials         Extract round metrics from CloudWatch (``extract_model_metrics.sh``)
Linux + NVIDIA GPU + ``uv``  Run the local simulator replica
~15 GB free disk             Tutorial dataset (~6.3 GB) plus simulator output
Ark+ backbone checkpoint      Request via `this form <https://forms.gle/qkoDGXNiKRPTDdCe8>`_
Internet access              Hugging Face dataset download, AWS API calls
===========================  =====================================================================

If you only want to contribute a **simulator data point** (e.g., your site's GPU baseline for
the multi-site comparison), you only need the GPU, ``uv``, and internet — skip the platform and
CloudWatch steps.

.. _overhead-simulator:

***********************************
Step 1: Run the local simulator
***********************************

The simulator replica uses the same ``arkplus_fine_tuning`` tutorial that ships with the FLIP
repository. A single ``make`` target chains the full pipeline — data download, checkpoint
preparation, 50-round simulation, metric extraction, and bundling:

.. code-block:: bash

   git clone https://github.com/londonaicentre/FLIP.git
   cd FLIP/fl-tutorials/nvflare/image_classification/arkplus_fine_tuning
   make experiment

This produces a timestamped bundle in the tutorial directory:

.. code-block:: text

   arkplus_sim_experiment_<hostname>_<timestamp>.zip

The bundle contains:

* ``rounds.tsv`` — per-round timing table (round number, duration, aggregation time, gap)
* ``summary.md`` — human-readable statistics (mean ± std, totals)
* ``round_timings_boxplot.png`` — visual overview of per-round timing
* ``experiment.log`` — the full simulator server log (model files excluded; metrics-only)
* ``provenance/config.json`` — the app configuration used
* ``provenance/.env.app`` — per-site data paths and environment
* ``provenance/host_info.txt`` — GPU model, driver version, OS, git commit

If you already ran the simulator and only need to re-bundle (e.g., the simulation completed but
packing failed), use ``make package`` instead — it re-packs the last run without re-simulating.

On a multi-GPU host pick a device with ``CUDA_VISIBLE_DEVICES=<n> make experiment``. You can
change the round count from the default 50 with ``EXPERIMENT_ROUNDS=<n>`` if you are testing.

.. _overhead-metrics:

The extracted metrics are also available standalone under ``model_metrics/`` at the repo root:

.. code-block:: bash

   make run NUM_ROUNDS=50            # just simulate (skip bundling)
   make metrics                      # extract rounds.tsv + summary from the server log

.. _overhead-platform-run:

************************************
Step 2: Run on the FLIP platform
************************************

Follow the :ref:`Ark+ submission guide <arkplus-submit-to-flip>` to create a project, run the
cohort query, upload the app files, and start training on the live platform. In brief:

#. **Create a project** in the FLIP UI
#. **Paste the cohort SQL** from ``query.sql`` and run it against each trust
#. **Select trusts** and stage the project for administrator approval
#. **Upload the app files** — ``config.json``, ``trainer.py``, ``models.py``,
   ``arkplus_flat_models.py``, ``data_utils.py``, ``query.sql``, ``requirements.txt``, and the
   backbone checkpoint as a separate file
#. **Start training** — the model transitions through ``INITIATED`` → ``PREPARED`` →
   ``TRAINING_STARTED`` → ``RESULTS_UPLOADED``

.. note::

   If you already have a completed platform run and only need to extract its metrics, skip to
   :ref:`Step 3 <overhead-extract>` — you only need the model ID from the FLIP UI.

.. _overhead-extract:

**************************************************
Step 3: Extract platform metrics from CloudWatch
**************************************************

The platform run's round-level timing is logged by the FL server (``fl-server``) to CloudWatch.
The extraction script ``scripts/extract_model_metrics.sh`` pulls these log lines and writes the
same artefacts the simulator produces (``rounds.tsv``, ``summary.md``, boxplot):

.. code-block:: bash

   cd /path/to/FLIP

   # One-time: authenticate with the AWS production account
   aws sso login --profile prod

   # Extract metrics for a specific model ID (from the FLIP UI model page)
   ./scripts/extract_model_metrics.sh <model-id> <output-directory>

   # Example
   ./scripts/extract_model_metrics.sh 24985ec3-3349-435b-afcd-f38972d8695d model_metrics/24985ec3-3349-435b-afcd-f38972d8695d

The script queries CloudWatch Logs for the time window covering the model's lifecycle (it reads
the model's ``created_at`` timestamp from the FLIP API to bound the search), extracts
``ScatterAndGather`` controller events from the ``fl-server`` log group, and writes:

* ``rounds.tsv`` — per-round timing (round number, duration in seconds, aggregation time, gap)
* ``summary.md`` — statistics (mean, std, min, max, total span) and a timeline table
* ``round_timings_boxplot.png`` — side-by-side boxplots of round duration, aggregation, and gap
* ``failures.tsv`` — any error, retry, or warning lines found in the time window (useful for
  diagnosing runs that didn't complete)

The script requires ``aws`` CLI with the ``prod`` profile and ``jq``. It also calls the FLIP
API to look up the model — set ``FLIP_API_BASE`` to the production URL if not already in your
environment.

.. _overhead-compare:

********************************
Step 4: Compare the two runs
********************************

With both ``rounds.tsv`` files in hand, pass the platform file to the simulator's ``make
metrics`` target:

.. code-block:: bash

   cd fl-tutorials/nvflare/image_classification/arkplus_fine_tuning
   make metrics COMPARE=../../../../model_metrics/24985ec3-3349-435b-afcd-f38972d8695d/rounds.tsv

The output ``summary.md`` in ``model_metrics/simulator-<workspace>-<timestamp>/`` now includes a
side-by-side comparison table with platform − simulator deltas for every metric, plus an
interpretation section.

.. _overhead-interpreting:

*********************
Interpreting results
*********************

A clean comparison looks like this (actual numbers from the UK + Thailand production run vs.
UK RTX 5090 simulator, July 2026):

=============================  ================  ===============  ==========================
Metric                          Platform (s)      Simulator (s)    Δ (platform − simulator)
=============================  ================  ===============  ==========================
Round duration (steady-state)   745.78 ± 0.74     275.36 ± 0.47    **+470.42**
Aggregation                     0.188 ± 0.064     0.220 ± 0.048    −0.03
Inter-round gap                 0.186 ± 0.016     0.207 ± 0.015    −0.02
Round 0 (initialisation)        3,829.99          294.66           +3,535.33
Total span                      40,382            13,797           +26,585
=============================  ================  ===============  ==========================

Three findings to read from the table:

1. **Server-side platform overhead is effectively zero.** Aggregation and inter-round gap —
   the two host-independent metrics — are sub-second and statistically indistinguishable
   between the production ECS ``fl-server`` and the local simulator. The FLIP orchestration
   layer adds no measurable per-round cost on the server path.

2. **The steady-state round-duration delta is site compute, not platform overhead.** The
   simulator packs both clients onto one GPU, serialising their work (275 s ≈ 2 × 135 s per
   client). The platform rounds are gated by the slowest client's GPU (in this case the
   Thailand site, ~745 s). With head-only updates (~27 KB) WAN transfer contributes
   negligible time — corroborated by the round duration's extreme regularity (± 0.74 s over
   49 cross-continental rounds).

3. **Round 0 is a one-off communication cost.** The platform round 0 (+3,535 s) covers staging
   the 795 MB backbone checkpoint to S3 and broadcasting the full model to every client over
   the WAN. After round 0, ``TrimBroadcastVars`` / ``ReconstructFullModel`` shrink every
   subsequent broadcast to the classifier head. This cost is paid once per training run,
   regardless of the number of rounds.

.. _overhead-adapting:

***************************************
Adapting for your own model
***************************************

The same extraction-and-compare workflow works for any FLIP run — not just the Ark+ experiment:

#. **Run your model on FLIP** as usual through the UI
#. **Run the same model in the local simulator** — use the tutorial harness or NVFLARE's
   ``SimulatorRunner`` directly, matching the deployed ``config.json`` round count
#. **Extract platform metrics** with ``extract_model_metrics.sh <your-model-id>``
#. **Compare** with ``make metrics COMPARE=/path/to/platform/rounds.tsv``

The comparison will show your model's platform overhead profile:

* **Aggregation time** scales with the number of trainable parameters you exchange each round
* **Inter-round gap** is typically sub-second regardless of model size — it reflects FLIP's
  orchestration polling loop, not model weight transfer
* **Round 0** bundles your checkpoint size (S3 upload + WAN broadcast); use
  ``AGGREGATE_ONLY_REGEX`` in ``config.json`` if you have a large frozen backbone to broadcast
  once
* **Steady-state round duration** on the platform is gated by your slowest participating site's
  GPU time plus WAN transfer for the per-round payload

.. _overhead-third-site:

***********************************************
Adding a third site's simulator results
***********************************************

The experiment is designed for multi-site hardware comparison. A collaborating site reproduces
the simulator baseline on their own GPU with a single command:

.. code-block:: bash

   git clone https://github.com/londonaicentre/FLIP.git
   cd FLIP/fl-tutorials/nvflare/image_classification/arkplus_fine_tuning
   make experiment

They send back the ``arkplus_sim_experiment_<host>_<timestamp>.zip`` bundle. To fold it into the
comparison, extract the zip and pass its ``rounds.tsv`` as a second comparison point:

.. code-block:: bash

   unzip arkplus_sim_experiment_bdms-gpu-1_20260714T120000.zip -d /tmp/bdms-sim
   make metrics COMPARE=/path/to/platform/rounds.tsv \
                 COMPARE_2=/tmp/bdms-sim/model_metrics/simulator-*/rounds.tsv

The provenance folder in each bundle (GPU model, driver, OS, git commit) ensures you can
attribute per-site deltas to hardware rather than software differences.

.. seealso::

   :doc:`arkplus-fine-tuning`
      The full Ark+ application guide — project setup, cohort query, file upload, and training.

   `arkplus_fine_tuning README <https://github.com/londonaicentre/FLIP/blob/develop/fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/README.md>`_
      Technical reference for the app internals (model architecture, finetuning mechanisms, ``job.py``).

   `scripts/extract_model_metrics.sh <https://github.com/londonaicentre/FLIP/blob/develop/scripts/extract_model_metrics.sh>`_
      The CloudWatch extraction script used in Step 3.
