# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Recipe describing a FLIP evaluation job that drives the evaluator via the NVFLARE Client API.

This is the Client-API counterpart of the legacy ``evaluation`` job type. Where the legacy app pairs
the ``RUN_EVALUATOR`` executor with a bespoke ``ModelEval`` + ``EvaluationPTModelLocator`` server flow
(broadcasting *all* uploaded checkpoints to clients as one ``DataKind.COLLECTION`` DXO), this recipe
evaluates a **single** uploaded model and reuses the same cross-site validation path the
:class:`~flip.nvflare.recipes.FlipFedAvgRecipe` already drives for its ``validate`` phase:

* server: :class:`~flip.nvflare.controllers.CrossSiteModelEval` (with ``submit_model`` disabled, so it
  only validates the server-provided model) loads the uploaded checkpoint via
  :class:`~flip.nvflare.components.EvaluationModelLocator` and broadcasts it to every client as a single
  ``FLModel``; :class:`~flip.nvflare.components.EvaluationJsonGenerator` collects the returned metrics
  into ``evaluation_results.json`` (unchanged output contract); ``PersistToS3AndCleanup`` zips + uploads
  the run dir to S3.
* client: the stock ``InProcessClientAPIExecutor`` runs the evaluator script, which is the canonical
  Client-API ``is_evaluate()`` loop — receive the global model, score it on local data, send back an
  aggregate-only metrics ``FLModel``. No ``Executor``/``Shareable`` plumbing, no ``COLLECTION`` unwrap.

The recipe runs identically across SimEnv/PocEnv/ProdEnv and exports the standard NVFLARE FedJob layout
for the FLIP-API, exactly like :class:`FlipFedAvgRecipe`. ``model_id`` is resolved lazily by the FLIP
components from ``meta.json['custom_props']['model_id']`` (written here for SimEnv/PocEnv; overwritten by
the FLIP-API in production).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nvflare import FedJob
from nvflare.app_common.executors.in_process_client_api_executor import InProcessClientAPIExecutor
from nvflare.app_opt.pt.file_model_persistor import PTFileModelPersistor
from nvflare.recipe.spec import Recipe

from flip.nvflare.components import (
    CleanupImages,
    ClientEventHandler,
    EvaluationJsonGenerator,
    EvaluationModelLocator,
    PersistToS3AndCleanup,
    ServerEventHandler,
)
from flip.nvflare.controllers import CrossSiteModelEval, InitEvaluation
from flip.nvflare.runtime import FLIP_CUSTOM_PROPS_KEY, FLIP_MODEL_ID_KEY

# Default UUID used by SimEnv/PocEnv runs when the caller doesn't pass one. Pinned so dev runs are
# reproducible. Production runs override this via meta.json['custom_props']['model_id'].
_DEV_MODEL_ID = "00000000-0000-0000-0000-000000000001"


class FlipEvalRecipe(Recipe):
    """FLIP evaluation recipe wired for the NVFLARE Client API.

    Args:
        min_clients: Minimum number of clients required for the evaluation.
        eval_script: Evaluator script path. ``"evaluator.py"`` is taken to mean ``"custom/evaluator.py"``
            inside the job; pass an explicit ``custom/`` prefix to override that convention.
        eval_args: Argument string for the evaluator. NVFlare's TaskScriptRunner whitespace-splits this —
            any FLIP-API placeholder used here must substitute into a single token. The SQL query is
            plumbed via the client config's top-level ``query`` key instead.
        model_id: Real UUID for SimEnv/PocEnv runs. Written into ``meta.json['custom_props']`` so FLIP
            components can resolve it lazily at first event/task. Production runs override this via the
            FLIP-API which writes the real model id into the same key.
        validation_timeout: NVFlare timeout (seconds) for the ``validate`` task.
        project_id, query: Top-level keys on the client config (consumed by the FLIP-API placeholder
            substitution and read by the evaluator at runtime).
        evaluate_task_name: NVFlare task name for the cross-site validation task (must match
            ``CrossSiteModelEval``'s ``validation_task_name``, default ``"validate"``).
        params_exchange_format, params_transfer_type: NVFlare param-exchange knobs for the Client API.
    """

    def __init__(
        self,
        *,
        min_clients: int = 1,
        eval_script: str = "evaluator.py",
        eval_args: str = "--project_id {project_id}",
        model_id: str = _DEV_MODEL_ID,
        validation_timeout: int = 12000,
        project_id: str = "",
        query: str = "SELECT * FROM Table;",
        evaluate_task_name: str = "validate",
        params_exchange_format: str = "numpy",
        params_transfer_type: str = "FULL",
    ):
        self.min_clients = min_clients
        self.eval_script = eval_script if eval_script.startswith("custom/") else f"custom/{eval_script}"
        self.eval_args = eval_args
        self.model_id = model_id
        self.validation_timeout = validation_timeout
        self.project_id = project_id
        self.query = query
        self.evaluate_task_name = evaluate_task_name
        self.params_exchange_format = params_exchange_format
        self.params_transfer_type = params_transfer_type

        super().__init__(self._build_fed_job())

    def _build_fed_job(self) -> FedJob:
        """Construct the FedJob NVFLARE's Recipe uses for ``execute(env)``.

        ``meta_props`` carries the FLIP runtime config (currently just ``model_id``) into the exported
        ``meta.json`` so the lazily-resolving components can find it.
        """
        job = FedJob(
            name="flip_evaluation",
            min_clients=self.min_clients,
            meta_props={FLIP_CUSTOM_PROPS_KEY: {FLIP_MODEL_ID_KEY: self.model_id}},
        )

        # Server: persistor (required by PersistToS3AndCleanup's inventory check — no model is trained
        # here, so its inventory stays empty and it simply zips the run dir).
        persistor_id = job.to_server(PTFileModelPersistor(model={"path": "models.get_model"}), id="persistor")

        # Server: FLIP components — single-model checkpoint locator, results JSON, event handler, S3 persistor.
        job.to_server(EvaluationModelLocator(), id="model_locator")
        job.to_server(EvaluationJsonGenerator(), id="json_generator")
        job.to_server(ServerEventHandler(), id="flip_server_event_handler")
        job.to_server(PersistToS3AndCleanup(persistor_id=persistor_id), id="persist_and_cleanup")

        # Server: workflows in execution order — init (status + config check + cleanup) → cross-site validate.
        # submit_model_task_name="" disables client model submission: we only validate the server-loaded model.
        job.to_server(InitEvaluation(min_clients=self.min_clients))
        job.to_server(
            CrossSiteModelEval(
                model_locator_id="model_locator",
                submit_model_task_name="",
                validation_task_name=self.evaluate_task_name,
                validation_timeout=self.validation_timeout,
            )
        )

        # Clients: cleanup executor for the init/post-validation tasks.
        job.to_clients(CleanupImages(), tasks=["init_task", "post_validation"])

        # Clients: Client API evaluator for the validate task.
        job.to_clients(
            InProcessClientAPIExecutor(
                task_script_path=self.eval_script,
                task_script_args=self.eval_args,
                evaluate_task_name=self.evaluate_task_name,
                params_exchange_format=self.params_exchange_format,
                params_transfer_type=self.params_transfer_type,
            ),
            tasks=[self.evaluate_task_name],
        )

        # Clients: client event handler (FLIP status/event plumbing).
        job.to_clients(ClientEventHandler(), id="flip_client_event_handler")

        return job

    def export(self, job_dir: str | Path, **_kwargs: Any) -> None:
        """Export the job using NVFLARE's standard FedJob layout.

        Produces ``<job_dir>/<job_name>/{meta.json, app/config/, app/custom/}`` — the same structure
        NVFLARE's admin client uploads to the cluster. The FLIP-API consumes this directory, optionally
        rewrites ``meta.json['custom_props']`` with the real model_id at submit time, and forwards the
        job to the fl-server stack.
        """
        self.job.export_job(str(job_dir))
        self._write_client_config_params(Path(job_dir))

    def _write_client_config_params(self, job_dir: Path) -> None:
        """Emit ``project_id`` / ``query`` as top-level keys on the exported ``config_fed_client.json``.

        These are not NVFLARE components, so they don't fall out of ``export_job`` — but the client
        contract expects them at the config top level: the evaluator's ``--project_id {project_id}`` arg
        resolves the ``{project_id}`` reference against the top-level ``project_id`` key, and
        ``evaluator.load_query()`` reads the top-level ``query``. We write the recipe's (placeholder)
        defaults so the exported template is self-documenting and the ``{project_id}`` reference always
        resolves. In production the fl-server's ``configure_client`` overwrites ``project_id`` / ``query``
        with the real submission values; in SimEnv/LOCAL_DEV they're ignored (data comes from the
        ``DEV_DATAFRAME`` / ``DEV_IMAGES_DIR`` env). Mirrors the hand-written ``evaluation`` template.
        """
        client_cfg = job_dir / self.job.name / "app" / "config" / "config_fed_client.json"
        if not client_cfg.exists():
            return
        config = json.loads(client_cfg.read_text())
        config.setdefault("project_id", self.project_id)
        config.setdefault("query", self.query)
        client_cfg.write_text(json.dumps(config, indent=2))
