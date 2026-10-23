####################################
Create a FLIP app from a Flower app
####################################

.. admonition:: Before you start
   :class: note

   You need:

   - a FLIP account with the ``researcher`` role;
   - an approved project with a saved cohort query;
   - a model created under that project, so you have a ``flip-model-id``.

   This page assumes you know the :ref:`flip-fl-nets` component page and the project and model lifecycle in
   :doc:`/user-guides/user-common`.

.. note::

   This page is the Flower twin of :doc:`create-flip-app-from-flare`. Both pages follow the same structure and cover
   the same FLIP touchpoints, so you can compare the two backends section by section. The FLARE page opens with a
   table summarising how the backends differ.

This page shows how to adapt a stock Flower app so that it runs on FLIP — for example, one copied from the
`Flower quickstart examples <https://flower.ai/docs/examples.html>`_ or the FLIP app hub.

The FLIP touchpoints your app needs are:

- ``flip.update_status(...)`` in the ``ServerApp`` to report model lifecycle transitions to the Central Hub
- ``flip.get_dataframe(...)`` in the ``ClientApp`` to retrieve the project's cohort DataFrame
- ``flip.get_by_accession_number(...)`` in the ``ClientApp`` to pull the imaging for each accession
- ``flip_local_dp_mod`` on the ``ClientApp``'s training handler, to privatise the update before it leaves the Trust
- the reply ``MetricRecord`` / ``ConfigRecord`` convention that carries per-epoch metrics to the FLIP UI

.. seealso::

   Runnable references in the repository:

   - ``fl-apps/flower/standard/app/`` — the platform's base bundle: the ``ServerApp`` and the
     ``FedAvgWithClientMetrics`` strategy. You supply ``client_app.py`` and ``models.py``.
   - ``fl-tutorials/flower/xray_classification/`` — chest X-ray classification, with both ``ServerApp`` and
     ``ClientApp``.
   - ``fl-tutorials/flower/3d_spleen_segmentation/`` — MONAI spleen segmentation. Exercises every touchpoint below,
     including best-model selection.

*****************
Starting point
*****************

A Flower app that FLIP can run is a standard Flower project whose ``pyproject.toml`` declares a ``ServerApp`` and a
``ClientApp``. The minimum layout is:

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

You upload ``client_app.py``, ``config.json`` and ``models.py`` (see :ref:`fl-required-files`), plus ``config.toml``
if you want to override the template's run configuration (see :ref:`flower-config-toml`). Any other file you upload
with them, such as ``data_loading.py`` or ``transforms.py``, is bundled into the app too, so
``from app.transforms import ...`` imports work.

You need ``server_app.py`` and ``strategy.py`` only to run the app locally. On the platform they come from the FLIP
template, and uploaded copies are dropped because their names collide with the template's.

The ``pyproject.toml`` wires the two entry points together:

.. code-block:: toml

   [tool.flwr.app.components]
   serverapp = "app.server_app:app"
   clientapp = "app.client_app:app"

If you are starting from scratch, ``fl-apps/flower/standard/`` provides the platform's ``ServerApp`` and strategy;
the two tutorials above add a matching ``ClientApp``.

.. note::

   For a Flower app, ``config.json`` carries only ``job_type`` (``{"job_type": "standard"}``), which FLIP uses to
   pick the base template. Run configuration goes in ``config.toml`` — unlike FLARE, where ``config.json`` carries
   the training settings.

   FLARE's ``meta.json`` GPU request has no Flower counterpart: Flower does not schedule jobs by GPU. A GPU request
   made when you initiate training is recorded in the model's activity feed but not enforced, and each SuperNode uses
   whatever GPUs its host provides.

.. important::

   **Do not declare ``flip-utils`` as a dependency** in your own ``pyproject.toml``. The ``flip-utils`` package
   (imported as ``flip``) is pre-installed in every FLIP FL node image, and on the platform your app imports that
   copy.

   A local ``flwr run`` installs your declared dependencies into a per-run environment that takes precedence. A
   declared ``flip-utils`` would therefore come from PyPI — whose release can lag the one you develop against — and
   hide the copy the run should use. Left undeclared, ``import flip`` resolves to the FL image's copy on the platform,
   and to your checkout's ``flip-utils`` under ``make sim-tutorial``. See :ref:`flower-config-toml` for how
   dependencies resolve on the platform.

*******************************************
ServerApp: reporting model lifecycle status
*******************************************

On the server side, the FLIP integration is a ``FLIP`` object passed into the ``@app.main()`` function, plus four
status updates around ``strategy.start(...)``.

On the platform this ``ServerApp`` is the template's, not yours. This section explains what it does, so that your
local ``server_app.py`` behaves the same way and you know what the platform reports for you.

Imports
========

.. code-block:: python

   from flip import FLIP
   from flip.constants.flip_constants import ModelStatus
   from flwr.app import ArrayRecord, Context
   from flwr.serverapp import Grid, ServerApp
   from flwr.serverapp.strategy import FedAvg

Injecting the FLIP client
==========================

Add ``flip: FLIP = FLIP()`` as a default argument to the ``@app.main()`` function. ``FLIP()`` picks up its Central
Hub connection details from the SuperLink's environment, so no configuration is needed.

.. code-block:: python

   app = ServerApp()


   @app.main()
   def main(grid: Grid, context: Context, flip: FLIP = FLIP()) -> None:
       ...

Reading the model ID
=====================

Every run FLIP submits has a ``flip-model-id`` key in ``context.run_config``. Pass it to every ``flip.*`` call that
updates Central Hub state.

.. code-block:: python

   run_config = context.run_config
   num_rounds = int(run_config.get("num-server-rounds", 1))
   model_id = run_config.get("flip-model-id")

.. note::

   Declare ``flip-model-id`` in ``[tool.flwr.app.config]`` of your ``pyproject.toml`` with a placeholder value, for
   example ``flip-model-id = "uuid"``. Flower lets a caller — including FLIP — override only keys that are already
   declared. FLIP injects the real model ID when you submit.

The four lifecycle transitions
==============================

Wrap your training entry point with four ``update_status`` calls. They drive the progress bar on the model page
(see :ref:`view-results`). ``FlipFedAvg`` (see *Strategy considerations*) reports ``RUNNING`` from
``strategy.start`` itself, so the explicit ``RUNNING`` call is only needed with stock ``FedAvg``, as here.

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

   **Always report ``RESULTS_UPLOADED`` last, and only after a successful upload.** Without it, the model stays
   "running" in the FLIP UI even though the run has ended. Reported before ``flip.upload_results_to_s3`` succeeds, it
   shows a finished model with nothing to download — the template reports ``ERROR`` instead if saving or uploading
   fails.

Full minimal example
=====================

A simplified version of ``fl-apps/flower/standard/app/server_app.py``, reduced to the lifecycle calls. The template
also has the best-model bookkeeping, the results-file layout and the ``min_clients`` wiring described under
*Strategy considerations* below — a real app must not leave that out.

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

Most stock Flower strategies (``FedAvg``, ``FedProx`` and the other built-ins) work unchanged on FLIP, because the
FLIP touchpoints live in the ``ServerApp`` function, not in the strategy. You can use any strategy in
`flwr.serverapp.strategy <https://flower.ai/docs/framework/ref-api/flwr.serverapp.strategy.html>`_.

On the platform, though, the strategy is the template's ``FedAvgWithClientMetrics``, built on
``flip.flower.strategy.FlipFedAvg``. ``FlipFedAvg`` forwards each client's metrics and errors to the hub and reports
round progress to the Live activity feed (see :ref:`fl-jobs`). A strategy built on stock ``FedAvg`` trains
correctly but reports nothing. Base your local strategy on ``FlipFedAvg`` too, so the UI shows what the platform
will:

.. code-block:: python

   from flip.flower.strategy import FlipFedAvg


   class MyStrategy(FlipFedAvg):
       def aggregate_train(self, server_round, replies):
           result = super().aggregate_train(server_round, replies)  # forwards metrics to the hub
           # push any custom server-side signals here
           return result

.. warning::

   **Set the strategy's node thresholds from** ``flip-min-clients``. Flower defaults ``min_train_nodes``,
   ``min_evaluate_nodes`` and ``min_available_nodes`` to **2**. Left on those defaults, a single-Trust project never
   starts a round: the server waits for a second node forever. Nothing times out, and in the UI it looks like slow
   training — the only trace is a ``Waiting for nodes to connect`` line in the ServerApp log, which the platform
   does not show.

   FLIP injects ``flip-min-clients`` with the number of participating Trusts. FLIP's strategies take it as one
   argument, read with a helper:

   .. code-block:: python

      from flip.flower.strategy import min_clients_from_run_config

      strategy = FedAvgWithClientMetrics(
          flip=flip,
          model_id=model_id,
          min_clients=min_clients_from_run_config(run_config),
      )

   If you build on flwr's ``FedAvg`` directly, pass the same value to all three ``min_*`` arguments — but only when
   it is not ``None``, since ``FedAvg`` expects an ``int``.

   Do not substitute a constant when the key is absent. The helper then returns ``None``, which keeps flwr's
   defaults. That is deliberate: nothing injects the key on a local ``flwr run .`` or the compose stack's
   ``make submit``, and a low constant would let a round start before every Trust has connected — silently training
   on part of the federation.

****************************************
ClientApp: fetching the FLIP DataFrame
****************************************

On the client side, fetch the cohort DataFrame at the start of each training round with ``flip.get_dataframe``. The
call runs the project's saved cohort query (see :ref:`create-cohort-query`) and returns a pandas ``DataFrame`` with
at least an ``accession_id`` column, plus whatever other columns your SQL ``SELECT`` returns. The X-ray tutorial's
``query.sql`` returns its OMOP labels this way.

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

The MONAI tutorial bundles the FLIP client and cohort metadata into a small helper class (``app/data_loading.py``),
which makes it easier to pass data-loading state through the ``@app.train()`` function:

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

   Declare ``flip-project-id`` and ``flip-cohort-query`` in ``[tool.flwr.app.config]`` for the same reason as
   ``flip-model-id``: so FLIP can override them with the project's real values when you submit.

*******************************************
ClientApp: fetching images by accession
*******************************************

Iterate the DataFrame's ``accession_id`` column and call ``flip.get_by_accession_number`` for each study. The call
returns a ``pathlib.Path`` to a local directory containing ``input_*.nii.gz`` files. If you requested
``ResourceType.SEGMENTATION``, or labels were uploaded during data enrichment, it also contains ``label_*.nii.gz``.

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

   **Skip a failing accession; fail on an empty datalist.** Wrap ``get_by_accession_number`` in ``try / except``
   and ``continue`` on failure: a Trust may lack a resource type for one accession (for example, imaging but no
   segmentation), and one bad study should not abort the round.

   If the loop ends with **no** samples, raise an error that names the likely cause — usually a skipped
   data-enrichment (label upload) step. Otherwise the DataLoader fails with a ``num_samples=0`` error that looks like
   an app bug.

.. note::

   ``ResourceType`` is an enum in ``flip.constants``. The samples use ``ResourceType.NIFTI`` and
   ``ResourceType.SEGMENTATION``; pass a single value or a list. The first call for a project, accession and
   resource type downloads the study; later calls — such as the same loop on the next round — return the cached
   copy, so you need no on-disk guards of your own.

***************************************************
ClientApp: privatising the update before it leaves
***************************************************

On FLARE, FLIP's template applies a privacy filter to every training result, and the app author does nothing. On
Flower the ``ClientApp`` is your uploaded file, so the template cannot add the filter. Register FLIP's
differential-privacy mod on your training handler:

.. code-block:: python

   from flip.flower.privacy import flip_local_dp_mod
   from flwr.clientapp import ClientApp

   app = ClientApp()


   @app.train(mods=[flip_local_dp_mod])   # training only — leave @app.evaluate alone
   def train(msg: Message, context: Context) -> Message:
       ...

On each SuperNode, the mod clips the local update to ``dp-clipping-norm`` and adds Gaussian noise scaled by
``dp-sensitivity``, ``dp-epsilon`` and ``dp-delta``, so the FL server only ever receives a privatised update.
``dp-enabled`` turns it on or off. The template declares all five keys, so you can override them per run in
``config.toml``.

Integer buffers, such as BatchNorm's ``num_batches_tracked`` counters, pass through untouched. See *Privacy filters
on shared model updates* on :ref:`fl-update-privacy` for how it works and its caveats.

.. warning::

   **The platform does not enforce this.** It is checked by code review before upload. A ``client_app.py`` without
   the mod trains and aggregates normally while sharing raw model updates. Register it.

***************************************************
ClientApp: sending per-epoch metrics to the hub
***************************************************

Per-round, per-client metrics reach the FLIP UI in the reply ``Message``:

- put the metrics in a ``MetricRecord`` under ``"metrics"``;
- put the client's site name in a ``ConfigRecord`` under ``"config"``.

The server-side ``FlipFedAvg`` strategy forwards every numeric metric to the Central Hub on the client's behalf.
They populate the graphs on the model page (see :ref:`training-metrics`).

.. code-block:: python

   from flip.flower.identity import client_identity
   from flwr.app import ArrayRecord, ConfigRecord, Message, MetricRecord, RecordDict

   client_name = client_identity(context)  # SUPERNODE_NAME on a SuperNode; partition-id under the simulator

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

   Do **not** call ``flip.send_metrics`` (or ``flip.update_status``) from the ``ClientApp``. SuperNodes deliberately
   hold no Central Hub credentials — only the SuperLink does — so a direct call fails on the platform. Use the reply
   ``MetricRecord``.

.. warning::

   **The ``site`` value must be the Trust's FL kit slot** (``Trust_1``, ``Trust_2``, …), not the Trust's display
   name. The hub maps the slot back to the owning Trust:

   - a ``site`` that matches no slot is recorded against ``unknown_client`` and is not attributed to the Trust in the
     UI;
   - a reply with no ``site`` at all is dropped.

   Read the name with ``client_identity(context)`` rather than from the environment. On a FLIP SuperNode it returns
   ``SUPERNODE_NAME``, which the Trust's deployment sets to its kit slot. Under the simulator, where that variable is
   absent, it derives a ``site-<n>`` name from the partition ID. One ``client_app.py`` then attributes its metrics
   correctly on the platform, on the compose stack and under ``flwr run``. Both tutorials do this.

.. note::

   **Labels.** FLIP upper-cases metric labels (``train_loss`` becomes ``TRAIN_LOSS``), so the tutorials write them in
   lower case. Keep the names stable so that metrics from repeated runs line up on the same chart.

.. note::

   **Choosing the x-axis.** A key has the form ``<label>[@<x_label>][.x_<V>]``:

   - ``@<x_label>`` names the x-axis (for example ``epoch``).
   - ``.x_<V>`` is the coordinate on it — any float, for example ``"loss@wall_clock_s.x_12.5"``.
   - A key with neither suffix is plotted at its FL global round, on an axis titled "Global Rounds".
   - Each ``(label, x_label)`` pair is a separate plot, so one metric logged under two x-labels appears as two plots.

   FLARE ``SummaryWriter`` tags use the same grammar, with ``global_step`` in place of ``.x_<V>``.

.. _flower-config-toml:

****************************************************
Wiring it up: ``pyproject.toml`` and ``config.toml``
****************************************************

Two files carry a Flower app's configuration, and they play different roles locally and on the platform:

- your ``pyproject.toml`` drives **local** runs;
- an uploaded ``config.toml`` sets the run configuration **on the platform**.

``pyproject.toml`` — local development
======================================

Beyond a normal Flower project, your ``pyproject.toml`` needs a ``[tool.flwr.app.config]`` block that declares the
FLIP run-config keys (placeholder values are fine) and your training hyperparameters (``num-server-rounds``,
``local-epochs``, …), so you can override them locally. Abridged from
``fl-tutorials/flower/3d_spleen_segmentation/pyproject.toml``:

.. code-block:: toml

   [project]
   name = "standard-app"
   version = "1.0.0"
   dependencies = [
       # flip-utils is deliberately NOT declared — see the note under "Starting point"
       "flwr[simulation]>=1.38.0",
       # ... your model-framework deps (monai, torch, nibabel, ...)
   ]

   [tool.flwr.app]
   publisher = "flwrlabs"

   [tool.flwr.app.components]
   serverapp = "app.server_app:app"
   clientapp = "app.client_app:app"

   [tool.flwr.app.config]
   num-server-rounds = 3   # equivalent to FLARE's GLOBAL_ROUNDS
   local-epochs = 1        # equivalent to FLARE's LOCAL_ROUNDS
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

   **How dependencies resolve on the platform.** FLIP bundles your files into its own base template, whose
   ``pyproject.toml`` governs the run; an uploaded ``pyproject.toml`` cannot override it. Nothing is installed at run
   time: the ``ServerApp`` and every ``ClientApp`` run in the FL image's own environment, which carries every package
   the template's code imports.

   That environment's ``torch`` / ``torchvision`` are PyTorch's CUDA 13.0 builds (Torch 2.13 / torchvision 0.28), so
   GPU hosts need **NVIDIA driver 580 or newer**.

   If your app needs a package the FL images do not carry, ask the platform operators to add it to the images.
   Declaring it in any ``pyproject.toml`` installs nothing on the platform.

``config.toml`` — run configuration on the platform
====================================================

Because the template's ``pyproject.toml`` governs a platform run, set rounds, epochs, learning rate or the DP knobs
in a ``config.toml`` uploaded next to your code. FLIP passes it to ``flwr run`` when you submit, so every top-level
key in it overrides the template's ``[tool.flwr.app.config]``. From
``fl-tutorials/flower/3d_spleen_segmentation/app/config.toml``:

.. code-block:: toml

   num-server-rounds = 30
   local-epochs = 5
   learning-rate = 5e-5
   best-model-metric = "test_dice"
   best-model-metric-minimize = false

- ``config.toml`` can only override keys the template already declares; Flower rejects any other key. It cannot add
  new ones.
- Without a ``config.toml``, an app runs with the template's defaults: three rounds of one local epoch.
- For a local ``flwr run``, the same keys live in your own ``[tool.flwr.app.config]``.

Best-model selection (optional)
===============================

Set ``best-model-metric`` in ``config.toml`` to an aggregated **evaluation** metric your clients report
(``test_dice`` in the spleen tutorial). The template then saves ``best_FL_global_model.pt`` next to the final model
whenever that metric improves, and records ``best_model``, ``best_round`` and ``best_metric`` in
``cross_val_results.json``.

- Set ``best-model-metric-minimize = true`` for loss-like metrics.
- With a single round, the best checkpoint just duplicates the final model; the run logs a warning but proceeds.
- The evaluate phase then runs **every** round instead of only the last — one test-split inference pass per client
  per round.
- The key must be one the clients actually report. Otherwise the run completes with no best model, logging a warning
  each round rather than failing.

This is the Flower equivalent of ``BEST_MODEL_METRIC`` in a FLARE ``config.json``.

************************************
Submitting the app to FLIP
************************************

Once your app runs locally (see the next section), upload it on the model page of the FLIP UI, as you would a FLARE
app. FLIP checks that the required files for the job type are present (they differ from a FLARE app's — see
:ref:`fl-required-files`), bundles them with the template, and then lets you click **Initiate Training**.

When you submit, FLIP:

- injects ``flip-model-id``, ``flip-project-id``, ``flip-cohort-query``, ``flip-min-clients`` (the number of
  participating Trusts) and ``flip-job-dir`` (the app directory, where an ``evaluation`` job finds its uploaded
  checkpoint) into the run config, on top of your ``config.toml``;
- starts the ``ServerApp`` on the Central Hub's SuperLink and the ``ClientApp`` on each approved Trust's SuperNode.

************************************
Local testing before upload
************************************

There are two local ways to run an app: the flwr simulator, and the Flower containers. The app code is identical on
both, because ``client_identity`` reads ``SUPERNODE_NAME`` where it is set and otherwise falls back to the partition
ID in the Flower ``Context`` (see the ``client_identity`` warning above).

In ``LOCAL_DEV`` mode (the default outside the platform), ``get_dataframe`` reads the CSV named by
``DEV_DATAFRAME`` and ``get_by_accession_number`` reads the directory named by ``DEV_IMAGES_DIR``. Both local paths
point these at the tutorial's dataset under ``fl-tutorials/data/``.

**Fast inner loop: the flwr simulator.** ``make run-tutorial`` (or its alias ``make sim-tutorial``, as on FLARE)
runs ``flwr run`` against a local SuperLink that flwr starts itself — no containers, no FL API, no Docker — so a
code change is one re-run away:

.. code-block:: shell

   make -C fl-tutorials download-spleen-data FL_BACKEND=flower   # reference dataset into fl-tutorials/data/
   make -C fl-tutorials run-tutorial TUTORIAL=3d_spleen_segmentation FL_BACKEND=flower

The wrapper:

- sets the dev-data variables for the tutorial;
- sets the number of simulated sites from ``flip-min-clients``;
- supplies ``flip-job-dir``, pointed at the downloaded checkpoint, so the ``evaluation`` tutorial runs unchanged;
- exits with the run's final status (a bare ``flwr run`` returns 0 whatever happened to the run);
- refuses to start if a SuperLink from another checkout is already listening, which would otherwise run your app in
  that checkout's environment.

Under the simulator every ``ClientApp`` sees the same cohort, so the tutorials call ``partition_cohort`` (from
``flip.flower.identity``) to take their own slice. On the platform this does nothing, because each Trust already
serves only its own rows.

**Full-fidelity checks: the end-to-end smoke test, or the Flower containers.** The simulator cannot exercise the FL
images, the FL API submit path or SuperNode registration. The end-to-end smoke tests cover those, against the
running dev stack, as for FLARE:

.. code-block:: shell

   make e2e_smoke FL_BACKEND=flower                       # the X-ray app
   make -C flip-api e2e_smoke_spleen FL_BACKEND=flower    # likewise e2e_smoke_spleen_evaluation, e2e_smoke_prostate
   make e2e_smoke_ehr FL_BACKEND=flower

To run just the Flower containers — a SuperLink, two SuperNodes and an FL API, with the dev data mounted:

.. code-block:: shell

   cd fl-services/flower
   make build                                 # fl-base / superlink / supernode images
   make up                                    # fl-api, superlink, supernode-1, supernode-2
   make submit APP=3d_spleen_segmentation     # POSTs to the fl-api inside the container

A tutorial submitted this way runs with its ``pyproject.toml`` values, not its ``app/config.toml``.

.. tip::

   Prefer ``make run-tutorial`` to a bare ``flwr run .``. The wrapper sets the data variables, the site count and the
   checkpoint path that a bare run needs by hand. Also, ``flwr run`` executes the ``ClientApp`` from a snapshot under
   ``~/.flwr/apps/``, so paths you set after launch are not seen.

.. seealso::

   ``fl-tutorials/flower/3d_spleen_segmentation/README.md`` has the full local-run instructions and remaining
   caveats, including the data-enrichment (label upload) step the segmentation tutorials need before they can train
   on a real FLIP project. ``flip-api/AGENTS.md`` documents the end-to-end smoke tests.

***************************
Common pitfalls
***************************

- **Missing** ``RESULTS_UPLOADED``. Without the final ``flip.update_status(model_id, ModelStatus.RESULTS_UPLOADED)``
  in a local ``ServerApp``, the model stays "running" in the UI.
- **Calling** ``flip.send_metrics`` / ``flip.update_status`` **from the** ``ClientApp``. SuperNodes hold no Central Hub
  credentials. Put metrics in the reply ``MetricRecord`` and the site in the ``ConfigRecord``; the server forwards
  them.
- **Wrong or missing** ``site``. It must be the Trust's **FL kit slot** (``Trust_1``, ``Trust_2``, …), not its
  display name. Any other value lands under ``unknown_client``; a reply with no ``site`` is dropped. Use
  ``client_identity(context)`` — reading ``SUPERNODE_NAME`` directly records ``unknown_client`` under the simulator,
  where the variable is absent.
- **No DP mod.** A ``client_app.py`` without ``@app.train(mods=[flip_local_dp_mod])`` shares raw model updates. The
  platform does not enforce it.
- **Run configuration in the wrong file.** On Flower, ``config.json`` carries only ``job_type``. Rounds, epochs and
  hyperparameters for a platform run go in ``config.toml``. Without one, the app runs three rounds of one epoch,
  whatever your own ``pyproject.toml`` says.
- **Declaring** ``flip-utils`` **as a dependency.** In a local ``flwr run`` it comes from PyPI and hides the
  ``flip-utils`` the run should use. Leave it undeclared; on the platform the app always imports the FL image's copy.
- **Undeclared run-config keys.** Flower — and therefore FLIP — can only override keys already declared in
  ``[tool.flwr.app.config]``. Declare ``flip-model-id``, ``flip-project-id``, ``flip-cohort-query``,
  ``flip-job-dir`` and ``flip-min-clients`` with placeholder values in your own ``pyproject.toml``, or a local run
  fails. On the platform, the template's ``pyproject.toml`` (under ``fl-apps/flower/<job_type>/``) is what FLIP's
  overrides and your ``config.toml`` are checked against; an uploaded ``pyproject.toml`` is not used.
- **Missing** ``ResourceType``. If a Trust lacks the requested resource type for an accession,
  ``get_by_accession_number`` raises. Wrap the call in ``try / except`` and skip that accession, so one bad study
  does not abort the round.
