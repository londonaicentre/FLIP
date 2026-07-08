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

"""Recipe describing a FLIP FedAvg job that drives client trainers via the NVFLARE Client API.

The recipe is a thin :class:`Recipe` subclass that builds a :class:`FedJob` with the
FLIP-specific server/client components. The same recipe object runs across all NVFLARE
execution environments:

.. code-block:: python

    from flip.nvflare.recipes import FlipFedAvgRecipe
    from nvflare.recipe.sim_env import SimEnv

    recipe = FlipFedAvgRecipe(
        num_rounds=2,
        min_clients=2,
        train_script="trainer.py",
        model_id="<uuid>",      # SimEnv/PocEnv: pinned by you
    )
    recipe.execute(SimEnv(num_clients=2))   # iterate locally
    # ...or recipe.execute(PocEnv(num_clients=2)) — same recipe object
    # ...or recipe.execute(ProdEnv(...)) — same recipe object

Production hand-off to the FLIP-API works through the same recipe via :meth:`export`:

.. code-block:: python

    recipe = FlipFedAvgRecipe(num_rounds=10, min_clients=3, train_script="trainer.py")
    recipe.export("/tmp/xray_job")
    # FLIP-API picks up the job folder, writes meta.json['custom_props']['model_id']
    # with the real UUID, and submits to the NVFlare cluster.

The FLIP components no longer take ``model_id`` at construction — they resolve it lazily
from ``meta.json['custom_props']['model_id']`` via :func:`flip.nvflare.runtime.get_flip_model_id`
on the first event/task that needs it. The recipe writes that key into ``meta.json``
automatically so SimEnv/PocEnv runs work out of the box.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nvflare import FedJob
from nvflare.apis.dxo import DataKind
from nvflare.app_common.aggregators import InTimeAccumulateWeightedAggregator
from nvflare.app_common.executors.in_process_client_api_executor import InProcessClientAPIExecutor
from nvflare.app_common.shareablegenerators.full_model_shareable_generator import FullModelShareableGenerator
from nvflare.app_common.workflows.cross_site_model_eval import CrossSiteModelEval
from nvflare.app_common.workflows.global_model_eval import GlobalModelEval
from nvflare.job_config.defs import FilterType
from nvflare.recipe.spec import Recipe

from flip.constants import FlipTasks
from flip.nvflare.components import (
    CleanupImages,
    ClientEventHandler,
    ClientExceptionReporter,
    FlipAnalyticsBridge,
    InitialCheckpointPTModelPersistor,
    KeepOnlyVars,
    PersistToS3AndCleanup,
    PTModelLocator,
    ReconstructFullModel,
    ServerEventHandler,
    TrimBroadcastVars,
    ValidationJsonGenerator,
)
from flip.nvflare.components import (
    PercentilePrivacy as PercentilePrivacyFilter,
)
from flip.nvflare.controllers import BroadcastTask, InitTraining, ScatterAndGather
from flip.nvflare.runtime import FLIP_CUSTOM_PROPS_KEY, FLIP_MODEL_ID_KEY

# Default UUID used by SimEnv/PocEnv runs when the caller doesn't pass one. Pinned so dev runs
# are reproducible. Production runs override this via meta.json['custom_props']['model_id'].
_DEV_MODEL_ID = "00000000-0000-0000-0000-000000000001"


@dataclass
class PercentilePrivacy:
    """Percentile-based DP noise filter configuration.

    Stock NVFLARE semantics (Shokri & Shmatikov "largest percentile to share"): components of the
    per-step weight diff with magnitude BELOW the ``percentile``-th percentile are zeroed (only the
    top ``100 - percentile`` % are shared), and the survivors are truncated to ``±gamma`` (absolute).
    Defaults match stock NVFLARE (share top 90%, clip at 0.01). Do NOT raise ``percentile`` towards
    95+ — that discards ~all of the update and stalls FedAvg convergence; with a frozen-backbone
    head-only update it silently resets the global head every round.
    """

    gamma: float = 0.01
    percentile: int = 10
    off: bool = False


class FlipFedAvgRecipe(Recipe):
    """FLIP FedAvg recipe wired for the NVFLARE Client API.

    Args:
        num_rounds: Number of federated rounds.
        min_clients: Minimum number of clients required per round.
        train_script: Trainer script path. ``"trainer.py"`` is taken to mean
            ``"custom/trainer.py"`` inside the job; pass an explicit ``custom/`` prefix
            to override that convention.
        train_args: Argument string for the trainer. NVFlare's TaskScriptRunner whitespace-splits
            this — any FLIP-API placeholder used here must substitute into a single token.
            The SQL query is plumbed via the client config's top-level ``query`` key instead.
        model_id: Real UUID for SimEnv/PocEnv runs. Written into ``meta.json['custom_props']``
            so FLIP components can resolve it lazily at first task. Production runs override
            this via the FLIP-API which writes the real model id into the same key.
        percentile_privacy: Configuration for the percentile-based DP noise filter.
        heart_beat_timeout, validation_timeout, wait_time_after_min_received: NVFlare timing knobs.
        project_id, query: Top-level keys on the client config (consumed by the FLIP-API
            placeholder substitution and read by the trainer at runtime).
        local_rounds: Top-level local_rounds key on the client config.
        train_task_name, submit_model_task_name, evaluate_task_name: NVFlare task names. Client-model
            submission is disabled by default so post-training evaluation covers only the aggregated
            global model. Pass ``"submit_model"`` to opt into the full all-to-all matrix.
        params_exchange_format, params_transfer_type: NVFlare param-exchange knobs.
        aggregate_only_regex: when set, wire the frozen-backbone head-only filters — KeepOnlyVars
            (client result filter, ordered BEFORE PercentilePrivacy), ReconstructFullModel (client data
            filter) and TrimBroadcastVars (server data filter) — so only params matching the regex are
            aggregated per round and, after round 0, broadcast. Empty (default) wires none. Mirrors the
            fl-server's deploy-time injection from config.json's ``AGGREGATE_ONLY_REGEX``, which is the
            canonical path for production jobs (the shipped ``standard_client_api`` template bakes no
            head-only filters): the fl-server folds any recipe-baked chains into its own — a single
            ``["train", "validate"]`` ``ReconstructFullModelForEval`` chain that also extends the
            head-only broadcast to cross-site validation, superseding the recipe's train-only
            ``ReconstructFullModel`` (FLIP#730/#733). This arg stands alone only where that deploy step
            doesn't run (SimEnv/PocEnv), so the ``validate`` broadcast stays full-model there.
    """

    def __init__(
        self,
        *,
        num_rounds: int = 3,
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
        local_rounds: int = 1,
        train_task_name: str = "train",
        submit_model_task_name: str = "",
        evaluate_task_name: str = "validate",
        params_exchange_format: str = "numpy",
        params_transfer_type: str = "FULL",
        aggregate_only_regex: str = "",
    ):
        self.num_rounds = num_rounds
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
        self.local_rounds = local_rounds
        self.train_task_name = train_task_name
        self.submit_model_task_name = submit_model_task_name
        self.evaluate_task_name = evaluate_task_name
        self.params_exchange_format = params_exchange_format
        self.params_transfer_type = params_transfer_type
        self.aggregate_only_regex = aggregate_only_regex

        super().__init__(self._build_fed_job())

    def _build_fed_job(self) -> FedJob:
        """Construct the FedJob NVFLARE's Recipe uses for ``execute(env)``.

        ``meta_props`` carries the FLIP runtime config (currently just ``model_id``) into the
        exported ``meta.json`` so the lazily-resolving components can find it.
        """
        job = FedJob(
            name="flip_fedavg",
            min_clients=self.min_clients,
            meta_props={FLIP_CUSTOM_PROPS_KEY: {FLIP_MODEL_ID_KEY: self.model_id}},
        )

        # Server: persistence and aggregation primitives. InitialCheckpointPTModelPersistor seeds the
        # round-0 global model from a server-side SERVER_CHECKPOINT (frozen-backbone finetuning); with no
        # SERVER_CHECKPOINT declared it is a drop-in for the stock PTFileModelPersistor. model_id is
        # resolved lazily from meta.json custom_props at runtime (like the other FLIP components), so it
        # is not passed as a component arg.
        persistor_id = job.to_server(
            InitialCheckpointPTModelPersistor(model={"path": "models.get_model"}),
            id="persistor",
        )
        shareable_generator_id = job.to_server(FullModelShareableGenerator(), id="shareable_generator")
        aggregator_id = job.to_server(
            InTimeAccumulateWeightedAggregator(expected_data_kind=DataKind.WEIGHTS), id="aggregator"
        )

        # Server: FLIP components (locator, JSON generator, event handler, S3 persistor).
        job.to_server(PTModelLocator(model={"path": "models.get_model"}), id="model_locator")
        job.to_server(ValidationJsonGenerator(), id="json_generator")
        job.to_server(ServerEventHandler(), id="flip_server_event_handler")
        job.to_server(PersistToS3AndCleanup(persistor_id=persistor_id), id="persist_and_cleanup")

        # Server workflows: init → train → model evaluation → post-validation cleanup.
        job.to_server(InitTraining(min_clients=self.min_clients))
        job.to_server(
            ScatterAndGather(
                min_clients=self.min_clients,
                num_rounds=self.num_rounds,
                start_round=0,
                wait_time_after_min_received=self.wait_time_after_min_received,
                aggregator_id=aggregator_id,
                persistor_id=persistor_id,
                shareable_generator_id=shareable_generator_id,
                train_task_name=self.train_task_name,
                train_timeout=0,
                ignore_result_error=False,
            )
        )
        if self.submit_model_task_name:
            evaluation_controller = CrossSiteModelEval(
                model_locator_id="model_locator",
                submit_model_task_name=self.submit_model_task_name,
                validation_timeout=self.validation_timeout,
            )
        else:
            evaluation_controller = GlobalModelEval(
                model_locator_id="model_locator",
                validation_timeout=self.validation_timeout,
            )
        job.to_server(evaluation_controller)
        evaluation_result_tasks = [self.evaluate_task_name, FlipTasks.POST_VALIDATION.value]
        if self.submit_model_task_name:
            evaluation_result_tasks.insert(0, self.submit_model_task_name)
        job.to_server(
            ClientExceptionReporter(),
            filter_type=FilterType.TASK_RESULT,
            tasks=evaluation_result_tasks,
        )
        job.to_server(BroadcastTask(task_name=FlipTasks.POST_VALIDATION.value))

        # Server: head-only broadcast trim (frozen-backbone finetuning). After round 0 the server
        # broadcasts only the vars matching the regex; clients rebuild the full model via
        # ReconstructFullModel. No-op unless aggregate_only_regex is set.
        if self.aggregate_only_regex:
            job.to_server(
                TrimBroadcastVars(include_vars=self.aggregate_only_regex),
                tasks=[self.train_task_name],
                filter_type=FilterType.TASK_DATA,
                id="trim_broadcast_to_trainable",
            )

        # Clients: cleanup executor for init/post tasks.
        job.to_clients(CleanupImages(), tasks=["init_training", "post_validation"])

        # Clients: Client API trainer for train / optional submit_model / validate.
        executor_tasks = [self.train_task_name, self.evaluate_task_name]
        if self.submit_model_task_name:
            executor_tasks.insert(1, self.submit_model_task_name)
        job.to_clients(
            InProcessClientAPIExecutor(
                task_script_path=self.train_script,
                task_script_args=self.train_args,
                train_task_name=self.train_task_name,
                submit_model_task_name=self.submit_model_task_name,
                evaluate_task_name=self.evaluate_task_name,
                params_exchange_format=self.params_exchange_format,
                params_transfer_type=self.params_transfer_type,
            ),
            tasks=executor_tasks,
        )

        # Clients: head-only round-trip (frozen-backbone finetuning). KeepOnlyVars MUST be wired before
        # PercentilePrivacy so the percentile cutoff sees only the trainable head, not the frozen
        # backbone's ~0 diffs; ReconstructFullModel rebuilds the full model from the trimmed broadcast.
        # No-op unless aggregate_only_regex is set.
        if self.aggregate_only_regex:
            job.to_clients(
                KeepOnlyVars(include_vars=self.aggregate_only_regex),
                filter_type=FilterType.TASK_RESULT,
                tasks=[self.train_task_name],
                id="keep_only_trainable_vars",
            )
            job.to_clients(
                ReconstructFullModel(),
                filter_type=FilterType.TASK_DATA,
                tasks=[self.train_task_name],
                id="reconstruct_full_model",
            )

        # Clients: percentile-privacy DP noise on training results.
        job.to_clients(
            PercentilePrivacyFilter(
                gamma=self.percentile_privacy.gamma,
                percentile=self.percentile_privacy.percentile,
                off=self.percentile_privacy.off,
            ),
            filter_type=FilterType.TASK_RESULT,
            tasks=[self.train_task_name],
            id="percentile_privacy",
        )

        # Clients: event handlers — client event handler + analytics bridge.
        job.to_clients(ClientEventHandler(), id="flip_client_event_handler")
        job.to_clients(FlipAnalyticsBridge(), id="flip_analytics_bridge")

        return job

    def export(self, job_dir: str | Path, **_kwargs: Any) -> None:
        """Export the job using NVFLARE's standard FedJob layout.

        Produces ``<job_dir>/<job_name>/{meta.json, app/config/, app/custom/}`` —
        the same structure NVFLARE's admin client uploads to the cluster. The FLIP-API
        consumes this directory, optionally rewrites ``meta.json['custom_props']`` with
        the real model_id at submit time, and forwards the job to the fl-server stack.
        """
        self.job.export_job(str(job_dir))
        self._write_client_config_params(Path(job_dir))

    def _write_client_config_params(self, job_dir: Path) -> None:
        """Emit ``project_id`` / ``query`` / ``local_rounds`` as top-level keys on the exported
        ``config_fed_client.json``.

        These are not NVFLARE components, so they don't fall out of ``export_job`` — but the
        client contract expects them at the config top level: the trainer's
        ``--project_id {project_id}`` arg resolves the ``{project_id}`` reference against the
        top-level ``project_id`` key, and ``trainer.load_query()`` reads the top-level ``query``.
        We write the recipe's (placeholder) defaults so the exported template is self-documenting
        and the ``{project_id}`` reference always resolves. In production the fl-server's
        ``configure_client`` overwrites ``project_id`` / ``query`` with the real submission
        values at job-assembly; in SimEnv/LOCAL_DEV they're ignored (data comes from the
        ``DEV_DATAFRAME`` / ``DEV_IMAGES_DIR`` env). Mirrors the hand-written ``standard`` template.
        """
        client_cfg = job_dir / self.job.name / "app" / "config" / "config_fed_client.json"
        if not client_cfg.exists():
            return
        config = json.loads(client_cfg.read_text())
        config.setdefault("project_id", self.project_id)
        config.setdefault("query", self.query)
        config.setdefault("local_rounds", self.local_rounds)
        client_cfg.write_text(json.dumps(config, indent=2))
