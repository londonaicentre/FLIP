###################################
Flower app walkthrough for FLIP
###################################

.. warning::

   This page assumes you are familiar with the :ref:`flip-fl-nodes` component page and with the project / model lifecycle described in :doc:`user-common`. Before starting, you must already have: a FLIP account with the ``researcher`` role, an approved project with a saved cohort query, and a model created under that project (so you have a ``flip-model-id``).

This page walks through the code changes required to adapt a stock Flower app (for example, one copied from the official `Flower quickstart examples <https://flower.ai/docs/examples.html>`_ or pulled from the internal FLIP app hub) so that it can run on FLIP. The walkthrough covers the four first-class SDK calls a FLIP-compatible app needs:

- ``flip.update_status(...)`` to signal model lifecycle transitions to the central hub
- ``flip.get_dataframe(...)`` to retrieve the project's cohort DataFrame
- ``flip.get_by_accession_number(...)`` to pull imaging resources for each accession
- ``flip.send_metrics(...)`` to push per-round training metrics back to the FLIP UI

A full, runnable reference is available in the `flip-fl-base-flower <https://github.com/londonaicentre/flip-fl-base-flower>`_ repository:

- ``src/standard/app/`` — a minimal training-only ``ServerApp`` / ``ClientApp`` template.
- ``tutorials/monai/`` — a MONAI spleen-segmentation example that exercises every SDK call covered below.

*****************
Starting point
*****************

A Flower app that FLIP can run is just a standard Flower project with a ``pyproject.toml`` that declares a ``ServerApp`` and a ``ClientApp``. The minimum folder layout is:

.. code-block:: text

   my-flower-app/
   ├── app/
   │   ├── __init__.py
   │   ├── client_app.py
   │   └── server_app.py
   └── pyproject.toml

The ``pyproject.toml`` wires the two entry points together:

.. code-block:: toml

   [tool.flwr.app.components]
   serverapp = "app.server_app:app"
   clientapp = "app.client_app:app"

If you are starting from scratch, copy ``src/standard/`` from ``flip-fl-base-flower`` as a template — it already contains the minimum FLIP integration described below.

.. note::

   The ``flip-utils`` package (published on PyPI, imported as ``flip``) is pre-installed in every FLIP FL node image. You only need to declare it as a dependency in your ``pyproject.toml`` for local development — you do not have to vendor or install it at run time.

*******************************************
ServerApp: reporting model lifecycle status
*******************************************

On the server side, the FLIP integration is a single object (``FLIP``) injected into the ``@app.main()`` function plus four status transitions around ``strategy.start(...)``.

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

   strategy.start(grid=grid, initial_arrays=arrays, num_rounds=num_rounds)

   flip.update_status(model_id, ModelStatus.TRAINING_STARTED)   # after strategy.start returns
   flip.update_status(model_id, ModelStatus.RESULTS_UPLOADED)   # after post-training finalisation

.. warning::

   Forgetting the final ``ModelStatus.RESULTS_UPLOADED`` transition will leave the model indefinitely in the "training" state in the FLIP UI even though execution has ended.

Full minimal example
=====================

Reproduced from ``flip-fl-base-flower/src/standard/app/server_app.py``:

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

       strategy.start(grid=grid, initial_arrays=arrays, num_rounds=num_rounds)

       flip.update_status(flip_model_id, ModelStatus.TRAINING_STARTED)
       flip.update_status(flip_model_id, ModelStatus.RESULTS_UPLOADED)

***********************
Strategy considerations
***********************

Most stock Flower strategies (``FedAvg``, ``FedProx``, and the other built-ins) work unchanged on FLIP because the FLIP touchpoints live in the ``ServerApp`` callable, not in the strategy itself. You can pick any strategy the `flwr.serverapp.strategy <https://flower.ai/docs/framework/ref-api/flwr.serverapp.strategy.html>`_ module exposes.

If you subclass a strategy to add custom aggregation or post-round hooks, the ``flip`` and ``model_id`` values bound inside ``main()`` are free to close over: pass them into the subclass constructor and call ``flip.update_status`` or ``flip.send_metrics`` from aggregation callbacks as needed.

.. code-block:: python

   class MyStrategy(FedAvg):
       def __init__(self, *args, flip: FLIP, model_id: str, **kwargs):
           super().__init__(*args, **kwargs)
           self._flip = flip
           self._model_id = model_id

       def aggregate_train(self, server_round, replies):
           result = super().aggregate_train(server_round, replies)
           # push any custom server-side signals here
           return result

****************************************
ClientApp: fetching the FLIP DataFrame
****************************************

On the client side, the cohort DataFrame is fetched once at the start of each training round via ``flip.get_dataframe``. The call runs against the project's saved cohort query (see :ref:`create-cohort-query`) and returns a pandas ``DataFrame`` containing at minimum an ``accession_id`` column plus whatever other columns the SQL ``SELECT`` projected.

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

The MONAI tutorial bundles the FLIP client with cohort metadata into a small helper class. This makes it easier to thread data-loading state through the ``@app.train()`` callable:

.. code-block:: python

   import logging

   from flip import FLIP


   class FLIP_BASE:
       def __init__(self):
           self.project_id = ""
           self.query = ""
           self.dataframe = None
           self.flip = FLIP()
           self.logger = logging.getLogger(self.__class__.__name__)
           self.logger.setLevel(logging.INFO)

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

Once you have the cohort DataFrame, iterate its ``accession_id`` column and call ``flip.get_by_accession_number`` to pull the imaging resources for each study. The call returns a ``pathlib.Path`` pointing at a local directory containing files named ``input_*.nii.gz`` (and, if you also requested ``ResourceType.SEGMENTATION``, ``label_*.nii.gz``).

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

   Always wrap ``get_by_accession_number`` in a ``try / except`` loop and ``continue`` on failure. A single trust may be missing a resource type for a specific accession (for example, a study with imaging but no segmentation), and you should not abort the whole round on one bad accession.

.. note::

   ``ResourceType`` is an enum in ``flip.constants``. The samples use ``ResourceType.NIFTI`` and ``ResourceType.SEGMENTATION``; you may pass either a single value or a list.

***************************************************
ClientApp: sending per-round metrics to the hub
***************************************************

Per-round, per-client metrics are pushed to the central hub with ``flip.send_metrics``. This is what populates the graphs on the model page (see the "Metrics" section of :doc:`user-common`). This push is independent of any ``MetricRecord`` you return inside the Flower ``Message`` — the ``MetricRecord`` is for in-network aggregation by the strategy, while ``send_metrics`` is for FLIP UI surfacing.

.. code-block:: python

   import os

   client_name = os.getenv("SUPERNODE_NAME", "unknown_client")
   model_id = run_config.get("flip-model-id")

   # global_round from server is 1-based; convert to 0-based for local epoch arithmetic
   global_round = int(msg.content["config"]["server-round"]) - 1

   for epoch in range(local_epochs):
       train_loss = train_func(...)
       val_dice, val_loss = validate_func(...)

       round_num = global_round * local_epochs + epoch + 1
       flip_utils.flip.send_metrics(client_name, model_id, label="TRAIN_LOSS", value=train_loss, round=round_num)
       flip_utils.flip.send_metrics(client_name, model_id, label="VAL_LOSS",   value=val_loss,   round=round_num)
       flip_utils.flip.send_metrics(client_name, model_id, label="VAL_DICE",   value=val_dice,   round=round_num)

.. warning::

   ``SUPERNODE_NAME`` must match the trust name registered in the central-hub database exactly, otherwise metrics will be recorded against ``unknown_client`` and will not be attributable to the site in the FLIP UI. The FLIP-provisioned SuperNode images set ``SUPERNODE_NAME`` for you; if you are running locally you must export it yourself.

.. note::

   Use stable, upper-case ``label`` strings (``TRAIN_LOSS``, ``VAL_LOSS``, ``VAL_DICE`` are the conventions used in the MONAI tutorial) so that metrics from repeated runs line up on the same chart in the UI.

************************************
Wiring it up via ``pyproject.toml``
************************************

The ``pyproject.toml`` of a FLIP-compatible Flower app needs three things beyond a normal Flower project:

1. ``flip-utils`` declared as a dependency.
2. A ``[tool.flwr.app.config]`` block declaring the FLIP run-config keys, even if the values are placeholders.
3. Training hyperparameters (``num-server-rounds``, ``local-epochs``, etc.) declared in the same block so you can override them via ``flwr run . --run-config "key=value"`` locally.

Abridged from ``flip-fl-base-flower/tutorials/monai/pyproject.toml``:

.. code-block:: toml

   [project]
   name = "quickstart-monai"
   version = "1.0.0"
   dependencies = [
       "flip-utils>=0.1.2",
       "flwr[simulation]>=1.26.1",
       # ... your model-framework deps (monai, torch, nibabel, ...)
   ]

   [tool.flwr.app]
   publisher = "flwrlabs"

   [tool.flwr.app.components]
   serverapp = "app.server_app:app"
   clientapp = "app.client_app:app"

   [tool.flwr.app.config]
   num-server-rounds = 3
   local-epochs = 1
   learning-rate = 1e-4
   batch-size = 2
   # FLIP-injected keys — declare placeholders so FLIP can override at submit time
   flip-model-id = "uuid"
   flip-project-id = "uuid"
   flip-cohort-query = "*"

************************************
Submitting the app to FLIP
************************************

Once your app runs locally (see the next section), upload it through the FLIP UI's model page the same way you would upload a FLARE app. FLIP validates the required files for a Flower app (which differ from those required for a FLARE app — see the "Model Files" subsection of :doc:`user-common` and the :ref:`flip-fl-nodes` page for the canonical list) and then lets you click **Initiate Training**.

At submit time, the FLIP FL API:

- Injects ``flip-model-id``, ``flip-project-id``, and ``flip-cohort-query`` into your app's run config.
- Sets ``SUPERNODE_NAME`` on each participating trust's SuperNode container.
- Starts the ``ServerApp`` on the Central Hub's SuperLink and the ``ClientApp`` on each approved trust's SuperNode.

************************************
Local testing before upload
************************************

From inside your app's root directory:

.. code-block:: bash

   pip install -e .
   flwr run .

The MONAI tutorial also supports offline smoke-tests by pointing the FLIP helpers at a local CSV + NIfTI directory via the ``DEV_DATAFRAME`` and ``DEV_IMAGES_DIR`` environment variables:

.. code-block:: bash

   DEV_DATAFRAME="../../data/spleen/sample_get_dataframe_response.csv" \
   DEV_IMAGES_DIR="../../data/spleen/accession-resources" \
   flwr run .

See ``flip-fl-base-flower/tutorials/monai/README.md`` for the full local-run instructions.

***************************
Common pitfalls
***************************

- **Missing ``RESULTS_UPLOADED``.** Forgetting the final ``flip.update_status(model_id, ModelStatus.RESULTS_UPLOADED)`` call leaves the model stuck on "training" in the UI.
- **Wrong ``SUPERNODE_NAME``.** Metrics pushed with a name that does not match the trust's central-hub registration land under ``unknown_client`` and will not appear on the per-site chart.
- **Undeclared run-config keys.** ``flwr run`` (and therefore FLIP's FL API) can only override keys already declared in ``[tool.flwr.app.config]``. Declaring ``flip-model-id``, ``flip-project-id``, and ``flip-cohort-query`` with placeholder values is mandatory even though the real values are injected by FLIP.
- **Missing ``ResourceType``.** If a trust does not have the resource type you requested for a given accession, ``get_by_accession_number`` will raise. Always wrap the call in ``try / except`` and skip the accession on failure so a single bad study does not abort the whole round.
