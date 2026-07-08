.. Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at
       http://www.apache.org/licenses/LICENSE-2.0
   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.

##################################
MLflow experiment tracking (dev)
##################################

The development stack ships an `MLflow <https://mlflow.org>`_ tracking server
(``flip-mlflow``) that mirrors federated training telemetry for researchers:
cross-run metric comparison, run parameters, and a model registry linking each
uploaded results zip to the exact run that produced it.

Additive dual-write
===================

MLflow **complements** the canonical pipeline — it never replaces it and is
never in the training critical path:

- flip-api's database remains the source of truth for model status and the
  metrics the FLIP UI charts.
- The fl-server (via the ``flip`` package's ``MlflowSink``) and flip-api (via
  ``mlflow_run_service``) additionally mirror to MLflow, *best-effort*: every
  MLflow failure is swallowed and logged as a warning, HTTP timeouts are
  bounded, and an unset ``MLFLOW_TRACKING_URI`` disables the integration
  entirely with zero behaviour change.

What gets recorded
==================

===========================  ====================================================================
FLIP concept                 MLflow concept
===========================  ====================================================================
Model                        Experiment ``flip/<model_id>`` (tagged with model name + project id)
FL job (one training run)    Run ``job-<fl_job_id>``, pre-created by flip-api at submit with
                             tags (``flip.fl_job_id``, ``flip.backend``, ``flip.job_type``,
                             ``flip.trusts``…) and the ``config.json`` hyperparameters as params
Per-trust metric             Metric ``<LABEL>/<fl_client_name>``, step = global round
Status transition            Tag ``flip.status``; terminal statuses end the run
                             (``RESULTS_UPLOADED`` → FINISHED, ``ERROR`` → FAILED,
                             ``STOPPED`` → KILLED)
Results zip                  Registered model version of ``flip-model-<model_id>``
===========================  ====================================================================

.. important::

   **Model weights are never copied into MLflow.** A registered model version's
   *source* is the S3 URI of the results zip that ``upload_results_to_s3``
   already wrote — a metadata reference. Downloads keep going through the hub's
   ``GET /files/model/{id}/fl/results`` endpoint. (Consequently the MLflow UI
   shows the version and its source URI but cannot stream the artefact itself.)

Security model (dev)
====================

MLflow OSS has no authentication of its own, so access control is enforced by
network placement:

- The UI is published on ``127.0.0.1:${MLFLOW_PORT}`` only — hub administrators
  on the dev host.
- The container joins ``central-hub-network`` (flip-api) and a dedicated
  hub-only bridge ``central-hub-mlflow-network`` (fl-servers). It must **never**
  join ``shared-net-1/2``: the trust fl-clients live there, and keeping
  trust-side containers off MLflow's networks *is* the security boundary.
  fl-clients keep zero hub credentials, exactly as before.

Using it
========

.. code-block:: bash

   make up            # full stack — MLflow starts automatically
   make mlflow        # or: just the MLflow server (e.g. for tutorial plotting)

Browse http://localhost:5000. Training initiated through FLIP appears as
described above; simulator/tutorial runs can also be mirrored — see
"Running the FL tutorials" in the repository README.

Configuration
=============

===========================  ==========================================================
Variable                     Meaning
===========================  ==========================================================
``MLFLOW_TRACKING_URI``      Tracking server URI seen by fl-server and flip-api
                             (``http://mlflow:5000`` in dev). **Empty = integration
                             disabled** — the default everywhere outside dev.
``MLFLOW_PORT``              Host loopback port for the UI (default ``5000``).
===========================  ==========================================================

The dev server stores runs in SQLite on a named volume (``mlflow_data``) — a
deliberate single-writer spike setup. Before any production or multi-writer
use, move the backend store to a proper database and put real authentication
in front (production hosting is a separate decision — see FLIP#745, WP6).
Production compose files do not deploy MLflow.
