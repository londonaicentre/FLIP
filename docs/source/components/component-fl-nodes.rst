.. _flip-fl-nodes:

#########################
Federated Learning Nodes
#########################

FLIP supports two federated learning frameworks: NVFLARE and Flower AI. 
In both settings, the minimum components are:

- FL server: orchestrates the training process across sites and performs the aggregation of weights. It also uploads results at the end of the training.
- FL client: performs local training on the data at each site and sends the weights to the server.
- FL API: launches the training and acts as interface between the other FL components and the Central Hub API.

The FL API and Server are hosted at the Central Hub (in the Cloud), while client nodes are launched in the sites participating in a specific project (Cloud or on-premise).

.. figure:: ../../images/nodes_fl.jpg
   :alt: Diagram of federated learning nodes showing FL server, FL clients, and FL API interactions.
   :width: 300px
   :align: center
   
   Depiction of the FL nodes and the services they communicate with.

Only the client will be running deep learning training, and therefore, requires access to GPU units.

Job types 
------------------------

Due to security restrictions, FLIP users are not allowed to control what happens on the server side.
Although most adjustable aspects of machine learning training happen on the client side 
(e.g. dataloading, training loop, model architecture), FLIP provides different job types
that the user can choose based on their needs.
Which job types are available depends on the backend.
**Both backends** offer federated averaging (job type `standard`) and an evaluation task
(job type `evaluation`) — for a Flower app, those two are the whole set.
**NVFLARE** adds federated optimisation (job type `fed_opt`) and diffusion model training
(job type `diffusion_model`), which covers multi-stage federated training, along with three further
job types that drive the client code through the modern **NVFLARE Client API**
(a plain training/evaluation script using ``nvflare.client`` instead of a class-based ``Executor``):
federated averaging (job type `standard_client_api`), model evaluation (job type `evaluation_client_api`)
and two-stage diffusion model training (job type `diffusion_model_client_api`).
The manifests under :ref:`fl-required-files` are the authoritative list for each backend.
More job types will be added in the future, adjusting to the community's needs.

**How to choose a job type?**

A federated learning job is an ensemble of files (among which we can find `python`, `json` or `toml` files)
we call an app. Some of these files are required to run the app, and some are optional — which ones
are required depends on the job type (see :ref:`fl-required-files` below).

The job type is passed as key `job_type` in the `config.json` file (for both NVFLARE and Flower apps).
An unrecognised value is rejected at submission. A missing one falls back to ``standard``, as does an
app carrying no ``config.json`` at all — which is a valid Flower submission, since ``config.json`` is a
required file for the NVFLARE job types but not for the Flower ones (see :ref:`fl-required-files`).

Once uploaded, the UI will indicate which files are required for the specific job.

Then, the Central Hub API will take care of bundling together:
- The files the user has uploaded
- The static (non-modifiable) files that are required for the specific job type.

For more information about currently supported apps, see the per-job-type implementations under
`fl-apps/ <https://github.com/londonaicentre/FLIP/tree/develop/fl-apps>`_.

Examples of how the same job type (standard -> federated averaging) can run different user-uploaded applications are:

- `xray_classification <https://github.com/londonaicentre/FLIP/tree/develop/fl-tutorials/nvflare/image_classification/xray_classification>`_
- `3d_spleen_segmentation <https://github.com/londonaicentre/FLIP/tree/develop/fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation>`_

Both cases perform a supervised federated averaging training, but the data, architecture and training configuration are different.

The NVFLARE Client API job types have their own tutorials:

- `xray_classification_client_api <https://github.com/londonaicentre/FLIP/tree/develop/fl-tutorials/nvflare/image_classification/xray_classification_client_api>`_ (job type `standard_client_api`)
- `3d_spleen_segmentation_evaluation_client_api <https://github.com/londonaicentre/FLIP/tree/develop/fl-tutorials/nvflare/image_evaluation/3d_spleen_segmentation_evaluation_client_api>`_ (job type `evaluation_client_api`)
- `latent_diffusion_model_client_api <https://github.com/londonaicentre/FLIP/tree/develop/fl-tutorials/nvflare/image_synthesis/latent_diffusion_model_client_api>`_ (job type `diffusion_model_client_api`)

These tutorials run on the local NVFLARE simulator from the repo root — e.g.
``make -C fl-tutorials run-tutorial TUTORIAL=xray_classification`` (requires a GPU and the
``flare-fl-base`` image; see the
`fl-tutorials/ <https://github.com/londonaicentre/FLIP/tree/develop/fl-tutorials/nvflare>`_ README).


.. figure:: ../../images/job_types.jpg
   :alt: Example figure
   :width: 300px
   :align: center
   
   Workflow of how the user uploads files for a specific job type.

.. _fl-required-files:

Required files per job type
---------------------------

Each job type declares its own set of required files. A submission missing any of them is rejected
before anything is shipped to a Trust, with a message naming the missing files.

The manifests that decide this are included below **directly from the repository** rather than
transcribed, so this page cannot fall out of step with the platform. Each key is a job type and its
array is the exact set of files that job type requires.

Each backend's manifest is generated from the per-template
``fl-apps/<backend>/<job_type>/required_files.json`` files by ``fl-apps/check_required_files.sh``,
which runs as a pre-commit hook and is enforced in CI — so adding a job type or changing its
required set updates this page as a side effect of the change itself. The FLIP UI reads the same
manifests through the ``/model/job-types`` endpoint and shows the applicable list on the model page.

.. literalinclude:: ../../../fl-apps/nvflare/required_files.json
   :language: json
   :caption: ``fl-apps/nvflare/required_files.json`` — required files per NVFLARE job type

.. literalinclude:: ../../../fl-apps/flower/required_files.json
   :language: json
   :caption: ``fl-apps/flower/required_files.json`` — required files per Flower job type

.. note::

   Every NVFLARE job type lists ``config.json``, which carries ``job_type`` along with the training
   configuration below. The Flower job types do not require it, but uploading one is still how a
   Flower app selects a job type other than ``standard``.

   ``pyproject.toml`` is **not** a file the researcher supplies for a Flower app. It is part of the
   platform's base template for the job type and is bundled automatically; a ``pyproject.toml``
   uploaded as a model file does not become the app's project file. Per-run overrides go in
   ``config.toml`` instead (see below).

Beyond the required set, additional files uploaded with the model are bundled into the app, which is
how apps ship helper modules and transforms. Two exceptions are worth knowing:

- **A file whose name collides with one the base template already provides is silently dropped.**
  The template's copy wins and the upload is skipped with only a server-side log line — no error
  reaches the UI. For Flower this covers ``server_app.py``, ``strategy.py``, ``__init__.py`` and
  ``pyproject.toml``, which matters in practice because the Flower tutorials ship the first three in
  their app directories; for NVFLARE it covers whatever the chosen job type's template already
  contains.
- **A checkpoint declared for server-side use is not shipped to the Trusts.** A file named by
  ``SERVER_CHECKPOINT``, or by an evaluation job's ``models`` entries, is staged on the FL server
  rather than placed in the app bundle, so it never travels to a client. This is deliberate: it
  keeps large weights off the client deployment path.

.. _fl-training-configuration:

Training configuration
----------------------

NVFLARE ``config.json``
~~~~~~~~~~~~~~~~~~~~~~~

For NVFLARE job types, ``config.json`` carries both the job type and the platform-recognised
training settings. Every setting has a default, so an app that declares only ``job_type`` is valid.

.. code-block:: json

    {
      "job_type": "standard",
      "GLOBAL_ROUNDS": 5,
      "LOCAL_ROUNDS": 2,
      "IGNORE_RESULT_ERROR": false,
      "AGGREGATOR": "InTimeAccumulateWeightedAggregator",
      "AGGREGATION_WEIGHTS": {
         "Trust_1": 1.0,
         "Trust_2": 0.5
      }
    }

**GLOBAL_ROUNDS**
   Number of global (server-side) rounds — how many times the server distributes the global model,
   collects client updates and aggregates them. Accepted range 1–1000 inclusive.

   *Default = 1*

**LOCAL_ROUNDS**
   Number of local training iterations performed at each client site per global round. Accepted
   range 1–1000 inclusive.

   *Default = 1*

   .. note::

      A value that is not a number, or that falls outside the accepted range, is discarded rather
      than reported. What happens next differs between the two keys, because the platform consumes
      them differently.

      ``GLOBAL_ROUNDS`` is read by the platform and written into the server's job configuration, so
      a discarded value really does produce a **one-round run**. A single-round job is easy to
      mistake for a broken one, so check the value that reached the job if training finishes sooner
      than expected. The exception is ``BEST_MODEL_METRIC``: because it requires at least two global
      rounds, a discarded ``GLOBAL_ROUNDS`` fails the job outright instead of defaulting.

      ``LOCAL_ROUNDS`` is **not** read by the platform — it is read by your own trainer, straight out
      of ``config.json``. The default of 1 is filled in only when the key is *absent*; a key that is
      present but invalid is left exactly as written, so the value reaches your trainer verbatim.
      ``"LOCAL_ROUNDS": 5000`` will run 5000 local iterations, and a non-numeric value will fail
      inside your own code rather than at validation.

      Neither key is rewritten in the ``config.json`` deployed with the app, so app code that reads
      ``GLOBAL_ROUNDS`` from that file sees the value you wrote, not the value the server is using.

**IGNORE_RESULT_ERROR**
   Whether training should proceed when a client returns an error.

   *Default = false*

**AGGREGATOR**
   The NVFLARE aggregation component used to combine client updates. FLIP only supports aggregator
   components built into NVFLARE; allowed values are ``InTimeAccumulateWeightedAggregator`` and
   ``AccumulateWeightedAggregator``. Any other value fails config validation and prevents training
   from starting.

   *Default =* ``InTimeAccumulateWeightedAggregator``

**AGGREGATION_WEIGHTS**
   JSON object mapping a client site to the weight its update carries in aggregation. Weights must
   be numbers between 0 and 1 inclusive; a non-object value, or a weight outside that range, fails
   config validation.

   .. warning::

      The keys are **FL client site names** — the FL kit slot each Trust occupies (``Trust_1``,
      ``Trust_2``, …), which is what the FL server knows a client by. They are not the Trust's
      hub-side name or code. A key that matches no participating client is simply unused, so a
      mistyped key produces no error and no effect.

   *Default = no weights supplied, which NVFLARE treats as a weight of 1.0 for every participating
   client*

**AGGREGATE_ONLY_REGEX**
   Regular expression matching the model-parameter names to keep in each client update. When set,
   only matching parameters are sent back to the server — for example, a fine-tune with a frozen
   backbone can ship just its head instead of the full model. Must be a valid regular expression.

   *Default = unset, i.e. the full model update is sent*

**BEST_MODEL_METRIC** / **BEST_MODEL_METRIC_MINIMIZE**
   Validation-metric label driving best-global-model selection. When set, the best global model is
   saved alongside the final one and included in the results, and the client trainer must report a
   metric under this label. Set ``BEST_MODEL_METRIC_MINIMIZE`` to ``true`` for loss-like metrics
   where lower is better. Because selection cannot happen before the first aggregate exists,
   ``BEST_MODEL_METRIC`` requires ``GLOBAL_ROUNDS`` of at least 2, and setting
   ``BEST_MODEL_METRIC_MINIMIZE`` on its own is rejected.

   *Default = unset, i.e. only the final model is saved*

Three further keys are read by FLIP's own NVFLARE components rather than by the FL API's validator,
so they are unvalidated but are not simply passed through either: ``SERVER_CHECKPOINT`` (names a
model file to stage on the FL server instead of bundling it into the app — a string or a list of
strings), ``GLOBAL_ROUNDS_AE`` and ``GLOBAL_ROUNDS_DM`` (per-phase round counts for the two-phase
``diffusion_model`` job types, which bypass the range check applied to ``GLOBAL_ROUNDS``), and
``models`` (the checkpoints an evaluation job loads, each entry naming a ``checkpoint`` file).

Any other key the platform does not recognise is passed through to the app untouched, for the
uploaded code to read at runtime. This is how the tutorials carry app-specific settings such as
``LEARNING_RATE`` or ``VAL_SPLIT`` — those are conventions of individual apps, not platform
settings, and they are neither validated nor defaulted. A misspelled key of this kind is therefore
silently absent at runtime rather than reported as an error.

Flower run configuration
~~~~~~~~~~~~~~~~~~~~~~~~

Flower apps do not use the NVFLARE keys above. Their run configuration lives in the
``[tool.flwr.app.config]`` table of the base template's ``pyproject.toml``, which the platform
supplies. An app overrides those values with a ``config.toml`` file uploaded alongside its code:
the FL API passes it to ``flwr run --run-config`` at submission, and the FLIP runtime parameters
(model id, project id, cohort query, job directory) are injected into it automatically.

``config.toml`` can only override keys that the template's ``pyproject.toml`` already declares — it
cannot introduce new ones. Round counts follow Flower's naming rather than NVFLARE's, as
``num-server-rounds`` (global rounds) and ``local-epochs`` (local rounds).

For a worked example of both files, see the Flower tutorials under
`fl-tutorials/flower/ <https://github.com/londonaicentre/FLIP/tree/develop/fl-tutorials/flower>`_
and :doc:`/working-with-flip-apps/create-flip-app-from-flower`.

Data access and communication with external services
----------------------------------------------------

Though the user is allowed to upload the training script that will run on the client side, the access to data will have
to be via the FLIP package (see `https://github.com/londonaicentre/FLIP/tree/develop/flip-utils/flip`).
This package, installed by default in client and server nodes, will make a series of functions available to the user. 

For data access:
- `flip.get_dataframe(project_id, query)`: retrieves the dataframe linked to the project ID and query that have been used on the project.
- `flip.get_by_accession_number(project_id, accession_id, resource_type)`: retrieves data of a certain type (e.g. NIFTI) associated with an accession ID. ``resource_type`` defaults to ``ResourceType.NIFTI`` and can be a single type or a list.

These calls - among others - communicate with the Imaging API and retrieve the data from the project's XNAT.

For communication with the Central Hub:
- `flip.update_status(model_id, new_model_status)`: these calls will update the Central Hub about status on the specific model that is running (example: when it started training, or if there's an error).
- `flip.send_metrics(client_name, model_id, label, value, global_round, x_value=None, x_label=None)`: sends a metric to the central hub so that it can plot the training results. ``global_round`` is provenance — always the FL global round the metric is reported in. Where the point is *plotted* is the optional coordinate pair: ``x_value`` is the x-coordinate (any float, e.g. an epoch counter) and ``x_label`` names the x-axis (e.g. ``"epoch"``); both default to the global round on the "Global Rounds" axis. A plot is identified by the ``(label, x_label)`` pair, so the same metric logged against different x-labels is shown as separate plots.
- `flip.send_event(model_id, event_type, global_round, ...)`: sends a typed round-progress **fact** to the Central Hub — one of ``ROUND_STARTED``, ``CLIENT_RESULT_RECEIVED`` (with the serialized update size in ``details.size_bytes``) or ``ROUND_AGGREGATED`` (with ``returned``/``expected`` counts). The hub composes the display text shown in the model page's Live activity feed at serve time, so wording changes ship with a flip-api redeploy and never require rebuilding FL images. Rounds are 1-based on both backends.

The fl-server emits these events automatically — NVFLARE via the FLIP ``ScatterAndGather``/``ServerEventHandler`` components (wired by path in each template's server config, so no app-template changes were required), Flower via the ``flip.flower.strategy.FlipFedAvg`` base strategy the app templates subclass. User training code never calls ``send_event`` directly. Pre-existing **Flower** apps (whose uploaded strategy subclasses stock ``FedAvg``) keep working and simply emit no round telemetry; pre-existing **NVFLARE** apps reference the FLIP components by path from the baked ``flip`` package, so they start emitting as soon as the fl-server image carries this version — with no app change.

Note the reported upload sizes measure slightly different things per backend — NVFLARE sums the in-memory tensor sizes of the client's (possibly partial) weight update, Flower sums the serialized array buffers — each internally consistent within a run.

The server will also use the package to update the status, as well as to upload the final results, which will be first saved in the server, to the final S3 buckets users can download from.


Privacy filters on shared model updates
---------------------------------------

Before a client's training result leaves a site, the NVFLARE training job types (`standard`, `fed_opt`,
`diffusion_model`, `standard_client_api`, `diffusion_model_client_api`) pass it through a percentile-based
privacy filter (``PercentilePrivacy``, following Shokri & Shmatikov, "Privacy-preserving deep learning",
CCS '15; the diffusion job types use the stage-aware ``StagePercentilePrivacy`` subclass, which computes
the cutoff per training stage):

- weight-diff components with magnitude **below** the ``percentile``-th percentile are zeroed, so only the
  largest ``100 - percentile`` % of each update's components are shared;
- the surviving components are truncated to ``±gamma``.

FLIP ships the stock NVFLARE defaults — ``percentile=10``, ``gamma=0.01`` (share the top 90 %, clip at 0.01).
Both values can be tuned per app in the job's ``config_fed_client.json`` (or via
``FlipFedAvgRecipe(percentile_privacy=...)`` for recipe-generated jobs), and the filter can be disabled for a
run with ``off: true``. Two caveats for anyone changing them:

- **Raising** ``percentile`` **sharply degrades training.** At e.g. 95 only the top 5 % of every update
  survives, which stalls FedAvg convergence; on a frozen-backbone (head-only) finetune it silently resets the
  global head every round. For that reason the head-only ``KeepOnlyVars`` filter is always ordered before
  ``PercentilePrivacy``, so the percentile is computed over the trainable parameters only, never the frozen
  backbone's all-zero diffs.
- **This is a heuristic output filter, not formal differential privacy.** Sparsifying and clipping each shared
  update bounds what a single round reveals, but adds no calibrated noise and carries no
  ``(epsilon, delta)`` guarantee. It complements — rather than replaces — FLIP's primary output controls
  (review of the uploaded app code and aggregate-only results).


Disclaimer: some things are still under construction!
-----------------------------------------------------

There are currently some elements that are still under construction, and might not adjust exactly to 
the description above:

- for the class-based NVFLARE job types (``standard``, ``evaluation``, ``fed_opt``, ``diffusion_model``) the user upload is intentionally minimal — see :ref:`fl-required-files` for the per-job-type set — and the rest of the app is filled in from
  the static (non-modifiable) templates baked into the flip-api image at `FL_APP_BASE_DIR` (`fl-apps/`, see FLIP#724).
  These templates used to be published to an S3 bucket; that path has been removed. You can check what a fully bundled app looks like by consulting
  the per-job-type implementations under `fl-apps/ <https://github.com/londonaicentre/FLIP/tree/develop/fl-apps/nvflare>`_.
- the modern NVFLARE Client API job types (`standard_client_api`, `evaluation_client_api`, `diffusion_model_client_api`) instead let the user upload a plain training/evaluation script that calls
  ``nvflare.client`` directly. Over time, more job types will migrate to this recipe-driven model.

