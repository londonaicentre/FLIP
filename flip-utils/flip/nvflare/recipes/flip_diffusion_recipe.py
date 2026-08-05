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

"""Recipe describing a FLIP latent-diffusion job that drives clients via the NVFLARE Client API.

This is the Client-API counterpart of the legacy ``diffusion_model`` job type. The job is
**multi-phase**: an autoencoder (+ GAN discriminator) FedAvg phase followed by a diffusion-model
FedAvg phase over the frozen autoencoder's latent space, each phase followed by its own cross-site
validation and a post-validation cleanup broadcast. The server workflow chain mirrors the legacy
template exactly:

``InitTraining`` → ``ScatterAndGatherLDM(train_ae)`` → ``GlobalModelEval(validate_ae)`` →
``BroadcastTask(post_validation)`` → ``ScatterAndGatherLDM(train_dm)`` →
``GlobalModelEval(validate_dm)`` → ``BroadcastTask(post_validation)``

The two :class:`~flip.nvflare.controllers.ScatterAndGatherLDM` instances share one persistor: the
``train_dm`` controller seeds its phase with the autoencoder weights the ``train_ae`` controller
persisted (the phase-1 → phase-2 handoff, unchanged from the legacy job).

Client-side, a **single** stock ``InProcessClientAPIExecutor`` serves all four ML task names
(``train_ae`` / ``train_dm`` / ``validate_ae`` / ``validate_dm``): the executor forwards the actual
task name to the script, which dispatches on ``flare.get_task_name()`` (the single-name
``flare.is_train()`` / ``flare.is_evaluate()`` predicates cannot distinguish the two train phases).
The ``StagePercentilePrivacy`` DP filter stays wired on both train tasks' results, as in the legacy
client config; the script activates its per-stage filtering by stamping ``FlipMetaKey.STAGE`` on the
outgoing ``FLModel`` meta.

The recipe runs identically across SimEnv/PocEnv/ProdEnv and exports the standard NVFLARE FedJob
layout for the FLIP-API, exactly like :class:`FlipFedAvgRecipe`. ``model_id`` is resolved lazily by
the FLIP components from ``meta.json['custom_props']['model_id']`` (written here for SimEnv/PocEnv;
overwritten by the FLIP-API in production). The per-phase round counts are likewise soft: the
``ScatterAndGatherLDM`` controllers re-read ``GLOBAL_ROUNDS_AE`` / ``GLOBAL_ROUNDS_DM`` from the app
``config.json`` at start, so the values baked here are SimEnv/PocEnv defaults only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nvflare import FedJob
from nvflare.apis.dxo import DataKind
from nvflare.app_common.aggregators import InTimeAccumulateWeightedAggregator
from nvflare.app_common.executors.in_process_client_api_executor import InProcessClientAPIExecutor
from nvflare.app_common.shareablegenerators.full_model_shareable_generator import FullModelShareableGenerator
from nvflare.app_common.workflows.global_model_eval import GlobalModelEval
from nvflare.app_opt.pt.file_model_persistor import PTFileModelPersistor
from nvflare.job_config.defs import FilterType
from nvflare.recipe.spec import Recipe

from flip.constants import FlipTasks
from flip.nvflare.components import (
    CleanupImages,
    ClientEventHandler,
    ClientExceptionReporter,
    FlipAnalyticsBridge,
    InitialPTModelLocator,
    PersistToS3AndCleanup,
    PTModelLocator,
    ServerEventHandler,
    StagePercentilePrivacy,
    ValidationJsonGenerator,
)
from flip.nvflare.controllers import BroadcastTask, InitTraining, ScatterAndGatherLDM
from flip.nvflare.recipes.flip_fedavg_recipe import PercentilePrivacy
from flip.nvflare.runtime import FLIP_CUSTOM_PROPS_KEY, FLIP_MODEL_ID_KEY

# Default UUID used by SimEnv/PocEnv runs when the caller doesn't pass one. Pinned so dev runs are
# reproducible. Production runs override this via meta.json['custom_props']['model_id'].
_DEV_MODEL_ID = "00000000-0000-0000-0000-000000000001"

TRAIN_AE_TASK = "train_ae"
TRAIN_DM_TASK = "train_dm"
VALIDATE_AE_TASK = "validate_ae"
VALIDATE_DM_TASK = "validate_dm"


class FlipDiffusionRecipe(Recipe):
    """FLIP latent-diffusion recipe wired for the NVFLARE Client API.

    Args:
        num_rounds_ae: Federated rounds for the autoencoder phase (SimEnv/PocEnv default — in a
            deployed job ``ScatterAndGatherLDM`` re-reads ``GLOBAL_ROUNDS_AE`` from ``config.json``).
        num_rounds_dm: Federated rounds for the diffusion-model phase (deployed jobs re-read
            ``GLOBAL_ROUNDS_DM`` from ``config.json``).
        min_clients: Minimum number of clients required per round.
        train_script: Client script path serving all four ML tasks. ``"trainer.py"`` is taken to
            mean ``"custom/trainer.py"`` inside the job; pass an explicit ``custom/`` prefix to
            override that convention.
        train_args: Argument string for the script. NVFlare's TaskScriptRunner whitespace-splits
            this — any FLIP-API placeholder used here must substitute into a single token. The SQL
            query is plumbed via the client config's top-level ``query`` key instead.
        model_id: Real UUID for SimEnv/PocEnv runs. Written into ``meta.json['custom_props']`` so
            FLIP components can resolve it lazily at first event/task. Production runs override
            this via the FLIP-API which writes the real model id into the same key.
        percentile_privacy: Configuration for the stage-aware percentile DP filter applied to both
            train tasks' results (:class:`~flip.nvflare.components.StagePercentilePrivacy`).
        heart_beat_timeout, validation_timeout, wait_time_after_min_received: NVFlare timing knobs.
        project_id, query: Top-level keys on the client config (consumed by the FLIP-API
            placeholder substitution and read by the script at runtime).
        params_exchange_format, params_transfer_type: NVFlare param-exchange knobs for the
            Client API.
    """

    def __init__(
        self,
        *,
        num_rounds_ae: int = 1,
        num_rounds_dm: int = 1,
        min_clients: int = 1,
        train_script: str = "trainer.py",
        train_args: str = "--project_id {project_id}",
        model_id: str = _DEV_MODEL_ID,
        percentile_privacy: PercentilePrivacy | None = None,
        heart_beat_timeout: int = 600,
        validation_timeout: int = 12000,
        wait_time_after_min_received: int = 10,
        project_id: str = "",
        query: str = "SELECT * FROM Table;",
        params_exchange_format: str = "numpy",
        params_transfer_type: str = "FULL",
    ):
        self.num_rounds_ae = num_rounds_ae
        self.num_rounds_dm = num_rounds_dm
        self.min_clients = min_clients
        self.train_script = train_script if train_script.startswith("custom/") else f"custom/{train_script}"
        self.train_args = train_args
        self.model_id = model_id
        self.percentile_privacy = percentile_privacy or PercentilePrivacy()
        self.heart_beat_timeout = heart_beat_timeout
        self.validation_timeout = validation_timeout
        self.wait_time_after_min_received = wait_time_after_min_received
        self.project_id = project_id
        self.query = query
        self.params_exchange_format = params_exchange_format
        self.params_transfer_type = params_transfer_type

        super().__init__(self._build_fed_job())

    def _add_train_phase(self, job: FedJob, train_task_name: str, model_locator_id: str) -> None:
        """Wire one training phase: SAG-LDM round loop, cross-site validation, cleanup broadcast.

        Both phases share the aggregator/persistor/shareable-generator wired in
        :meth:`_build_fed_job`; ``model_locator_id`` is the phase-1 → phase-2 handoff hook —
        ``train_dm`` passes the ``PTModelLocator`` it uses to seed itself with the persisted
        autoencoder weights (``train_ae`` receives the initial-model locator, which its controller
        never reads).
        """
        validate_task_name = VALIDATE_AE_TASK if train_task_name == TRAIN_AE_TASK else VALIDATE_DM_TASK
        job.to_server(
            ScatterAndGatherLDM(
                min_clients=self.min_clients,
                num_rounds_ae=self.num_rounds_ae,
                num_rounds_dm=self.num_rounds_dm,
                start_round=0,
                wait_time_after_min_received=self.wait_time_after_min_received,
                aggregator_id="aggregator",
                persistor_id="persistor",
                model_locator_id=model_locator_id,
                shareable_generator_id="shareable_generator",
                train_task_name=train_task_name,
                train_timeout=0,
                ignore_result_error=False,
            )
        )
        job.to_server(
            GlobalModelEval(
                model_locator_id="model_locator",
                validation_task_name=validate_task_name,
                validation_timeout=self.validation_timeout,
            )
        )
        job.to_server(BroadcastTask(task_name=FlipTasks.POST_VALIDATION.value))

    def _build_fed_job(self) -> FedJob:
        """Construct the FedJob NVFLARE's Recipe uses for ``execute(env)``.

        ``meta_props`` carries the FLIP runtime config (currently just ``model_id``) into the
        exported ``meta.json`` so the lazily-resolving components can find it.
        """
        job = FedJob(
            name="flip_diffusion",
            min_clients=self.min_clients,
            meta_props={FLIP_CUSTOM_PROPS_KEY: {FLIP_MODEL_ID_KEY: self.model_id}},
        )

        # Server: persistence and aggregation primitives, shared by both phases (the shared
        # persistor is what carries the trained autoencoder from phase 1 into phase 2).
        job.to_server(PTFileModelPersistor(model={"path": "models.get_model"}), id="persistor")
        job.to_server(FullModelShareableGenerator(), id="shareable_generator")
        job.to_server(InTimeAccumulateWeightedAggregator(expected_data_kind=DataKind.WEIGHTS), id="aggregator")

        # Server: FLIP components (locators, JSON generator, event handler, S3 persistor). The
        # initial-model locator mirrors the legacy template's ``model_locator_initial`` wiring for
        # the autoencoder phase; the run-dir locator serves cross-site validation and the
        # phase-1 → phase-2 weight handoff.
        job.to_server(PTModelLocator(model={"path": "models.get_model"}), id="model_locator")
        job.to_server(InitialPTModelLocator(model={"path": "models.get_model"}), id="model_locator_initial")
        job.to_server(ValidationJsonGenerator(), id="json_generator")
        job.to_server(ServerEventHandler(), id="flip_server_event_handler")
        job.to_server(PersistToS3AndCleanup(persistor_id="persistor"), id="persist_and_cleanup")

        # Server workflows: init, then the two training phases in sequence.
        job.to_server(InitTraining(min_clients=self.min_clients))
        self._add_train_phase(job, TRAIN_AE_TASK, model_locator_id="model_locator_initial")
        self._add_train_phase(job, TRAIN_DM_TASK, model_locator_id="model_locator")

        job.to_server(
            ClientExceptionReporter(),
            filter_type=FilterType.TASK_RESULT,
            tasks=[VALIDATE_AE_TASK, VALIDATE_DM_TASK, FlipTasks.POST_VALIDATION.value],
        )

        # Clients: cleanup executor for the init/post-validation tasks.
        job.to_clients(CleanupImages(), tasks=["init_training", FlipTasks.POST_VALIDATION.value])

        # Clients: ONE Client API executor serving all four ML tasks. The executor forwards the
        # actual task name to the script, which dispatches on ``flare.get_task_name()`` — the
        # executor's own train/evaluate task-name knobs are single-valued, so they are left at
        # their stock defaults and intentionally unused.
        job.to_clients(
            InProcessClientAPIExecutor(
                task_script_path=self.train_script,
                task_script_args=self.train_args,
                params_exchange_format=self.params_exchange_format,
                params_transfer_type=self.params_transfer_type,
            ),
            tasks=[TRAIN_AE_TASK, TRAIN_DM_TASK, VALIDATE_AE_TASK, VALIDATE_DM_TASK],
        )

        # Clients: stage-aware percentile-privacy DP noise on both phases' training results.
        job.to_clients(
            StagePercentilePrivacy(
                gamma=self.percentile_privacy.gamma,
                percentile=self.percentile_privacy.percentile,
                off=self.percentile_privacy.off,
            ),
            filter_type=FilterType.TASK_RESULT,
            tasks=[TRAIN_AE_TASK, TRAIN_DM_TASK],
            id="percentile_privacy",
        )

        # Clients: event handlers — client event handler + analytics bridge (SummaryWriter relay).
        job.to_clients(ClientEventHandler(), id="flip_client_event_handler")
        job.to_clients(FlipAnalyticsBridge(), id="flip_analytics_bridge")

        return job

    def export(self, job_dir: str | Path, **_kwargs: Any) -> None:
        """Export the job using NVFLARE's standard FedJob layout.

        Produces ``<job_dir>/<job_name>/{meta.json, app/config/, app/custom/}`` — the same structure
        NVFLARE's admin client uploads to the cluster. The FLIP-API consumes this directory,
        optionally rewrites ``meta.json['custom_props']`` with the real model_id at submit time, and
        forwards the job to the fl-server stack.
        """
        self.job.export_job(str(job_dir))
        self._write_client_config_params(Path(job_dir))

    def _write_client_config_params(self, job_dir: Path) -> None:
        """Emit ``project_id`` / ``query`` as top-level keys on the exported ``config_fed_client.json``.

        These are not NVFLARE components, so they don't fall out of ``export_job`` — but the client
        contract expects them at the config top level: the script's ``--project_id {project_id}``
        arg resolves the ``{project_id}`` reference against the top-level ``project_id`` key, and
        ``trainer.load_query()`` reads the top-level ``query``. We write the recipe's (placeholder)
        defaults so the exported template is self-documenting and the ``{project_id}`` reference
        always resolves. In production the fl-server's ``configure_client`` overwrites
        ``project_id`` / ``query`` with the real submission values; in SimEnv/LOCAL_DEV they're
        ignored (data comes from the ``DEV_DATAFRAME`` / ``DEV_IMAGES_DIR`` env). Mirrors the
        hand-written ``diffusion_model`` template.
        """
        client_cfg = job_dir / self.job.name / "app" / "config" / "config_fed_client.json"
        if not client_cfg.exists():
            return
        config = json.loads(client_cfg.read_text())
        config.setdefault("project_id", self.project_id)
        config.setdefault("query", self.query)
        # Trailing newline: keeps the committed diffusion_model_client_api template stable across
        # regenerations and satisfies the end-of-file-fixer pre-commit hook.
        client_cfg.write_text(json.dumps(config, indent=2) + "\n")
