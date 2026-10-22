###################################
Create a FLIP app from a FLARE app
###################################

.. admonition:: Before you start
   :class: note

   You need:

   - a FLIP account with the ``researcher`` role;
   - an approved project with a saved cohort query;
   - a model created under that project, so you have a model ID.

   This page assumes you know the :ref:`flip-fl-nets` component page and the project and model lifecycle in
   :doc:`/user-guides/user-common`.

.. note::

   This page is the NVIDIA FLARE twin of :doc:`create-flip-app-from-flower`. Both pages follow the same structure
   and cover the same FLIP touchpoints, so you can compare the two backends section by section.

This page shows how to adapt a stock NVIDIA FLARE **Client API** training script so that it runs on FLIP — for
example, one copied from the `FLARE examples <https://github.com/NVIDIA/NVFlare/tree/main/examples/hello-world>`_ or
the FLIP app hub. Every FLARE job type on FLIP runs your client code through the Client API (``nvflare.client``): a
plain Python script that runs on each Trust.

The FLIP touchpoints your script needs are:

- ``flip.get_dataframe(...)`` to retrieve the project's cohort DataFrame
- ``flip.get_by_accession_number(...)`` to pull the imaging for each accession
- ``SummaryWriter.add_scalar(...)`` (FLARE's own tracking API) to publish per-epoch metrics to the model page
- the ``FLModel`` contract: what the trainer receives and returns each round

You do **not** call ``flip.update_status(...)``. On FLARE, FLIP owns the server side and reports the model
lifecycle for you (see :ref:`flare-server-side`).

.. seealso::

   Runnable references in the repository:

   - ``fl-apps/nvflare/standard/`` — the platform's base template for the ``standard`` job type. You supply
     ``trainer.py``, ``models.py`` and ``config.json``.
   - ``fl-tutorials/nvflare/image_classification/xray_classification/`` — chest X-ray classification. Labels come from
     OMOP via ``query.sql``, so no data enrichment is needed.
   - ``fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/`` — MONAI spleen segmentation. Exercises
     every touchpoint below, including best-model selection.

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
     - FLIP template (nothing to write)
   * - Model lifecycle status
     - ``flip.update_status`` calls in the ``ServerApp``
     - Reported automatically by the FLIP template
   * - Run configuration
     - ``config.toml`` overriding ``[tool.flwr.app.config]``
     - ``config.json`` (``GLOBAL_ROUNDS``, ``LOCAL_ROUNDS``, …)
   * - Per-epoch metrics
     - ``MetricRecord`` in the reply, forwarded by the server
     - ``SummaryWriter.add_scalar``, forwarded by the template
   * - Client identity
     - ``client_identity(context)`` placed in the reply's ``ConfigRecord``
     - Resolved automatically (nothing to do)
   * - Privacy filter on updates
     - ``flip_local_dp_mod`` registered by the uploaded ``client_app.py``
     - Wired by the template (nothing to do)
   * - Local run
     - ``make run-tutorial`` (simulator), as on FLARE
     - ``job.py`` recipe: ``make export`` / ``make sim``

*****************
Starting point
*****************

A FLARE app that FLIP can run is a folder of your files plus, for local runs only, a ``job.py`` that describes the
job with FLIP's ``FlipFedAvgRecipe``. The minimum layout, following the tutorials, is:

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

You upload ``trainer.py``, ``models.py`` and ``config.json`` (see :ref:`fl-required-files`). Any other file you
upload with them, such as ``transforms.py`` or ``data_utils.py``, lands in the same ``custom/`` directory on every
site, so plain ``from transforms import ...`` imports work.

You need ``job.py`` only to run the app locally. On the platform, FLIP assembles the job from the ``standard``
template and your uploaded files; an uploaded ``job.py`` is ignored.

.. note::

   You do **not** write ``config_fed_server.json`` or ``config_fed_client.json``. They come from the platform's
   template for the job type, and an uploaded copy is ignored. You cannot change the server side — see *Job types*
   in :ref:`fl-jobs`.

   ``meta.json`` is optional. Upload one to declare the job's GPUs, for example
   ``{"resource_spec": {"@default": {"num_of_gpus": 1}}}``. FLIP keeps its ``resource_spec`` and ``custom_props`` and
   writes the rest — see :ref:`fl-nvflare-meta-json`.

.. note::

   The ``flip-utils`` package (imported as ``flip``) is pre-installed in every FLIP FL node image, alongside
   ``nvflare``, ``torch`` and ``monai``. Nothing is installed per run, so your app can import only what the image
   already carries. If you need another dependency, ask the platform operators.

   For local runs, use the ``flip-utils`` project environment with its ``full`` extra, which installs the same
   packages as the FL image (see :ref:`flare-local-testing`).

.. _flare-server-side:

*****************************************
The server side is FLIP's (nothing to do)
*****************************************

On FLARE, FLIP owns the entire server side. The ``standard`` template runs four stages in order — initialise
training, federated-averaging rounds, evaluation of the final global model, post-run cleanup — and talks to the
Central Hub for you. What matters to you as an app author:

- **Model lifecycle.** FLIP moves the model through ``INITIATED`` → ``PREPARED`` → ``RUNNING`` →
  ``RESULTS_UPLOADED`` (or ``ERROR`` / ``STOPPED``). At the end of the run it zips and uploads
  ``FL_global_model.pt``, ``cross_val_results.json`` and, with best-model selection, ``best_FL_global_model.pt``.
  This drives the progress bar and downloads on the model page (see :ref:`view-results`).
- **Round telemetry.** The round-started, result-received and round-aggregated entries in the model page's Live
  activity feed come from the server. Your code never calls ``flip.send_event``.
- **Your** ``models.py`` **runs on the server too.** The server calls ``models.get_model()`` to build the round-0 global
  model and to load it for post-training evaluation. ``models.py`` must therefore import cleanly without data, GPUs
  or your trainer's dependencies (see :ref:`flare-models-py`).
- **Aggregation.** Clients return a weight **diff**. The server averages the diffs, weighted by the
  ``NUM_STEPS_CURRENT_ROUND`` each client reports, and adds the result to the global model. This fixes what your
  trainer must return (see :ref:`the diff warning below <flare-return-diff>`).
- **Privacy filter.** Every training result passes through a privacy filter before it leaves the Trust. You register
  nothing. See *Privacy filters on shared model updates* on :ref:`fl-update-privacy` for how it works and its caveats.

.. seealso::

   ``fl-apps/nvflare/standard/README.md`` lists every workflow, executor and filter in the template.

*****************************************
trainer.py: the Client API loop
*****************************************

On each Trust, FLIP runs your trainer for the ``train`` and ``validate`` tasks. The script builds its data pipeline
once, then loops on ``flare.receive()`` until the job ends.

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

FLIP hands the trainer two values at run time, through two different channels:

- The **project ID** arrives as the ``--project_id`` command-line flag.
- The **cohort query** cannot be a flag, because SQL contains spaces. Read it from the top-level ``query`` key of
  ``config_fed_client.json``, which FLIP writes when you submit the job.

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

Both loaders resolve paths relative to ``__file__``. On the platform the script does not run from your project
directory: ``trainer.py`` is at ``<app>/custom/trainer.py`` and the client config at
``<app>/config/config_fed_client.json``.

Fetching the FLIP DataFrame
===========================

Fetch the cohort DataFrame once, before the round loop, with ``flip.get_dataframe``. The call runs the project's
saved cohort query (see :ref:`create-cohort-query`) and returns a pandas ``DataFrame`` with at least an
``accession_id`` column, plus whatever other columns your SQL ``SELECT`` returns. The X-ray tutorial's
``query.sql`` returns its OMOP labels this way.

.. code-block:: python

   args = parse_args()
   config = load_config()

   flip = FLIP()
   dataframe = flip.get_dataframe(args.project_id, load_query())
   if "accession_id" not in dataframe.columns:
       raise ValueError("The dataframe must contain 'accession_id' column.")

``FLIP()`` picks up its connection details from the Trust's environment, so no configuration is needed. In local
development (``LOCAL_DEV``) the call ignores ``project_id`` and ``query`` and reads the CSV named by
``DEV_DATAFRAME`` instead — see :ref:`flare-local-testing`.

Fetching images by accession
============================

Iterate the DataFrame's ``accession_id`` column and call ``flip.get_by_accession_number`` for each study. The call
returns a ``pathlib.Path`` to a local directory containing ``input_*.nii.gz`` files. If you requested
``ResourceType.SEGMENTATION``, or labels were uploaded during data enrichment, it also contains ``label_*.nii.gz``.

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

   **Skip a failing accession; fail on an empty datalist.** Wrap ``get_by_accession_number`` in ``try / except``
   and ``continue`` on failure: a Trust may lack a resource type for one accession (for example, imaging but no
   segmentation), and one bad study should not abort the job.

   If the loop ends with **no** samples, raise an error that names the likely cause — usually a skipped
   data-enrichment (label upload) step. The spleen tutorial does this. Otherwise torch fails with a
   ``num_samples=0`` error that looks like an app bug.

.. note::

   ``ResourceType`` is an enum in ``flip.constants``. The samples use ``ResourceType.NIFTI`` and
   ``ResourceType.SEGMENTATION``; pass a single value or a list. On the platform, the first call for a project,
   accession and resource type downloads the study; later calls return the cached copy, so you need no on-disk
   guards of your own.

   In local development nothing is downloaded: the call returns ``DEV_IMAGES_DIR/<accession_id>``. If that
   directory is missing it silently **creates an empty one** instead of raising, so a local run just gets no
   samples for that accession and the ``except`` branch never runs.

The round loop
==============

Once the data pipeline is built, call ``flare.init()`` and loop on ``flare.receive()``. Each received ``FLModel``
carries the current global weights in ``params`` (as numpy arrays) and the 0-based round index in
``current_round``. Two task types reach the trainer:

- ``train`` — once per global round;
- ``validate`` — once after the last round, to evaluate the final global model on each site.

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

Create the optimizer **once**, outside the loop. The trainer process lives for the whole job, and the tutorials
keep optimizer state and learning-rate schedules across global rounds.

.. _flare-return-diff:

.. warning::

   **Return a diff, not full weights.** The ``standard`` template averages weight diffs and adds the average to the
   global model. A trainer that sends ``params_type="FULL"`` is rejected, and the round fails.

   Compute ``local - global`` for every key and send it with ``params_type="DIFF"``. Report the number of local
   optimisation steps in ``meta["NUM_STEPS_CURRENT_ROUND"]``: it sets the weight your update gets in the average,
   and the privacy filter's clipping bound scales with it.

The metrics you send from the ``is_evaluate()`` branch are saved per site in ``cross_val_results.json`` in the
results zip. The tutorials use the key ``val_acc``; any numeric key is accepted.

.. note::

   **Optional: cross-site evaluation of client models.** By default only the aggregated global model is evaluated
   after training, so the trainer never receives a ``submit_model`` task. The X-ray tutorial handles one anyway,
   returning its local weights with ``params_type="FULL"`` under ``flare.is_submit_model()``. This is harmless on the
   platform and lets the same script work locally with a recipe built with ``submit_model_task_name="submit_model"``.

*****************************************
trainer.py: sending per-epoch metrics
*****************************************

Publish per-epoch, per-site metrics with FLARE's own tracking API, ``SummaryWriter.add_scalar`` (``flare.log``
works too). The FLIP template forwards them from the Trust to the FL server, and the server sends them to the
Central Hub. They populate the graphs on the model page (see :ref:`training-metrics`).

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

   Do **not** call ``flip.send_metrics`` (or ``flip.update_status``) from the trainer. FL clients deliberately hold
   no Central Hub credentials — only the FL server does — so a direct call fails on the platform. Use
   ``SummaryWriter``.

.. note::

   **Site names and labels.** FLIP attributes each metric to its site automatically: the Trust's FL kit slot
   (``Trust_1``, ``Trust_2``, …), which the hub maps back to the owning Trust. Unlike Flower, the trainer has no
   client identity to set.

   Use stable, upper-case labels (the tutorials use ``TRAIN_LOSS``, ``VAL_LOSS``, ``VAL_DICE``) so that metrics from
   repeated runs line up on the same chart.

.. note::

   **Choosing the x-axis.** A tag has the form ``<label>[@<x_label>]``:

   - ``@<x_label>`` names the x-axis, and ``global_step`` is the coordinate on it.
   - With no ``@`` suffix, the axis is titled "Global Rounds". If you pass ``global_step`` it is still the
     coordinate; if you omit it, FLIP uses the current FL global round.
   - Each ``(label, x_label)`` pair is a separate plot, so one metric logged under two x-labels appears as two plots.

   For a once-per-run value such as ``TEST_DICE``, either omit ``global_step`` (plotted at the final round) or pass
   ``global_step=0`` (plotted at x=0). Pick one convention and keep it, or repeated runs will not line up.

   Flower metric keys use the same grammar, plus a ``.x_<V>`` suffix that ``global_step`` makes unnecessary here.

.. _flare-models-py:

*****************************************
models.py: the architecture
*****************************************

``models.py`` must expose ``get_model()``, returning a ``torch.nn.Module``. It is the one file that runs on **both**
sides: your trainer calls it to build the network it loads the received weights into, and the FL server calls it to
build the round-0 global model. Both sides therefore agree on the state-dict keys.

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

Keep ``models.py`` self-contained:

- import only the model library;
- read architecture parameters from ``config.json`` relative to ``__file__`` (both files sit in ``custom/`` on every
  site, including the server);
- do not import ``trainer.py``, or anything that needs data or a GPU at import time.

An ``evaluation`` job also loads its checkpoint into ``get_model()``, so a training app and its evaluation app
should share an identical ``models.py``.

.. note::

   **Fine-tuning from a pretrained backbone.** Name the checkpoint file in ``config.json`` under
   ``SERVER_CHECKPOINT`` and upload it with the app. FLIP stages it on the FL server only, never on the Trusts, and
   loads it into ``get_model()`` to build the round-0 global model. Clients receive the backbone plus freshly
   initialised heads at round 0. Pair it with ``AGGREGATE_ONLY_REGEX`` to send back only the trainable head each
   round. The Ark+ fine-tuning tutorial (``fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/``) shows
   both.

*****************************************
config.json: job type and run settings
*****************************************

``config.json`` is required for every job type on both backends. On FLARE it carries the platform's training
settings **and** anything your own code reads:

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

- ``job_type`` selects the template (``standard`` here; see *Job types* on :ref:`fl-jobs`).
- ``GLOBAL_ROUNDS`` is read by the platform. It is the only way to set the number of rounds for a platform run.
- ``LOCAL_ROUNDS`` is read by your trainer (``config["LOCAL_ROUNDS"]``), not by the server. FLIP still checks it at
  submit time:

  - If the key is absent, FLIP writes ``"LOCAL_ROUNDS": 1``, so a ``config.get("LOCAL_ROUNDS", 5)`` fallback in your
    trainer never fires.
  - The key must be named exactly ``LOCAL_ROUNDS``. A ``LOCAL_ROUNDS_<stage>`` key needs a matching
    ``GLOBAL_ROUNDS_<stage>``.
  - A value outside 1–1000 is left as written and reaches your code unchanged.

- ``BEST_MODEL_METRIC`` (optional) turns on best-model selection — see the next section.
- Everything else (``LEARNING_RATE``, ``VAL_SPLIT``, ``net_config``, …) is your app's own convention. FLIP passes it
  through untouched: it is neither validated nor defaulted.

.. seealso::

   :ref:`fl-training-configuration` lists every platform key with its default and validation rules, including
   ``AGGREGATOR``, ``AGGREGATION_WEIGHTS``, ``AGGREGATE_ONLY_REGEX`` and ``SERVER_CHECKPOINT``.

Best-model selection (optional)
===============================

When ``config.json`` sets ``BEST_MODEL_METRIC``, FLIP saves ``best_FL_global_model.pt`` next to the final model
whenever the averaged metric improves. Your trainer's part is to **evaluate the received global model before
training changes it**, and report that metric on the returned ``FLModel``:

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

- Set ``BEST_MODEL_METRIC_MINIMIZE`` to ``true`` for loss-like metrics.
- Selection skips round 0, when no aggregated model exists yet, so it needs ``GLOBAL_ROUNDS >= 2``. FLIP checks this
  at **Initiate Training**, not at upload: your files upload and scan cleanly, and then the job is refused with an
  error naming the two keys.
- The final aggregated model is not re-evaluated for selection, so "best" means the best of the intermediate global
  models.

************************************
Submitting the app to FLIP
************************************

Once your app runs locally (see the next section), upload the contents of ``app_files/`` on the model page of the
FLIP UI. FLIP checks that the required files for the job type are present (``trainer.py``, ``models.py`` and
``config.json`` for ``standard`` — see :ref:`fl-required-files`), bundles them with the template, and then lets
you click **Initiate Training**.

When you submit, FLIP:

- writes the model ID into ``config.json`` (``FLIP_MODEL_ID``) and the server config, so your files never need to
  carry it;
- writes ``meta.json``: your ``resource_spec`` (or the run's GPU override, or the platform default) and
  ``custom_props``, plus the job name and Trusts from the run;
- writes the project ID and the project's saved cohort query into ``config_fed_client.json`` — the two values your
  trainer reads;
- sets the minimum client count to the number of participating Trusts and the round count to ``GLOBAL_ROUNDS``, and
  applies ``AGGREGATOR``, ``AGGREGATION_WEIGHTS`` and ``IGNORE_RESULT_ERROR``;
- adds the optional components your ``config.json`` asks for: head-only aggregation for ``AGGREGATE_ONLY_REGEX`` and
  model selection for ``BEST_MODEL_METRIC``;
- stages any ``SERVER_CHECKPOINT`` (or an evaluation job's checkpoints) on the FL server only, keeping it out of the
  bundle sent to the Trusts;
- submits the job to the FL server, which deploys it to every approved Trust.

.. _flare-local-testing:

************************************
Local testing before upload
************************************

The tutorials describe the job in Python with ``FlipFedAvgRecipe`` — the same class the platform's ``standard``
template is generated from — and run it with FLARE's ``Recipe.execute``. A minimal ``job.py``, abridged from
``fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/job.py``:

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

The same script gives you two modes.

**Export (no GPU, no data).** Writes the complete FLARE job — ``meta.json``, ``app/config/`` and ``app/custom/``
with your files — under ``./fl_job/flip_fedavg/``, so you can inspect exactly what the platform will run:

.. code-block:: shell

   make export            # or, from the app directory:
   uv run --project ../../../../flip-utils --extra full \
       python job.py --export --export-dir ./fl_job --n_clients 2 --num_rounds 2

**Simulation (GPU and data).** Runs the job in the FLARE simulator against local data. In ``LOCAL_DEV`` mode (the
default outside the platform), ``get_dataframe`` reads a CSV and ``get_by_accession_number`` reads a directory, both
named in the app's ``.env.app``:

.. code-block:: text

   JOB_TYPE=standard
   DEV_IMAGES_DIR=../../../data/spleen/images
   DEV_DATAFRAME=../../../data/spleen/dataframe.csv
   # FLIP_PROJECT_ID: any non-empty token; LOCAL_DEV ignores it
   FLIP_PROJECT_ID=dev
   FLIP_QUERY=

.. code-block:: shell

   make -C fl-tutorials download-spleen-data              # reference dataset into fl-tutorials/data/
   make -C fl-tutorials run-tutorial TUTORIAL=3d_spleen_segmentation
   # or, inside the app directory: make sim NUM_ROUNDS=10 N_CLIENTS=2

.. important::

   Run ``job.py`` in the ``flip-utils`` environment with the ``full`` extra, as the tutorial Makefiles do. A bare
   ``uv run`` from the repository root picks an environment without ``torch``, ``nvflare`` or ``monai``.

   The simulator runs on the host, not in a container, so you do **not** need ``make build-fl``. You need the
   ``full`` extra installed (``uv sync --extra full`` in ``flip-utils``) and a GPU visible to that environment.

.. note::

   ``--num_rounds`` and ``--n_clients`` are **local settings only**. On the platform the round count comes from
   ``GLOBAL_ROUNDS`` in ``config.json`` and the client count from the Trusts you approved. Likewise,
   ``FLIP_PROJECT_ID`` and ``FLIP_QUERY`` only seed the exported config; FLIP replaces both when you submit.

.. note::

   Unlike Flower, the standalone FLARE stack under ``fl-services/nvflare`` cannot submit a single app over HTTP.
   Use the simulator, or the full platform path: upload through the FLIP UI, or run ``make e2e_smoke``.

.. seealso::

   ``fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/README.md`` has the full local-run instructions,
   including the data-enrichment (label upload) step the segmentation tutorials need before they can train on a real
   FLIP project.

************************************
Other job types
************************************

The walkthrough above is for ``standard`` (federated averaging). The other FLARE job types keep the same Client
API shape and change only what the script returns:

- ``fed_opt`` — same client contract as ``standard`` (a ``DIFF`` per round); only the server-side optimiser
  differs. Upload the same three files.
- ``evaluation`` — no training. Upload ``evaluator.py``, ``models.py`` and ``config.json``, and list the checkpoints
  to evaluate under ``models`` in ``config.json`` (each entry names a ``checkpoint`` file you upload with the app).
  The script handles ``flare.is_evaluate()`` only and returns aggregate metrics with
  ``flare.send(flare.FLModel(metrics={...}))`` — never per-patient values. Reference:
  ``fl-tutorials/nvflare/image_evaluation/3d_spleen_segmentation_evaluation/``.
- ``diffusion_model`` — two-stage latent-diffusion training. Adds ``validator.py`` and per-stage
  ``GLOBAL_ROUNDS_AE`` / ``GLOBAL_ROUNDS_DM`` keys. Reference:
  ``fl-tutorials/nvflare/image_synthesis/latent_diffusion_model/``.

:ref:`fl-required-files` is the authoritative list of required files per job type.

***************************
Common pitfalls
***************************

- **Returning full weights.** Sending ``params_type="FULL"`` from the ``train`` branch is rejected. Return
  ``local - global`` with ``params_type="DIFF"``, and report ``meta["NUM_STEPS_CURRENT_ROUND"]``.
- **Calling** ``flip.send_metrics`` / ``flip.update_status`` **from the trainer.** Trusts hold no Central Hub
  credentials. Publish metrics with ``SummaryWriter.add_scalar``; the server template reports the lifecycle.
- **A** ``models.py`` **that cannot import on the server.** The FL server calls ``models.get_model()`` with no data
  mounted. Keep ``models.py`` free of trainer imports, and do not open any file other than the ``config.json`` next to
  it.
- **Reading** ``config.json`` **from the working directory.** On the platform the trainer does not run from
  ``custom/``. Resolve ``config.json`` and ``config_fed_client.json`` relative to ``__file__``.
- **Expecting** ``GLOBAL_ROUNDS`` **to reach your code.** The platform consumes it; your trainer reads
  ``LOCAL_ROUNDS``. A ``GLOBAL_ROUNDS`` outside 1–1000 is silently discarded and the run falls back to a **single**
  round — check it if training finishes suspiciously early.
- ``BEST_MODEL_METRIC`` **with one round.** Selection skips round 0, so FLIP refuses the job at Initiate Training
  (after your files have uploaded cleanly). Use ``GLOBAL_ROUNDS >= 2``, or drop the key.
- **Missing** ``ResourceType`` **or labels.** If a Trust lacks the requested resource type for an accession,
  ``get_by_accession_number`` raises — skip that accession. If *no* accession yields a sample, raise an error that
  points at the data-enrichment step rather than letting the DataLoader fail on ``num_samples=0``.
- **Uploading FLARE config files.** ``config_fed_server.json``, ``config_fed_client.json`` and ``job.py`` are
  ignored on the platform; the template's copies are used. Only the files in ``app_files/`` matter. The exception is
  ``meta.json``: an uploaded one declares the job's resources, and one carrying anything beyond what :ref:`FLARE meta.json <fl-nvflare-meta-json>` lists (a ``launcher_spec``, a per-site ``resource_spec`` block, …) fails at
  Initiate Training.
