###################################
Create a FLIP app from a FLARE app
###################################

.. warning::

   This page assumes you are familiar with the :ref:`flip-fl-nets` component page and with the project / model lifecycle described in :doc:`/user-guides/user-common`. Before starting, you must already have: a FLIP account with the ``researcher`` role, an approved project with a saved cohort query, and a model created under that project (so you have a model ID).

.. note::

   This page is the NVIDIA FLARE twin of :doc:`create-flip-app-from-flower` and follows the same
   structure. If you change one page, check whether the other needs the same change.

This page walks through the code changes required to adapt a stock NVIDIA FLARE **Client API** training script (for example, one copied from the official `NVFLARE examples <https://github.com/NVIDIA/NVFlare/tree/main/examples/hello-world>`_ or pulled from the internal FLIP app hub) so that it can run on FLIP. Every NVFLARE job type on FLIP drives the client code through the Client API (``nvflare.client``) — a plain Python script that FLIP's ``ClientAPIExecutor`` runs in-process on each Trust. The walkthrough covers the FLIP touchpoints such a script needs:

- ``flip.get_dataframe(...)`` to retrieve the project's cohort DataFrame
- ``flip.get_by_accession_number(...)`` to pull imaging resources for each accession
- ``SummaryWriter.add_scalar(...)`` (NVFLARE's own tracking API) to publish per-epoch metrics, which FLIP forwards to the model page
- the ``FLModel`` contract for what the trainer receives and returns each round

There is no ``flip.update_status(...)`` to call: on NVFLARE the whole server side is FLIP's, and its ``ServerEventHandler`` drives the model lifecycle for you (see :ref:`flare-server-side`).

A full, runnable reference is available in-tree:

- ``fl-apps/nvflare/standard/`` — the platform-side base template for job type ``standard``: the recipe-generated ``config_fed_server.json`` / ``config_fed_client.json`` and ``meta.json``. ``trainer.py``, ``models.py`` and ``config.json`` are user-supplied (see ``fl-apps/nvflare/standard/required_files.json``).
- ``fl-tutorials/nvflare/image_classification/xray_classification/`` — a complete chest X-ray classification example whose labels come from OMOP via ``query.sql`` (no data enrichment needed).
- ``fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/`` — a MONAI spleen-segmentation example that exercises every touchpoint covered below, including best-model selection.

.. list-table:: How the two backends divide the work
   :widths: 30 35 35
   :header-rows: 1

   * -
     - Flower
     - NVIDIA FLARE
   * - What you upload
     - ``client_app.py``, ``models.py``, ``config.json`` (+ optional ``config.toml``)
     - ``trainer.py``, ``models.py``, ``config.json`` (+ helper modules)
   * - Server side
     - FLIP template ``ServerApp`` (you write one only for local runs)
     - FLIP template workflows and components (nothing to write)
   * - Model lifecycle status
     - ``flip.update_status`` calls in the ``ServerApp``
     - Fired automatically by FLIP's ``ServerEventHandler``
   * - Run configuration
     - ``config.toml`` overriding ``[tool.flwr.app.config]``
     - ``config.json`` (``GLOBAL_ROUNDS``, ``LOCAL_ROUNDS``, …)
   * - Per-epoch metrics
     - ``MetricRecord`` in the reply, forwarded by the server
     - ``SummaryWriter.add_scalar``, forwarded by ``FlipAnalyticsBridge``
   * - Client identity
     - ``SUPERNODE_NAME`` env var placed in the reply's ``ConfigRecord``
     - NVFLARE site name, resolved from the event origin (nothing to do)
   * - Privacy filter on updates
     - ``flip_local_dp_mod`` registered by the uploaded ``client_app.py``
     - ``PercentilePrivacy`` wired by the template (nothing to do)
   * - Local run
     - ``fl-services/flower`` compose stack
     - ``job.py`` recipe: ``make export`` / ``make sim``

*****************
Starting point
*****************

A FLARE app that FLIP can run is a folder of user files plus, for local runs only, a ``job.py`` that describes the job with FLIP's ``FlipFedAvgRecipe``. The minimum layout, following the tutorials, is:

.. code-block:: text

   my-flare-app/
   ├── app_files/
   │   ├── config.json
   │   ├── models.py
   │   ├── trainer.py
   │   └── transforms.py      # optional helper modules travel with the upload
   ├── job.py
   ├── .env.app               # local-run environment (see Local testing)
   └── query.sql              # the cohort query you save on the project (not uploaded)

``trainer.py``, ``models.py`` and ``config.json`` are the files you upload — see :ref:`fl-required-files`. Any other file you upload alongside them (``transforms.py``, ``data_utils.py``, …) is bundled into the same ``custom/`` directory on every site, so plain ``from transforms import ...`` imports work. ``job.py`` is shown here because you need it to run the app locally; on-platform the job is assembled by FLIP from the ``standard`` template and your uploaded files, and a ``job.py`` you upload is simply carried along unused.

.. note::

   Unlike a hand-written NVFLARE job, you do **not** write ``config_fed_server.json``, ``config_fed_client.json`` or ``meta.json``. Those come from the platform's template for the job type (``fl-apps/nvflare/<job_type>/``), which is itself generated from ``flip.nvflare.recipes``; an uploaded copy lands in ``custom/`` and is never read as a config file. Researchers cannot change what happens on the server side — see the *Job types* section of :ref:`flip-fl-nets`.

.. note::

   The ``flip-utils`` package (imported as ``flip``) ships inside every FLIP FL node image, alongside ``nvflare``, ``torch`` and ``monai``. On-platform the trainer runs against those pre-installed packages — there is no per-run dependency installation, so an app can only import what the FL image already carries. If your app needs a dependency the image does not provide, ask the platform operators. For local runs, the tutorials use the ``flip-utils`` project environment with its ``full`` extra (``uv run --project flip-utils --extra full``), which installs the same package set as the ``flare-fl-base`` image.

.. _flare-server-side:

*****************************************
The server side is FLIP's (nothing to do)
*****************************************

On NVFLARE, FLIP owns the entire server side. The ``standard`` template runs four workflows in order — ``InitTraining`` → ``ScatterAndGather`` (federated averaging) → NVFLARE's stock ``GlobalModelEval`` → ``BroadcastTask`` (post-run cleanup) — and wires the FLIP components that talk to the Central Hub. The pieces that matter to an app author:

- **Model lifecycle.** ``ServerEventHandler`` moves the model through ``INITIATED`` → ``PREPARED`` → ``RUNNING`` → ``RESULTS_UPLOADED`` (or ``ERROR`` / ``STOPPED``) on NVFLARE's own events, and ``PersistToS3AndCleanup`` zips the run directory — ``FL_global_model.pt``, ``cross_val_results.json`` and, when selection is on, ``best_FL_global_model.pt`` — and uploads it at the end of the run. This is what drives the progress bar and downloads on the model page (see :ref:`view-results`).
- **Round telemetry.** ``ScatterAndGather`` and ``ServerEventHandler`` emit the round-started / result-received / round-aggregated facts shown in the model page's Live activity feed. Your code never calls ``flip.send_event``.
- **Your ``models.py`` runs on the server too.** The persistor and the model locator instantiate ``models.get_model()`` on the FL server to seed the round-0 global model and to load it for post-training evaluation — so ``models.py`` must import cleanly without data, GPUs or your trainer's dependencies (see :ref:`flare-models-py`).
- **Aggregation.** Clients return a weight **diff**; the server averages the diffs (``InTimeAccumulateWeightedAggregator``, weighted by the ``NUM_STEPS_CURRENT_ROUND`` each client reports) and adds the average onto the global model. This fixes the return contract of your trainer (see :ref:`the diff warning below <flare-return-diff>`).
- **Privacy filter.** Every training result passes through ``PercentilePrivacy`` before it leaves the Trust; you do not register anything in the trainer. The mechanism and its caveats are described under *Privacy filters on shared model updates* on :ref:`flip-fl-nets`.

The template's ``README.md`` (``fl-apps/nvflare/standard/README.md``) lists every workflow, executor and filter by path if you need the full picture.

*****************************************
trainer.py: the Client API loop
*****************************************

On the client side, the trainer is a plain script that FLIP's ``ClientAPIExecutor`` runs in-process for the ``train`` and ``validate`` tasks. It builds the data pipeline once, then loops on ``flare.receive()`` until the job ends.

Imports
=======

.. code-block:: python

   import argparse
   import json
   from pathlib import Path

   import nvflare.client as flare
   import torch
   from flip import FLIP
   from flip.constants import ResourceType
   from models import get_model
   from nvflare.client.tracking import SummaryWriter

Reading the FLIP run values
===========================

FLIP hands the trainer two values at run time, and they arrive through different channels because NVFLARE's ``TaskScriptRunner`` whitespace-splits the executor's ``task_script_args``:

- The **project ID** arrives as a CLI flag. The template's executor is configured with ``task_script_args = "--project_id {project_id}"``, and NVFLARE resolves ``{project_id}`` against the top-level ``project_id`` key of ``config_fed_client.json``, which FLIP's FL API writes at submit time.
- The **cohort query** cannot be a CLI flag — SQL contains spaces — so it is read straight out of the same file's top-level ``query`` key.

.. code-block:: python

   def parse_args() -> argparse.Namespace:
       parser = argparse.ArgumentParser()
       parser.add_argument("--project_id", type=str, default="")
       return parser.parse_args()


   def load_query() -> str:
       """Cohort query, written into config_fed_client.json by the FL API at submit time."""
       client_cfg = Path(__file__).parent.parent / "config" / "config_fed_client.json"
       if client_cfg.exists():
           return json.loads(client_cfg.read_text()).get("query", "")
       return ""


   def load_config() -> dict:
       """Your own config.json, which sits next to this script in custom/."""
       with open(Path(__file__).parent / "config.json") as f:
           return json.load(f)

Both loaders use paths relative to ``__file__`` because on-platform the script runs from NVFLARE's job workspace, not from your project directory: ``trainer.py`` is at ``<app>/custom/trainer.py`` and the client config at ``<app>/config/config_fed_client.json``.

Fetching the FLIP DataFrame
===========================

The cohort DataFrame is fetched once, before the round loop, via ``flip.get_dataframe``. The call runs against the project's saved cohort query (see :ref:`create-cohort-query`) and returns a pandas ``DataFrame`` containing at minimum an ``accession_id`` column plus whatever other columns the SQL ``SELECT`` projected — the X-ray tutorial's ``query.sql`` projects the OMOP labels this way.

.. code-block:: python

   args = parse_args()
   config = load_config()

   flip = FLIP()
   dataframe = flip.get_dataframe(args.project_id, load_query())
   if "accession_id" not in dataframe.columns:
       raise ValueError("The dataframe must contain 'accession_id' column.")

A fresh ``FLIP()`` instance picks up its Imaging API connection from the environment the FLIP-managed fl-client provides, so no additional configuration is needed. In local development (``LOCAL_DEV``) the same call ignores ``project_id`` and ``query`` and reads the CSV named by ``DEV_DATAFRAME`` instead — see :ref:`flare-local-testing`.

Fetching images by accession
============================

Once you have the cohort DataFrame, iterate its ``accession_id`` column and call ``flip.get_by_accession_number`` to pull the imaging resources for each study. The call returns a ``pathlib.Path`` pointing at a local directory containing files named ``input_*.nii.gz`` (and, if you also requested ``ResourceType.SEGMENTATION``, or the labels were uploaded during data enrichment, ``label_*.nii.gz``).

.. code-block:: python

   datalist = []
   for accession_id in dataframe["accession_id"]:
       try:
           accession_folder_path = flip.get_by_accession_number(
               args.project_id,
               accession_id,
               resource_type=[ResourceType.NIFTI],
           )
       except Exception as err:
           print(f"Could not get image data for {accession_id}: {err}")
           continue

       for img in accession_folder_path.rglob("input_*.nii.gz"):
           seg = str(img).replace("/input_", "/label_")
           if not Path(seg).exists():
               continue
           datalist.append({"image": str(img), "label": seg})

.. warning::

   Always wrap ``get_by_accession_number`` in a ``try / except`` loop and ``continue`` on failure. A single Trust may be missing a resource type for a specific accession (for example, a study with imaging but no segmentation), and you should not abort the whole job on one bad accession. Do fail loudly if the loop ends with an **empty** datalist, though: the spleen tutorial raises a ``RuntimeError`` naming the likely cause (the data-enrichment label upload was skipped), because the ``num_samples=0`` error torch would otherwise raise reads like an app bug.

.. note::

   ``ResourceType`` is an enum in ``flip.constants``. The samples use ``ResourceType.NIFTI`` and ``ResourceType.SEGMENTATION``; you may pass either a single value or a list. The first call per (project, accession, resource type) downloads the study out of XNAT; later calls — including the same loop running in local development — return the cached local copy, so there is no need to build your own on-disk guards.

The round loop
==============

After the data pipeline is built, call ``flare.init()`` and loop on ``flare.receive()``. Each received ``FLModel`` carries the current global weights in ``params`` (as numpy arrays, since the template sets ``params_exchange_format = "numpy"``) and the 0-based round index in ``current_round``. Two task types reach the trainer: ``train`` (one per global round) and, after the last round, ``validate`` — NVFLARE's ``GlobalModelEval`` broadcasting the final aggregated model for cross-site evaluation.

.. code-block:: python

   model = get_model().to(device)
   optimizer = torch.optim.Adam(model.parameters(), lr=config["LEARNING_RATE"])

   flare.init()
   writer = SummaryWriter()

   while flare.is_running():
       input_model = flare.receive()
       if input_model is None:
           break

       if flare.is_train():
           global_round = input_model.current_round or 0
           global_weights = {k: torch.as_tensor(v) for k, v in input_model.params.items()}
           model.load_state_dict(global_weights)

           n_iterations = 0
           for epoch in range(config["LOCAL_ROUNDS"]):
               train_loss, n_batches = train_one_epoch(model, train_loader, loss_fn, optimizer, device)
               n_iterations += n_batches
               # ... validation and metrics — see below

           # Return the update as a DIFF (local minus global) — see the warning below.
           new_state = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
           diff = {k: new_state[k] - global_weights[k].detach().cpu().numpy() for k in new_state}
           flare.send(
               flare.FLModel(
                   params=diff,
                   params_type="DIFF",
                   meta={"NUM_STEPS_CURRENT_ROUND": n_iterations},
               )
           )

       elif flare.is_evaluate():
           model.load_state_dict({k: torch.as_tensor(v) for k, v in input_model.params.items()})
           test_loss, test_dice = validate(model, val_loader, ...)
           writer.add_scalar("TEST_DICE", test_dice, global_step=0)
           flare.send(flare.FLModel(metrics={"val_acc": test_dice}))

       else:
           print("Received unknown task; ignoring.")

Create the optimizer **once**, outside the loop: the trainer process lives for the whole job, and the tutorials keep optimizer state (and learning-rate schedules) across global rounds rather than resetting it each round.

.. _flare-return-diff:

.. warning::

   **Return a diff, not full weights.** The ``standard`` template's aggregator averages ``WEIGHT_DIFF`` updates and the shareable generator adds the average onto the global model. A trainer that sends ``params_type="FULL"`` is rejected by the aggregator, and the round fails. Always compute ``local - global`` per key and send it with ``params_type="DIFF"``. Report the number of local optimisation steps in ``meta["NUM_STEPS_CURRENT_ROUND"]``: it is the weight the aggregator gives your update, and the privacy filter's clipping bound scales with it.

The ``is_evaluate()`` branch's metrics land, per site, in ``cross_val_results.json`` inside the results zip via ``ValidationJsonGenerator``. ``val_acc`` is the key the tutorials use; any numeric keys are accepted.

**Optional: cross-site evaluation of client models.** By default post-training evaluation covers only the aggregated global model, so the trainer never sees a ``submit_model`` task. The X-ray tutorial handles it anyway, returning the current local weights with ``params_type="FULL"`` under ``flare.is_submit_model()`` — harmless on the platform, and it makes the script work unchanged with a recipe constructed with ``submit_model_task_name="submit_model"`` locally.

*****************************************
trainer.py: sending per-epoch metrics
*****************************************

Per-epoch, per-site metrics are published through NVFLARE's own tracking API — ``SummaryWriter.add_scalar`` (``flare.log`` works too). The template's ``FlipAnalyticsBridge`` component catches the analytics events the Client API fires and re-emits them to the FL server, whose ``ScatterAndGather`` controller forwards them to the Central Hub with ``flip.send_metrics``. This is what populates the graphs on the model page (see the "Metrics" section of :doc:`/user-guides/user-common`).

.. code-block:: python

   for epoch in range(config["LOCAL_ROUNDS"]):
       train_loss, n_batches = train_one_epoch(...)
       val_loss, val_dice = validate(...)

       # "@epoch" names the x-axis; global_step is the x-coordinate (the cumulative epoch count).
       # The FL global round is stamped server-side as provenance.
       cumulative_epoch = global_round * config["LOCAL_ROUNDS"] + epoch + 1
       writer.add_scalar("TRAIN_LOSS@epoch", float(train_loss), global_step=cumulative_epoch)
       writer.add_scalar("VAL_LOSS@epoch", float(val_loss), global_step=cumulative_epoch)
       writer.add_scalar("VAL_DICE@epoch", float(val_dice), global_step=cumulative_epoch)

.. warning::

   Do **not** call ``flip.send_metrics`` (or ``flip.update_status``) from the trainer. FL clients deliberately do not hold the credentials needed to reach the Central Hub — only the FL server does — so a direct call fails on-platform. The bridge is the supported path.

.. note::

   The site name shown against each metric in the FLIP UI is resolved automatically from the NVFLARE event origin — the Trust's FL kit slot (``Trust_1``, ``Trust_2``, …), which the hub maps back to the owning Trust. There is no client-name variable to set, unlike Flower's ``SUPERNODE_NAME``.

.. note::

   Use stable, upper-case ``label`` strings (``TRAIN_LOSS``, ``VAL_LOSS``, ``VAL_DICE`` are the conventions used in the tutorials) so that metrics from repeated runs line up on the same chart in the UI.

.. note::

   **Arbitrary x-axis.** The tag grammar is ``<label>[@<x_label>]``: the part after ``@`` names the x-axis and ``global_step`` is the coordinate on it. With no ``@`` suffix the metric is plotted at its FL global round on an axis titled "Global Rounds" — pass ``global_step=0`` (or omit it) for a once-per-run value such as ``TEST_DICE``. A plot is identified by the ``(label, x_label)`` pair, so the same metric logged under two different x-labels is shown as two separate plots in the UI. This is the same grammar the Flower metric keys use; the ``.x_<V>`` suffix is not needed here because ``global_step`` carries the coordinate.

.. _flare-models-py:

*****************************************
models.py: the architecture
*****************************************

``models.py`` must expose ``get_model()`` returning a ``torch.nn.Module``. It is the one user file that runs on **both** sides: the trainer calls it to build the network the received weights are loaded into, and the FL server's persistor instantiates it (by path, ``models.get_model``) to produce the round-0 global model, so the state-dict keys the server broadcasts are exactly the keys your trainer's model expects.

.. code-block:: python

   import json
   from pathlib import Path

   from monai.networks.nets import UNet
   from torch import nn


   def load_net_config() -> dict:
       with open(Path(__file__).parent / "config.json") as f:
           return json.load(f).get("net_config", {})


   class SegmentationNetwork(nn.Module):
       def __init__(self, num_classes: int = 1):
           super().__init__()
           net_config = load_net_config()
           self.net = UNet(
               spatial_dims=net_config["spatial_dims"],
               in_channels=1,
               out_channels=num_classes + 1,
               num_res_units=2,
               norm="batch",
               channels=(16, 32, 64, 128, 256),
               strides=(2, 2, 2, 2),
           )

       def forward(self, x):
           return self.net(x)


   def get_model() -> nn.Module:
       return SegmentationNetwork()

Keep it self-contained: import only the model library, read any architecture parameters from ``config.json`` relative to ``__file__`` (both files sit in ``custom/`` on every site, including the server), and do not import ``trainer.py`` or anything that needs data or a GPU at import time. ``get_model()`` is also what an ``evaluation`` job loads a checkpoint into, so an architecture shared between a training app and its evaluation app should live in an identical ``models.py``.

.. note::

   **Fine-tuning from a pretrained backbone.** Declare the checkpoint's filename in ``config.json`` under ``SERVER_CHECKPOINT`` and upload it with the app. FLIP stages it on the FL server only (never on the Trusts) and loads it into ``get_model()`` to seed the round-0 global model, so clients receive the backbone plus freshly initialised heads at round 0 and build only the bare architecture themselves. Pair it with ``AGGREGATE_ONLY_REGEX`` to send back only the trainable head each round. The Ark+ fine-tuning tutorial (``fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/``) shows both.

*****************************************
config.json: job type and run settings
*****************************************

``config.json`` is required for every job type on both backends. On NVFLARE it carries the platform-recognised training settings **and** whatever your own code reads from it:

.. code-block:: json

   {
     "job_type": "standard",
     "GLOBAL_ROUNDS": 2,
     "LOCAL_ROUNDS": 4,
     "BEST_MODEL_METRIC": "VAL_DICE",
     "BEST_MODEL_METRIC_MINIMIZE": false,
     "LEARNING_RATE": 5e-5,
     "VAL_SPLIT": 0.1,
     "BATCH_SIZE": 3,
     "VALIDATE_EVERY": 1,
     "net_config": { "spatial_dims": 3 }
   }

- ``job_type`` selects the template that is bundled (``standard`` here; see *Job types* on :ref:`flip-fl-nets`).
- ``GLOBAL_ROUNDS`` is read by the platform and written into the server workflow at submit time — it is the only way to set the round count of a platform run.
- ``LOCAL_ROUNDS`` is **not** read by the platform: your trainer reads it (``config["LOCAL_ROUNDS"]``), so the value reaches your code verbatim.
- ``BEST_MODEL_METRIC`` (optional) turns on best-global-model selection — see the next section.
- Everything else (``LEARNING_RATE``, ``VAL_SPLIT``, ``net_config``, …) is an app convention: passed through untouched, neither validated nor defaulted.

The full list of platform keys, their defaults and validation rules — including ``AGGREGATOR``, ``AGGREGATION_WEIGHTS``, ``AGGREGATE_ONLY_REGEX`` and ``SERVER_CHECKPOINT`` — is on :ref:`fl-training-configuration`.

Best-model selection (optional)
===============================

When ``config.json`` names a ``BEST_MODEL_METRIC``, FLIP injects NVFLARE's stock ``IntimeModelSelector`` server-side and saves ``best_FL_global_model.pt`` next to the final model whenever the averaged metric improves. The trainer's part is to **evaluate the received global model before training mutates it** and report that metric on the returned ``FLModel``:

.. code-block:: python

   if flare.is_train():
       model.load_state_dict(global_weights)

       global_val_metrics = None
       if config.get("BEST_MODEL_METRIC"):
           g_loss, g_dice = validate(model, val_loader, ...)
           global_val_metrics = {"VAL_LOSS": g_loss, "VAL_DICE": g_dice}

       # ... local training ...

       flare.send(
           flare.FLModel(
               params=diff,
               params_type="DIFF",
               metrics=global_val_metrics,  # metrics of the *received* global model
               meta={"NUM_STEPS_CURRENT_ROUND": n_iterations},
           )
       )

Selection skips round 0 (no aggregated model exists yet), so ``BEST_MODEL_METRIC`` requires ``GLOBAL_ROUNDS >= 2``; the platform rejects the upload otherwise. Set ``BEST_MODEL_METRIC_MINIMIZE`` to ``true`` for loss-like metrics. The final aggregated model is never re-evaluated for selection, so "best" means best among the intermediate global models.

************************************
Submitting the app to FLIP
************************************

Once your app runs locally (see the next section), upload the contents of ``app_files/`` through the FLIP UI's model page. FLIP validates the required files for the job type (``trainer.py``, ``models.py``, ``config.json`` for ``standard`` — see :ref:`fl-required-files` for the canonical list), bundles them with the template into each site's ``custom/`` directory, and then lets you click **Initiate Training**.

At submit time, the FLIP FL API:

- Writes the model UUID into ``meta.json`` (``custom_props.model_id``) and the server config. FLIP's server components resolve it lazily from there, which is why nothing in your files carries a model ID.
- Writes ``project_id`` and ``query`` (the project's saved cohort query) as top-level keys of ``config_fed_client.json`` — the two values the trainer reads.
- Sets ``min_clients`` to the participating-Trust count and ``num_rounds`` to ``config.json``'s ``GLOBAL_ROUNDS`` in the ``ScatterAndGather`` workflow, and applies ``AGGREGATOR`` / ``AGGREGATION_WEIGHTS`` / ``IGNORE_RESULT_ERROR``.
- Injects the optional server- and client-side components that ``config.json`` asks for: the head-only filter chain for ``AGGREGATE_ONLY_REGEX`` and the model selector for ``BEST_MODEL_METRIC``.
- Stages any ``SERVER_CHECKPOINT`` (or an evaluation job's ``models`` checkpoints) on the FL server only, keeping it out of the bundle shipped to the Trusts.
- Submits the job to the net's FL server, which deploys it to every approved Trust's fl-client.

.. _flare-local-testing:

************************************
Local testing before upload
************************************

The tutorials describe the job in Python with ``FlipFedAvgRecipe`` — the same class the platform's ``standard`` template is generated from — and run it through NVFLARE's ``Recipe.execute``. A minimal ``job.py``, abridged from ``fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/job.py``:

.. code-block:: python

   import argparse
   import json
   import os
   import sys
   from pathlib import Path

   _APP_FILES_DIR = Path(__file__).parent / "app_files"
   sys.path.insert(0, str(_APP_FILES_DIR))  # so the recipe can resolve models.get_model

   from flip.nvflare.recipes import FlipFedAvgRecipe  # noqa: E402
   from nvflare import FedJob  # noqa: E402
   from nvflare.recipe import SimEnv  # noqa: E402


   def stage_app_files(job: FedJob) -> None:
       """Bundle every file in app_files/ into the job's server + client custom/ dirs."""
       for src in sorted(_APP_FILES_DIR.iterdir()):
           if src.is_file():
               job.add_file_to_server(str(src))
               job.add_file_to_clients(str(src))


   def main() -> None:
       parser = argparse.ArgumentParser()
       parser.add_argument("--n_clients", type=int, default=2)
       parser.add_argument("--num_rounds", type=int, default=2)
       parser.add_argument("--workspace", type=str, default="/tmp/nvflare/my-app")
       args = parser.parse_args()  # --export / --export-dir are consumed by Recipe.execute

       config = json.loads((_APP_FILES_DIR / "config.json").read_text())
       recipe = FlipFedAvgRecipe(
           num_rounds=args.num_rounds,
           min_clients=args.n_clients,
           train_script="trainer.py",
           train_args="--project_id {project_id}",
           project_id=os.environ.get("FLIP_PROJECT_ID", ""),
           query=os.environ.get("FLIP_QUERY", "SELECT * FROM Table;"),
           best_model_metric=config.get("BEST_MODEL_METRIC"),
           best_model_metric_minimize=config.get("BEST_MODEL_METRIC_MINIMIZE", False),
       )
       stage_app_files(recipe.job)

       env = SimEnv(num_clients=args.n_clients, num_threads=args.n_clients, workspace_root=args.workspace)
       run = recipe.execute(env)
       print(f"Job status: {run.get_status()}")


   if __name__ == "__main__":
       main()

Two execution modes come from that one script:

**Export (no GPU, no data).** Writes the complete NVFLARE job — ``meta.json``, ``app/config/``, and ``app/custom/`` with your files staged — under ``./fl_job/flip_fedavg/``, so you can inspect exactly what the platform will run:

.. code-block:: bash

   make export            # or, from the app directory:
   uv run --project ../../../../flip-utils --extra full \
       python job.py --export --export-dir ./fl_job --n_clients 2 --num_rounds 2

**Simulation (GPU + data).** Runs the job under the NVFLARE simulator against local data. ``LOCAL_DEV`` mode (the ``flip-utils`` default outside the platform) makes ``get_dataframe`` read a CSV and ``get_by_accession_number`` read a directory, both named in the app's ``.env.app``:

.. code-block:: text

   JOB_TYPE=standard
   DEV_IMAGES_DIR=../../../data/spleen/images
   DEV_DATAFRAME=../../../data/spleen/dataframe.csv
   FLIP_PROJECT_ID=dev   # any non-empty token; LOCAL_DEV ignores it
   FLIP_QUERY=

.. code-block:: bash

   make build-fl                                          # once: builds the flare-fl-base image
   make -C fl-tutorials download-spleen-data              # reference dataset into fl-tutorials/data/
   make -C fl-tutorials run-tutorial TUTORIAL=3d_spleen_segmentation
   # or, inside the app directory: make sim NUM_ROUNDS=10 N_CLIENTS=2

Run ``job.py`` in the ``flip-utils`` environment with the ``full`` extra, as the tutorial Makefiles do — a bare ``uv run`` from the repo root resolves to a venv without ``torch``/``nvflare``/``monai``. The tutorial harness (``run-tutorial``) runs the simulator inside the locally built ``flare-fl-base`` image, so ``make build-fl`` is a genuine prerequisite.

.. note::

   ``--num_rounds`` and ``--n_clients`` are **local knobs only**. On the platform the round count comes from ``config.json``'s ``GLOBAL_ROUNDS`` and the client count from the Trusts you approved — the FL API overrides whatever the recipe baked in. Likewise ``FLIP_PROJECT_ID`` / ``FLIP_QUERY`` only seed the exported client config; the FL API overwrites both at submit.

.. note::

   The standalone NVFLARE stack under ``fl-services/nvflare`` has no HTTP submit path (its FL API is admin-API based), so unlike Flower there is no compose-stack submit for a single app: use the simulator, or the full platform path (upload through the FLIP UI, or ``make e2e_smoke``).

See ``fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/README.md`` for the full local-run instructions, including the data-enrichment (label upload) step needed before the segmentation tutorials can train on a real FLIP project.

************************************
Other job types
************************************

The walkthrough above is for ``standard`` (federated averaging). The other NVFLARE job types keep the same Client API shape and change only what the script must return:

- ``fed_opt`` — identical client contract to ``standard`` (a ``DIFF`` per round); only the server-side optimiser differs. Upload the same three files.
- ``evaluation`` — no training. Upload ``evaluator.py``, ``models.py`` and ``config.json``, and list the checkpoints to evaluate under ``config.json``'s ``models`` (each entry naming a ``checkpoint`` file you upload with the app). The script handles ``flare.is_evaluate()`` only, returning aggregate metrics with ``flare.send(flare.FLModel(metrics={...}))`` — never per-patient values. Reference: ``fl-tutorials/nvflare/image_evaluation/3d_spleen_segmentation_evaluation/``.
- ``diffusion_model`` — two-stage latent-diffusion training; adds ``validator.py`` and per-phase ``GLOBAL_ROUNDS_AE`` / ``GLOBAL_ROUNDS_DM`` keys. Reference: ``fl-tutorials/nvflare/image_synthesis/latent_diffusion_model/``.

The manifests on :ref:`fl-required-files` are the authoritative list of required files per job type.

***************************
Common pitfalls
***************************

- **Returning full weights.** ``flare.send(FLModel(params=..., params_type="FULL"))`` from the ``train`` branch is rejected by the aggregator. Return ``local - global`` with ``params_type="DIFF"``, and report ``meta["NUM_STEPS_CURRENT_ROUND"]``.
- **Calling ``flip.send_metrics`` / ``flip.update_status`` from the trainer.** Trusts hold no Central Hub credentials. Publish metrics with ``SummaryWriter.add_scalar``; the lifecycle statuses are the server template's job.
- **``models.py`` that cannot import on the server.** The persistor instantiates ``models.get_model()`` on the FL server with no data mounted. Keep ``models.py`` free of trainer imports and of anything that opens files other than ``config.json`` next to it.
- **Reading ``config.json`` from the working directory.** On-platform the trainer's CWD is not ``custom/``. Resolve ``config.json`` (and ``config_fed_client.json``) relative to ``__file__``.
- **Expecting ``GLOBAL_ROUNDS`` to reach your code.** It is consumed by the platform; ``LOCAL_ROUNDS`` is the one your trainer reads. A ``GLOBAL_ROUNDS`` value outside 1–1000 is silently discarded and the run defaults to a **single** round — check it if training finishes suspiciously early.
- **``BEST_MODEL_METRIC`` with one round.** Selection skips round 0, so the platform rejects the combination; use ``GLOBAL_ROUNDS >= 2`` or drop the key.
- **Missing ``ResourceType`` or labels.** If a Trust does not have the resource type you requested for a given accession, ``get_by_accession_number`` raises — skip the accession. If *no* accession yields a usable sample, raise with a message pointing at the data-enrichment step rather than letting the DataLoader fail on ``num_samples=0``.
- **Uploading NVFLARE config files.** ``config_fed_server.json``, ``config_fed_client.json``, ``meta.json`` and ``job.py`` are not read on the platform; the template's copies are used. Only the files under ``app_files/`` matter.
