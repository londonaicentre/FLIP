####################################
Create a FLIP app from a Flower app
####################################

.. warning::

   This page assumes you are familiar with the :ref:`flip-fl-nets` component page and with the project / model lifecycle described in :doc:`/user-guides/user-common`. Before starting, you must already have: a FLIP account with the ``researcher`` role, an approved project with a saved cohort query, and a model created under that project (so you have a ``flip-model-id``).

.. note::

   This page is the Flower twin of :doc:`create-flip-app-from-flare` and follows the same
   structure. If you change one page, check whether the other needs the same change.

This page walks through the code changes required to adapt a stock Flower app (for example, one copied from the official `Flower quickstart examples <https://flower.ai/docs/examples.html>`_ or pulled from the internal FLIP app hub) so that it can run on FLIP. The walkthrough covers the FLIP touchpoints a FLIP-compatible app needs:

- ``flip.update_status(...)`` in the ``ServerApp`` to signal model lifecycle transitions to the central hub
- ``flip.get_dataframe(...)`` in the ``ClientApp`` to retrieve the project's cohort DataFrame
- ``flip.get_by_accession_number(...)`` in the ``ClientApp`` to pull imaging resources for each accession
- ``flip_local_dp_mod`` registered on the ``ClientApp``'s training handler to privatise the update before it leaves the Trust
- the reply ``MetricRecord`` / ``ConfigRecord`` convention through which per-epoch metrics reach the FLIP UI

A full, runnable reference is available in-tree:

- ``fl-apps/flower/standard/app/`` — the platform-side base bundle: the ``ServerApp`` and the ``FedAvgWithClientMetrics`` strategy. ``client_app.py`` and ``models.py`` are user-supplied (see ``fl-apps/flower/standard/required_files.json``).
- ``fl-tutorials/flower/xray_classification/`` — a complete chest X-ray classification example with both ``ServerApp`` and ``ClientApp``.
- ``fl-tutorials/flower/3d_spleen_segmentation/`` — a MONAI spleen-segmentation example that exercises every touchpoint covered below, including best-model selection.

*****************
Starting point
*****************

A Flower app that FLIP can run is just a standard Flower project with a ``pyproject.toml`` that declares a ``ServerApp`` and a ``ClientApp``. The minimum folder layout is:

.. code-block:: text

   my-flower-app/
   ├── app/
   │   ├── __init__.py
   │   ├── client_app.py
   │   ├── config.json
   │   ├── config.toml
   │   ├── models.py
   │   ├── server_app.py
   │   └── strategy.py
   └── pyproject.toml

``client_app.py``, ``config.json`` and ``models.py`` are the files you upload — see :ref:`fl-required-files` — plus ``config.toml`` if you want to override the template's run configuration (see :ref:`flower-config-toml`). Any other file you upload alongside them (``data_loading.py``, ``transforms.py``, …) is bundled into the app too, so ``from app.transforms import ...`` imports work. ``server_app.py`` and ``strategy.py`` are shown here because you need them to run the app locally; on-platform they come from the FLIP template, and uploaded copies are dropped because their names collide with the template's.

The ``pyproject.toml`` wires the two entry points together:

.. code-block:: toml

   [tool.flwr.app.components]
   serverapp = "app.server_app:app"
   clientapp = "app.client_app:app"

If you are starting from scratch, ``fl-apps/flower/standard/`` provides the platform-side ``ServerApp`` and strategy with the FLIP integration described below; for a complete worked example including a matching ``ClientApp``, see ``fl-tutorials/flower/xray_classification/`` or ``fl-tutorials/flower/3d_spleen_segmentation/``.

.. note::

   ``config.json`` carries only ``job_type`` for a Flower app (``{"job_type": "standard"}``). The hub reads that key and nothing else from it, to pick which base template to bundle. Run configuration goes in ``config.toml``, not here — unlike NVFLARE, where ``config.json`` carries the training settings.

.. note::

   The ``flip-utils`` package (imported as ``flip``) is baked into every FLIP FL node image at ``/opt/flip-utils``, and the base template's ``pyproject.toml`` pins it to that path. Do **not** declare ``flip-utils`` as a dependency in your own ``pyproject.toml``: Flower runtime-installs an app's declared dependencies into a per-run environment that it prepends to ``sys.path``, so a declared ``flip-utils`` resolves from PyPI — where the published release lags the in-repo package — and shadows the image's copy (FLIP#767). Left undeclared, ``import flip`` falls through to the image's copy, which is what the platform runs. See the *Wiring it up* section below for how dependencies resolve on-platform.

*******************************************
ServerApp: reporting model lifecycle status
*******************************************

On the server side, the FLIP integration is a single object (``FLIP``) injected into the ``@app.main()`` function plus four status transitions around ``strategy.start(...)``. On-platform this ``ServerApp`` is the template's (``fl-apps/flower/standard/app/server_app.py``), not yours — this section explains what it does, so that your local ``server_app.py`` behaves the same way and you know what the platform reports on your behalf.

Imports
=======

.. code-block:: python

   from flip import FLIP
   from flip.constants.flip_constants import ModelStatus
   from flwr.app import ArrayRecord, Context
   from flwr.serverapp import Grid, ServerApp
   from flwr.serverapp.strategy import FedAvg

Injecting the FLIP client
==========================

Add ``flip: FLIP = FLIP()`` as a default argument to the ``@app.main()`` callable. A fresh ``FLIP`` instance picks up its central-hub connection details from the environment that the FLIP-managed SuperLink provides at run time, so no additional configuration is needed.

.. code-block:: python

   app = ServerApp()


   @app.main()
   def main(grid: Grid, context: Context, flip: FLIP = FLIP()) -> None:
       ...

Reading the model ID
=====================

Every FLIP-submitted run receives a ``flip-model-id`` key in ``context.run_config``. This is the ID to pass to every ``flip.*`` call that updates central-hub state.

.. code-block:: python

   run_config = context.run_config
   num_rounds = int(run_config.get("num-server-rounds", 1))
   model_id = run_config.get("flip-model-id")

.. note::

   Declare ``flip-model-id`` in ``[tool.flwr.app.config]`` of your ``pyproject.toml`` as a placeholder (for example, ``flip-model-id = "uuid"``). Flower only allows a ``flwr run`` caller — including FLIP's FL API — to override keys that are already declared. The value you declare locally is irrelevant; FLIP injects the real model UUID at submit time.

The four lifecycle transitions
==============================

Wrap your training entry point with four ``update_status`` calls. These drive the progress bar on the model page in the FLIP UI (see :ref:`view-results`).

.. code-block:: python

   flip.update_status(model_id, ModelStatus.INITIATED)          # after config is read

   model = get_model()
   flip.update_status(model_id, ModelStatus.PREPARED)           # after initial weights are built

   arrays = ArrayRecord(model.state_dict())
   strategy = FedAvg(fraction_train=1.0, fraction_evaluate=0.0)

   flip.update_status(model_id, ModelStatus.RUNNING)            # the rounds start here

   result = strategy.start(grid=grid, initial_arrays=arrays, num_rounds=num_rounds)

   # ... save FL_global_model.pt and cross_val_results.json under output_dir ...
   flip.upload_results_to_s3(output_dir, model_id)
   flip.update_status(model_id, ModelStatus.RESULTS_UPLOADED)   # after the results zip is uploaded

.. warning::

   Forgetting the final ``ModelStatus.RESULTS_UPLOADED`` transition will leave the model indefinitely in the "running" state in the FLIP UI even though execution has ended. Conversely, do not report ``RESULTS_UPLOADED`` before ``flip.upload_results_to_s3`` has succeeded — the template reports ``ERROR`` if the save or upload fails, so the researcher never sees a "finished" model with nothing to download.

Full minimal example
=====================

Schematic, based on ``fl-apps/flower/standard/app/server_app.py`` — simplified to the lifecycle calls. See the template for the full version, including the ``min_clients`` wiring described under *Strategy considerations* below (which a real app must not omit), the best-model bookkeeping and the results-file layout:

.. code-block:: python

   from flip import FLIP
   from flip.constants.flip_constants import ModelStatus
   from flwr.app import ArrayRecord, Context
   from flwr.serverapp import Grid, ServerApp
   from flwr.serverapp.strategy import FedAvg

   from app.models import get_model

   app = ServerApp()


   @app.main()
   def main(grid: Grid, context: Context, flip: FLIP = FLIP()) -> None:
       run_config = context.run_config
       num_rounds = int(run_config.get("num-server-rounds"))
       flip_model_id = run_config.get("flip-model-id")

       flip.update_status(flip_model_id, ModelStatus.INITIATED)

       model = get_model()
       flip.update_status(flip_model_id, ModelStatus.PREPARED)

       arrays = ArrayRecord(model.state_dict())
       strategy = FedAvg(fraction_train=1.0, fraction_evaluate=0.0)

       flip.update_status(flip_model_id, ModelStatus.RUNNING)

       result = strategy.start(grid=grid, initial_arrays=arrays, num_rounds=num_rounds)

       output_dir = save_results(result)  # FL_global_model.pt + cross_val_results.json
       flip.upload_results_to_s3(output_dir, flip_model_id)
       flip.update_status(flip_model_id, ModelStatus.RESULTS_UPLOADED)

***********************
Strategy considerations
***********************

Most stock Flower strategies (``FedAvg``, ``FedProx``, and the other built-ins) work unchanged on FLIP because the FLIP touchpoints live in the ``ServerApp`` callable, not in the strategy itself. You can pick any strategy the `flwr.serverapp.strategy <https://flower.ai/docs/framework/ref-api/flwr.serverapp.strategy.html>`_ module exposes.

For a platform run, though, the strategy is the template's ``FedAvgWithClientMetrics``, a subclass of ``flip.flower.strategy.FlipFedAvg``. ``FlipFedAvg`` is what forwards each client's reply metrics and exceptions to the hub and emits the round-progress facts shown in the Live activity feed (see :ref:`flip-fl-nets`); a strategy built on stock ``FedAvg`` trains correctly but reports nothing. Base it on ``FlipFedAvg`` locally too, so what you see in the UI matches what the platform will do:

.. code-block:: python

   from flip.flower.strategy import FlipFedAvg


   class MyStrategy(FlipFedAvg):
       def aggregate_train(self, server_round, replies):
           result = super().aggregate_train(server_round, replies)  # forwards metrics to the hub
           # push any custom server-side signals here
           return result

.. warning::

   Set the strategy's node thresholds from ``flip-min-clients``. Flower defaults ``min_train_nodes``, ``min_evaluate_nodes`` and ``min_available_nodes`` to **2**, so a strategy left on those defaults never starts a round on a single-trust project: ``sample_nodes`` waits for a second node in an **unbounded** ``sleep(1)`` loop, so the job hangs for good rather than timing out (``start()``'s ``timeout`` bounds only ``send_and_receive``, which is never reached). The only trace is flwr's per-second ``Waiting for nodes to connect`` INFO line in the ServerApp log, which the platform does not surface — from the UI it is indistinguishable from slow training. FLIP's FL API injects ``flip-min-clients`` with the participating-trust count, the same value the NVFLARE adapter puts in ``min_clients``.

   FLIP's strategies take it as one argument, read via the helper so your app carries no parsing:

   .. code-block:: python

      from flip.flower.strategy import min_clients_from_run_config

      strategy = FedAvgWithClientMetrics(
          flip=flip,
          model_id=model_id,
          min_clients=min_clients_from_run_config(run_config),
      )

   Building on flwr's ``FedAvg`` directly? Pass the same value to ``min_train_nodes``, ``min_evaluate_nodes`` and ``min_available_nodes`` — but only when it is not ``None``, since ``FedAvg`` types those three as ``int``.

   Do not substitute a constant when the key is absent. ``min_clients_from_run_config`` returns ``None`` there, which leaves flwr's own defaults alone — the right answer, because nothing injects the key on a local ``flwr run .`` or the ``submit_tutorial`` path, and a low constant would let a round start before every participating trust has connected, training on part of the federation without saying so.

****************************************
ClientApp: fetching the FLIP DataFrame
****************************************

On the client side, the cohort DataFrame is fetched once at the start of each training round via ``flip.get_dataframe``. The call runs against the project's saved cohort query (see :ref:`create-cohort-query`) and returns a pandas ``DataFrame`` containing at minimum an ``accession_id`` column plus whatever other columns the SQL ``SELECT`` projected — the X-ray tutorial's ``query.sql`` projects the OMOP labels this way.

Minimal in-line version
========================

.. code-block:: python

   from flip import FLIP

   flip_client = FLIP()
   project_id = context.run_config["flip-project-id"]
   query = context.run_config.get("flip-cohort-query", "*")

   df = flip_client.get_dataframe(project_id=project_id, query=query)

Wrapper pattern (from the MONAI tutorial)
==========================================

The MONAI tutorial bundles the FLIP client with cohort metadata into a small helper class (``app/data_loading.py``). This makes it easier to thread data-loading state through the ``@app.train()`` callable:

.. code-block:: python

   from flip import FLIP


   class FLIP_BASE:
       def __init__(self):
           self.project_id = ""
           self.query = ""
           self.dataframe = None
           self.flip = FLIP()

Then inside the ``ClientApp``:

.. code-block:: python

   flip_utils = FLIP_BASE()
   flip_utils.project_id = run_config.get("flip-project-id")
   flip_utils.query = run_config.get("flip-cohort-query", "*")
   flip_utils.dataframe = flip_utils.flip.get_dataframe(
       project_id=flip_utils.project_id,
       query=flip_utils.query,
   )

.. note::

   Declare ``flip-project-id`` and ``flip-cohort-query`` in ``[tool.flwr.app.config]`` for the same reason you declare ``flip-model-id``: so that FLIP's FL API can override them at submit time with the project's real values.

*******************************************
ClientApp: fetching images by accession
*******************************************

Once you have the cohort DataFrame, iterate its ``accession_id`` column and call ``flip.get_by_accession_number`` to pull the imaging resources for each study. The call returns a ``pathlib.Path`` pointing at a local directory containing files named ``input_*.nii.gz`` (and, if you also requested ``ResourceType.SEGMENTATION``, or the labels were uploaded during data enrichment, ``label_*.nii.gz``).

.. code-block:: python

   from pathlib import Path

   from flip.constants import ResourceType

   for accession_id in df["accession_id"]:
       try:
           accession_folder_path = flip_client.get_by_accession_number(
               project_id,
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

   Always wrap ``get_by_accession_number`` in a ``try / except`` loop and ``continue`` on failure. A single trust may be missing a resource type for a specific accession (for example, a study with imaging but no segmentation), and you should not abort the whole round on one bad accession. Do fail loudly if the loop ends with an **empty** datalist, though: an app that lets the DataLoader raise on ``num_samples=0`` produces an error that reads like an app bug, when the usual cause is that the data-enrichment (label upload) step was skipped.

.. note::

   ``ResourceType`` is an enum in ``flip.constants``. The samples use ``ResourceType.NIFTI`` and ``ResourceType.SEGMENTATION``; you may pass either a single value or a list. The first call per (project, accession, resource type) downloads the study out of XNAT; later calls — such as the same loop running again on the next round — return the cached local copy, so there is no need to build your own on-disk guards.

***************************************************
ClientApp: privatising the update before it leaves
***************************************************

FLIP's NVFLARE templates apply a privacy filter to every training result inside the platform-owned client config, so the app author does nothing. On Flower the ``ClientApp`` is the uploaded file, so the template cannot wire the filter — the uploaded ``client_app.py`` is expected to register FLIP's differential-privacy mod on its training handler:

.. code-block:: python

   from flip.flower.privacy import flip_local_dp_mod
   from flwr.clientapp import ClientApp

   app = ClientApp()


   @app.train(mods=[flip_local_dp_mod])   # training only — leave @app.evaluate alone
   def train(msg: Message, context: Context) -> Message:
       ...

The mod clips the local update to ``dp-clipping-norm`` and adds Gaussian noise calibrated to (``dp-epsilon``, ``dp-delta``) on the SuperNode, so the FL server only ever receives a privatised update; it is toggled by ``dp-enabled``. All four keys are declared in the template's ``[tool.flwr.app.config]``, so they can be overridden per run from ``config.toml``. Integer buffers (BatchNorm's ``num_batches_tracked`` counters) pass through untouched. See *Privacy filters on shared model updates* on :ref:`flip-fl-nets` for the mechanism and its caveats.

.. warning::

   This is a convention checked by code review before upload, not enforced by the platform: a ``client_app.py`` that omits the mod trains and aggregates perfectly happily while sharing raw model updates. Register it.

***************************************************
ClientApp: sending per-epoch metrics to the hub
***************************************************

Per-round, per-client metrics reach the FLIP UI through the reply ``Message``: put them in a ``MetricRecord`` under ``"metrics"``, and put the client's site name in a ``ConfigRecord`` under ``"config"``. The server-side ``FlipFedAvg`` strategy forwards every numeric metric to the Central Hub with ``flip.send_metrics`` on the client's behalf (``flip.flower.metrics.handle_client_metrics``). This is what populates the graphs on the model page (see the "Metrics" section of :doc:`/user-guides/user-common`).

.. code-block:: python

   import os

   from flwr.app import ArrayRecord, ConfigRecord, Message, MetricRecord, RecordDict

   client_name = os.getenv("SUPERNODE_NAME", "unknown_client")

   # global_round from server is 1-based; convert to 0-based for local epoch arithmetic
   global_round = int(msg.content["config"]["server-round"]) - 1

   per_epoch_metrics = {}
   for epoch in range(local_epochs):
       train_loss = train_func(...)
       val_dice, val_loss = validate_func(...)

       # "@epoch" names the x-axis and ".x_<N>" is the coordinate (the cumulative epoch count),
       # so the server forwards one point per epoch. The FL global round is recorded alongside
       # as provenance.
       cumulative_epoch = global_round * local_epochs + epoch + 1
       per_epoch_metrics[f"train_loss@epoch.x_{cumulative_epoch}"] = float(train_loss)
       per_epoch_metrics[f"val_loss@epoch.x_{cumulative_epoch}"] = float(val_loss)
       per_epoch_metrics[f"val_dice@epoch.x_{cumulative_epoch}"] = float(val_dice)

   metrics = {
       "train_loss": avg_train_loss,          # per-round summary, plotted at the global round
       "val_loss": avg_val_loss,
       "val_dice": avg_val_dice,
       "num-examples": len(train_loader.dataset),  # bookkeeping — read by the strategy, not forwarded
       **per_epoch_metrics,
   }
   content = RecordDict({
       "arrays": ArrayRecord(model.state_dict()),
       "metrics": MetricRecord(metrics),
       "config": ConfigRecord({"site": client_name}),
   })
   return Message(content=content, reply_to=msg)

.. warning::

   Do **not** call ``flip.send_metrics`` (or ``flip.update_status``) from the ``ClientApp``. SuperNodes deliberately do not hold the credentials needed to reach the Central Hub — only the SuperLink does — so a direct call fails on-platform. The reply ``MetricRecord`` is the supported path.

.. warning::

   ``SUPERNODE_NAME`` is the trust's **FL kit slot** (e.g. ``Trust_1``, ``Trust_2``) — the same slot name flip-api assigns to that trust in the FLKitSlot table — and **not** the trust's display name. The hub resolves the slot back to the owning trust when saving metrics (see ``resolve_trust_from_fl_client_name`` in ``flip_api.model_services``); a ``site`` value that matches no assignment is recorded against ``unknown_client`` and will not be attributable to the site in the FLIP UI. A reply with no ``site`` at all is dropped by the forwarder. FLIP-provisioned SuperNode compose files set ``SUPERNODE_NAME=${FL_KIT_SLOT}`` for you; if you are running a SuperNode locally you must export the slot yourself.

.. note::

   Metric labels are upper-cased by the forwarder (``train_loss`` → ``TRAIN_LOSS``), so the tutorials write them in lower case in the reply. Keep the names stable so that metrics from repeated runs line up on the same chart in the UI.

.. note::

   **Arbitrary x-axis.** The key grammar is ``<label>[@<x_label>][.x_<V>]``: ``@<x_label>`` names the x-axis (e.g. ``epoch``) and ``.x_<V>`` is the coordinate on it (any float, e.g. ``"loss@wall_clock_s.x_12.5"``). A key with neither suffix is plotted at its FL global round on an axis titled "Global Rounds". The older ``.round_<N>`` suffix still parses but is deprecated. A plot is identified by the ``(label, x_label)`` pair, so the same metric logged under two different x-labels is shown as two separate plots in the UI. This is the same grammar the NVFLARE ``SummaryWriter`` tags use, minus the ``.x_<V>`` part, which there is carried by ``global_step``.

.. _flower-config-toml:

****************************************************
Wiring it up: ``pyproject.toml`` and ``config.toml``
****************************************************

Two files carry a FLIP-compatible Flower app's configuration, and they play different roles locally and on-platform.

``pyproject.toml`` — local development
======================================

Your own ``pyproject.toml`` drives local runs. Beyond a normal Flower project it needs a ``[tool.flwr.app.config]`` block declaring the FLIP run-config keys, even if the values are placeholders, plus the training hyperparameters (``num-server-rounds``, ``local-epochs``, etc.) so you can override them locally. Abridged from ``fl-tutorials/flower/3d_spleen_segmentation/pyproject.toml``:

.. code-block:: toml

   [project]
   name = "standard-app"
   version = "1.0.0"
   dependencies = [
       # flip-utils is deliberately NOT declared — see the note under "Starting point"
       "flwr[simulation]>=1.36.0",
       # ... your model-framework deps (monai, torch, nibabel, ...)
   ]

   [tool.flwr.app]
   publisher = "flwrlabs"

   [tool.flwr.app.components]
   serverapp = "app.server_app:app"
   clientapp = "app.client_app:app"

   [tool.flwr.app.config]
   num-server-rounds = 3   # equivalent to NVFLARE's GLOBAL_ROUNDS
   local-epochs = 1        # equivalent to NVFLARE's LOCAL_ROUNDS
   learning-rate = 1e-4
   batch-size = 2
   # Client-side DP knobs read by flip_local_dp_mod
   dp-enabled = true
   dp-clipping-norm = 1.0
   dp-sensitivity = 1e-4
   dp-epsilon = 10.0
   dp-delta = 1e-5
   # FLIP-injected keys — declare placeholders so FLIP can override at submit time
   flip-model-id = "uuid"
   flip-project-id = "uuid"
   flip-cohort-query = "*"
   flip-job-dir = "job-dir"
   flip-min-clients = 2  # placeholder matching flwr's default; FLIP injects the trust count
   # Best-model selection (opt-in) — see below
   best-model-metric = ""
   best-model-metric-minimize = false

.. note::

   **How dependencies resolve on-platform.** When you upload the app to FLIP, the platform bundles your
   files into its own base template (``fl-apps/flower/<job_type>/``), whose ``pyproject.toml``
   governs the run — uploaded files cannot override it. At run time, Flower's runtime dependency
   installer resolves that template's dependencies fresh for every run (``uv sync`` into an isolated
   per-run environment) on the SuperLink (``ServerApp``) and on each SuperNode (``ClientApp``), with two
   pins set by the template's ``[tool.uv.sources]``:

   - ``flip-utils`` installs from the source copy shipped inside the FL images at ``/opt/flip-utils`` —
     never from PyPI — so the platform always runs the ``flip-utils`` matching its images.
   - ``torch``/``torchvision`` come from PyTorch's cu128 wheel index (PyPI's default cu130 wheels
     require a newer NVIDIA driver than FLIP hosts run).

   Everything else resolves from PyPI at run time, so trust and hub hosts need outbound HTTPS to PyPI
   and ``download.pytorch.org``. If your app needs a dependency the base template does not declare,
   ask the platform operators to add it to the template.

``config.toml`` — run configuration on the platform
====================================================

Because the template's ``pyproject.toml`` governs a platform run, the way to set rounds, epochs, learning rate or the DP knobs for a platform run is a ``config.toml`` uploaded next to your code. The FL API merges it into the ``--run-config`` file it passes to ``flwr run`` at submission, so every top-level key in it overrides the template's ``[tool.flwr.app.config]``. From ``fl-tutorials/flower/3d_spleen_segmentation/app/config.toml``:

.. code-block:: toml

   num-server-rounds = 30
   local-epochs = 5
   learning-rate = 5e-5
   best-model-metric = "test_dice"
   best-model-metric-minimize = false

``config.toml`` can only override keys the template's ``pyproject.toml`` already declares — Flower rejects a ``--run-config`` override for an undeclared key — so it cannot introduce new ones. An app uploaded without a ``config.toml`` runs with the template's defaults (three rounds, one local epoch). For local ``flwr run`` the same keys live in your own ``[tool.flwr.app.config]`` instead.

Best-model selection (optional)
===============================

Set ``best-model-metric`` in ``config.toml`` to the name of an aggregated **evaluation** metric the clients report (``test_dice`` in the spleen tutorial) and the template saves ``best_FL_global_model.pt`` alongside the final model whenever that metric improves, recording ``best_model`` / ``best_round`` / ``best_metric`` in ``cross_val_results.json``. Set ``best-model-metric-minimize = true`` for loss-like metrics. Two consequences: the evaluate phase runs **every** round instead of only the last (one test-split inference pass per client per round), and the key must be one the clients actually emit — name a key nobody reports and the run completes with no best model, logging a warning per round rather than failing. Selection needs at least two rounds. This is the Flower analogue of NVFLARE's ``BEST_MODEL_METRIC`` in ``config.json``.

************************************
Submitting the app to FLIP
************************************

Once your app runs locally (see the next section), upload it through the FLIP UI's model page the same way you would upload a FLARE app. FLIP validates the required files for a Flower app (which differ from those required for a FLARE app, and depend on the job type — see :ref:`fl-required-files` for the canonical list), bundles them with the job type's template, and then lets you click **Initiate Training**.

At submit time, the FLIP FL API:

- Injects ``flip-model-id``, ``flip-project-id``, ``flip-cohort-query``, ``flip-min-clients``
  (the participating-trust count) and ``flip-job-dir`` (the app directory, which the ``evaluation`` ServerApp uses to find the uploaded checkpoint) into the run config, on top of whatever your ``config.toml`` sets.
- Sets ``SUPERNODE_NAME`` on each participating trust's SuperNode container to the trust's assigned FL kit slot (e.g. ``Trust_1``).
- Starts the ``ServerApp`` on the Central Hub's SuperLink and the ``ClientApp`` on each approved trust's SuperNode.

************************************
Local testing before upload
************************************

The supported way to run a Flower app locally is the standalone compose stack under ``fl-services/flower`` — a SuperLink, two SuperNodes and an FL API, with the tutorial's dev data bind-mounted. ``LOCAL_DEV`` mode (the ``flip-utils`` default outside the platform) makes ``get_dataframe`` read a CSV and ``get_by_accession_number`` read a directory, named by ``DEV_DATAFRAME`` and ``DEV_IMAGES_DIR``, which the stack bind-mounts into every container at fixed paths so relative paths in tutorial code resolve the same way everywhere.

.. code-block:: bash

   make -C fl-tutorials download-spleen-data FL_BACKEND=flower   # reference dataset into fl-tutorials/data/
   make -C fl-tutorials run-tutorial TUTORIAL=3d_spleen_segmentation FL_BACKEND=flower

The harness brings the stack up, submits the app to the FL API exactly as the platform does, waits for the run and tears the stack down. To iterate by hand instead:

.. code-block:: bash

   cd fl-services/flower
   make build                                 # fl-base / superlink / supernode images
   make up                                    # fl-api, superlink, supernode-1, supernode-2
   make submit APP=3d_spleen_segmentation     # POSTs to the fl-api inside the container

.. warning::

   Do **not** test with ``flwr run`` / the Simulation Engine. It is technically possible but brittle for reasons specific to this project: the long-lived ``flower-superlink`` caches its environment (so changing ``DEV_DATAFRAME`` between runs has no effect until you kill it), ``flwr run`` executes the ``ClientApp`` from a snapshot under ``~/.flwr/apps/`` so relative data paths break, and FLIP's settings singleton is pinned at import time so mid-run path overrides are ignored. The compose stack has none of these problems. ``fl-tutorials/flower/3d_spleen_segmentation/README.md`` spells out the workarounds if you must experiment with it anyway.

See the tutorial README for the full local-run instructions, including the data-enrichment (label upload) step needed before the segmentation tutorials can train on a real FLIP project.

***************************
Common pitfalls
***************************

- **Missing ``RESULTS_UPLOADED``.** Forgetting the final ``flip.update_status(model_id, ModelStatus.RESULTS_UPLOADED)`` call in a local ``ServerApp`` leaves the model stuck on "running" in the UI.
- **Calling ``flip.send_metrics`` / ``flip.update_status`` from the ``ClientApp``.** SuperNodes hold no Central Hub credentials. Put metrics in the reply ``MetricRecord`` with the site in the ``ConfigRecord``; the server forwards them.
- **Wrong or missing ``site``.** ``SUPERNODE_NAME`` must be the trust's **FL kit slot** (``Trust_1``, ``Trust_2``, ...), not the trust display name — metrics forwarded with any other value land under ``unknown_client`` and will not appear on the per-site chart; a reply with no ``site`` is dropped.
- **No DP mod.** A ``client_app.py`` without ``@app.train(mods=[flip_local_dp_mod])`` shares raw model updates. The platform does not enforce it.
- **Run configuration in the wrong file.** ``config.json`` carries only ``job_type`` on Flower; rounds, epochs and hyperparameters for a platform run go in ``config.toml``. An app uploaded without one runs three rounds of one epoch, whatever your own ``pyproject.toml`` says.
- **Declaring ``flip-utils`` as a dependency.** It resolves from PyPI and shadows the image's copy (FLIP#767). Leave it undeclared; the template pins it to ``/opt/flip-utils`` on-platform.
- **Undeclared run-config keys.** ``flwr run`` (and therefore FLIP's FL API) can only override keys already declared in ``[tool.flwr.app.config]``. Declaring ``flip-model-id``, ``flip-project-id``, ``flip-cohort-query``, ``flip-job-dir`` and ``flip-min-clients`` with placeholder values is mandatory even though the real values are injected by FLIP. This matters in two places: your own ``pyproject.toml``, so a local run works; and — for the on-platform run — the base template's ``pyproject.toml`` under ``fl-apps/flower/<job_type>/``, which is the config FLIP's overrides and your ``config.toml`` are validated against, since an uploaded ``pyproject.toml`` does not become the app's project file.
- **Missing ``ResourceType``.** If a trust does not have the resource type you requested for a given accession, ``get_by_accession_number`` will raise. Always wrap the call in ``try / except`` and skip the accession on failure so a single bad study does not abort the whole round.
