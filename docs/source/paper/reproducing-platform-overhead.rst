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

   This experiment is part of :ref:`the FLIP paper <paper>`. It uses the
   :doc:`Ark+ fine-tuning app <arkplus-fine-tuning>` — read that guide first if you are new to the app.

This guide reproduces the measurement of how much wall-clock time FLIP's orchestration adds to federated training,
compared with running the same app locally. It also works as a general recipe for extracting and comparing round-level
timings from any FLIP run, including your own models.

.. contents:: :local:
   :depth: 2

**********
Background
**********

The experiment runs the **identical** app (:ref:`Ark+ fine-tuning <arkplus-fine-tuning>`) in two ways:

=============  ==================================================  ========================================================
               **Platform run**                                    **Local simulator replica**
=============  ==================================================  ========================================================
Where          FLIP production, cross-continental (UK + Thailand)  Single machine, NVIDIA RTX 5090, both clients on one GPU
Rounds         50 global rounds × 5 local epochs                   50 global rounds × 5 local epochs
Round payload  Head-only after round 0 (~27 KB)                    Head-only after round 0 (~27 KB)
Source         CloudWatch logs from the production FL server       FLARE simulator server log
=============  ==================================================  ========================================================

Three metrics isolate the platform's own cost from site compute and network transfer:

* **Aggregation time** — how long the server takes to merge client updates each round.
* **Inter-round gap** — idle time between one round ending and the next starting.
* **Round 0 initialisation** — the one-off cost of staging the backbone checkpoint and sending the full model to every
  client.

The steady-state round duration (rounds 1 onwards) includes site GPU time and network transfer, so on its own it is
**not** a measure of platform overhead. The simulator replica gives the hardware baseline to compare it against.

.. _overhead-prerequisites:

*************
Prerequisites
*************

=============================  ====================================================================
Requirement                    Purpose
=============================  ====================================================================
FLIP account (``researcher``)  Submit and monitor the platform run
AWS production credentials     Extract round metrics from CloudWatch (Step 3)
Linux + NVIDIA GPU + ``uv``    Run the local simulator replica
~15 GB free disk               Tutorial dataset (~6.3 GB) plus simulator output
Ark+ backbone checkpoint       Request via `this form <https://forms.gle/qkoDGXNiKRPTDdCe8>`_
Internet access                Hugging Face dataset download, AWS API calls
=============================  ====================================================================

.. tip::

   To contribute only a **simulator data point** (for example your site's GPU baseline), you need just the GPU,
   ``uv`` and internet access. Do Step 1 and skip the rest.

.. _overhead-simulator:

***********************************
Step 1: Run the local simulator
***********************************

One ``make`` target runs the whole local pipeline: dataset download, checkpoint preparation, a 50-round simulation,
metric extraction and bundling.

.. code-block:: shell

   git clone https://github.com/londonaicentre/FLIP.git
   cd FLIP/fl-tutorials/nvflare/image_classification/arkplus_fine_tuning
   make reproduce-overhead

When it finishes, the tutorial directory holds a bundle named
``arkplus_sim_experiment_<hostname>_<timestamp>.zip``. It contains:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - File
     - Contents
   * - ``rounds.tsv``
     - Per-round timings: round number, duration, aggregation time, gap.
   * - ``summary.md``
     - Readable statistics (mean ± std, totals).
   * - ``round_timings_boxplot.png``
     - Box plots of the per-round timings.
   * - ``logs/``
     - The simulator's logs; any log of 64 MiB or more is left out. No model files.
   * - ``provenance/config.json``
     - The app configuration used.
   * - ``provenance/env.app.txt``
     - Per-site data paths and environment (a copy of ``.env.app``).
   * - ``provenance/host_info.txt``
     - GPU model, driver version, OS and git commit.

Useful variations:

* **Pick a GPU** on a multi-GPU host: ``CUDA_VISIBLE_DEVICES=<n> make reproduce-overhead``.
* **Change the round count** for testing: ``make reproduce-overhead OVERHEAD_ROUNDS=<n>`` (default 50).
* **Re-pack without re-simulating**, for example if the simulation finished but bundling failed:
  ``make package-overhead-bundle``.

.. _overhead-metrics:

To simulate and extract metrics without building a bundle, run the two steps separately. The metrics land in the
tutorial's ``round_metrics/`` directory.

.. code-block:: shell

   make run NUM_ROUNDS=50   # simulate only
   make round-metrics       # write rounds.tsv and summary.md from the server log

.. _overhead-platform-run:

************************************
Step 2: Run on the FLIP platform
************************************

Follow the :ref:`Ark+ submission guide <arkplus-submit-to-flip>`. In brief:

#. **Create a project** in the FLIP UI.
#. **Paste the cohort SQL** from ``query.sql`` and run it against each trust.
#. **Select trusts** and stage the project; each trust's Trust Admin (or a FLIP administrator, for a trust with none)
   approves it.
#. **Upload the app files** — ``config.json``, ``trainer.py``, ``models.py``, ``arkplus_flat_models.py``,
   ``data_utils.py``, ``loss_and_metrics.py`` and optionally ``meta.json`` — and the backbone checkpoint as a
   separate file.
#. **Start training.** The model status moves through **Model Queued** → **Model Prepared** → **Running** →
   **Results Uploaded**.

.. note::

   If you already have a finished platform run, skip to :ref:`Step 3 <overhead-extract>`. You only need the model's
   page URL from the FLIP UI.

.. _overhead-extract:

**************************************************
Step 3: Extract platform metrics from CloudWatch
**************************************************

The FL server logs each round's timing to CloudWatch. The extraction script reads those logs and writes the same files
the simulator produces. It needs the ``aws`` CLI and ``jq``.

.. code-block:: shell

   cd /path/to/FLIP
   aws sso login --profile prod   # one-time: sign in to the AWS production account

   # Pass the model's page URL from the FLIP UI, not a bare ID
   ./scripts/fl_round_metrics/extract_platform_round_timings.sh '<flip-ui-model-url>' -o <output-directory>

For example:

.. code-block:: shell

   ./scripts/fl_round_metrics/extract_platform_round_timings.sh \
       'https://app.flip.aicentre.co.uk/project/f48850b7-3101-46d1-8fa7-a00bf01d2597/model/24985ec3-3349-435b-afcd-f38972d8695d' \
       -o round_metrics

The script finds the run's time window itself by searching the last 7 days of hub logs for the model ID (CloudWatch
keeps 365 days in production, 90 elsewhere). To search further back use ``--search-days``; to set the window yourself pass ``--start`` and ``--end``
(ISO 8601).

It writes these files under ``<output-directory>/<model_id>/``:

* ``rounds.tsv`` — per-round timings (round number, duration in seconds, aggregation time, gap).
* ``summary.md`` — statistics (mean, std, n) and a status timeline.
* ``round_timings_boxplot.png`` — box plots of round duration, aggregation and gap.
* ``failures.tsv`` and ``infra_constraints.tsv`` — any errors, retries, warnings or resource-limit messages in the
  window, useful when a run did not complete.

Run the script with ``--help`` for all options.

.. _overhead-compare:

********************************
Step 4: Compare the two runs
********************************

Pass the platform's ``rounds.tsv`` to the simulator's ``round-metrics`` target:

.. code-block:: shell

   cd fl-tutorials/nvflare/image_classification/arkplus_fine_tuning
   make round-metrics COMPARE=../../../../round_metrics/24985ec3-3349-435b-afcd-f38972d8695d/rounds.tsv

The new ``summary.md`` in ``round_metrics/simulator-<workspace>-<timestamp>/`` adds a side-by-side table with the
platform − simulator difference for every metric, and a short interpretation.

.. _overhead-interpreting:

*********************
Interpreting results
*********************

These are the numbers from the UK + Thailand production run against a UK RTX 5090 simulator (July 2026):

=============================  ================  ===============  ==========================
Metric                          Platform (s)      Simulator (s)    Δ (platform − simulator)
=============================  ================  ===============  ==========================
Round duration (steady-state)   745.78 ± 0.74     275.36 ± 0.47    **+470.42**
Aggregation                     0.188 ± 0.064     0.220 ± 0.048    −0.03
Inter-round gap                 0.186 ± 0.016     0.207 ± 0.015    −0.02
Round 0 (initialisation)        3,829.99          294.66           +3,535.33
Total span                      40,382            13,797           +26,585
=============================  ================  ===============  ==========================

What the table shows:

1. **Server-side platform overhead is effectively zero.** Aggregation and inter-round gap are sub-second and
   statistically indistinguishable between the production FL server and the simulator.

2. **The steady-state difference is site compute, not platform overhead.** The simulator runs both clients on one GPU
   one after the other (275 s ≈ 2 × 135 s). On the platform each round waits for the slowest site's GPU — here the
   Thailand site, ~745 s. With ~27 KB head-only updates, network transfer is negligible, which the very regular round
   duration confirms (± 0.74 s over 49 cross-continental rounds).

3. **Round 0 is a one-off cost.** The extra ~3,535 s covers staging the ~759 MiB backbone checkpoint to S3 and sending
   the full model to every client over the WAN. Every later round sends only the classifier head, so this cost is paid
   once per run, whatever the number of rounds.

.. _overhead-adapting:

***************************************
Adapting for your own model
***************************************

The same workflow works for any FLIP run:

#. **Run your model on FLIP** through the UI as usual.
#. **Run the same model in the local simulator**, using the tutorial harness or FLARE's ``SimulatorRunner``, with the
   same round count as the deployed ``config.json``.
#. **Extract the platform metrics** with ``extract_platform_round_timings.sh '<your-model-url>'``.
#. **Compare** with ``make round-metrics COMPARE=/path/to/platform/rounds.tsv``.

How to read your results:

* **Aggregation time** grows with the number of trainable parameters exchanged each round.
* **Inter-round gap** is usually sub-second whatever the model size; it reflects FLIP's orchestration, not weight
  transfer.
* **Round 0** grows with your checkpoint size (S3 upload plus WAN broadcast). If you have a large frozen backbone, set
  ``AGGREGATE_ONLY_REGEX`` in ``config.json`` so it is sent only once.
* **Steady-state round duration** is set by your slowest site's GPU time plus the transfer time of each round's
  payload.

.. _overhead-third-site:

***********************************************
Adding a third site's simulator results
***********************************************

A collaborating site produces its own simulator baseline with the same commands as Step 1:

.. code-block:: shell

   git clone https://github.com/londonaicentre/FLIP.git
   cd FLIP/fl-tutorials/nvflare/image_classification/arkplus_fine_tuning
   make reproduce-overhead

They send back their ``arkplus_sim_experiment_<host>_<timestamp>.zip``. It already contains their ``rounds.tsv`` and
``summary.md``, so you only need to unzip it:

.. code-block:: shell

   unzip arkplus_sim_experiment_bdms-gpu-1_20260714T120000.zip -d /tmp/bdms-sim

The comparison takes **one baseline at a time**; there is no combined multi-site table. Either read the site's
``summary.md`` next to the platform's, or re-run the comparison on the site's bundled logs to get a platform-vs-that-site
``summary.md``:

.. code-block:: shell

   bash scripts/fl_round_metrics/extract_simulator_round_timings.sh /tmp/bdms-sim/simulator-arkplus-*/logs \
       --compare /path/to/platform/rounds.tsv -o /tmp/bdms-sim-compare

Repeat for each collaborating site. Each bundle's ``provenance/`` folder (GPU, driver, OS, git commit) lets you put
per-site differences down to hardware rather than software.

.. seealso::

   :doc:`arkplus-fine-tuning`
      The full Ark+ application guide — project setup, cohort query, file upload, and training.

   :gh:`arkplus_fine_tuning README <fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/README.md>`
      Technical reference for the app internals (model architecture, finetuning mechanisms, ``job.py``).

   :gh:`scripts/fl_round_metrics/extract_platform_round_timings.sh`
      The CloudWatch extraction script used in Step 3.
