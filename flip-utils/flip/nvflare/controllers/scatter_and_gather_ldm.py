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
#

import json
import os

from nvflare.apis.fl_context import FLContext
from nvflare.apis.signal import Signal
from nvflare.app_common.abstract.model_locator import ModelLocator
from nvflare.app_common.app_constant import AppConstants
from nvflare.security.logging import secure_format_exception

from flip.constants import PTConstants
from flip.nvflare.controllers.scatter_and_gather import ScatterAndGather


class ScatterAndGatherLDM(ScatterAndGather):
    """Two-phase (autoencoder → diffusion model) FedAvg controller for latent diffusion.

    Subclasses FLIP's :class:`ScatterAndGather` (itself a thin subclass of stock NVFLARE
    ScatterAndGather). Each instance runs a *single* phase — the job wires one controller per phase
    (``train_task_name`` ``train_ae`` then ``train_dm``) and NVFLARE runs them sequentially — so the
    round loop itself is stock's, inherited verbatim (with its per-round ``aggregator.reset``,
    failed-client tracking, ``memory_gc_rounds`` allocator cleanup, and any future upstream fixes),
    alongside all the shared FLIP hooks (``_accept_train_result``, the metrics relay,
    ``FlipEvents.ABORTED``). Only what is genuinely LDM-specific is overridden:

      * :meth:`__init__` — two per-phase round counts (``num_rounds_ae`` / ``num_rounds_dm``) and a
        server-side ``model_locator`` for the phase-1 → phase-2 weight handoff. Delegates the shared
        FedAvg wiring/validation to the base, passing ``num_rounds = ae + dm`` so the inherited
        persist/state code sees a valid total before a phase starts.
      * :meth:`start_controller` — resolves the ``model_locator`` and applies the app
        ``config.json`` per-phase round-count overrides, then delegates component validation and
        initial-model loading to stock.
      * :meth:`control_flow` — points ``self._num_rounds`` at this phase's round count (stock's
        loop, ``NUM_ROUNDS`` prop/header and last-round persist all key off it); for ``train_dm``
        first loads the phase-1 autoencoder weights via the persistor/model-locator; then delegates
        the round loop to stock.
      * :meth:`stop_controller` — cancels outstanding tasks (stock only flips the phase).
    """

    def __init__(
        self,
        model_id: str = "",
        min_clients: int = 1,
        num_rounds_ae: int = 5,
        num_rounds_dm: int = 5,
        start_round: int = 0,
        model_locator_id: str = "",
        wait_time_after_min_received: int = 10,
        aggregator_id: str = AppConstants.DEFAULT_AGGREGATOR_ID,
        persistor_id: str = AppConstants.DEFAULT_PERSISTOR_ID,
        shareable_generator_id: str = AppConstants.DEFAULT_SHAREABLE_GENERATOR_ID,
        train_task_name: str = AppConstants.TASK_TRAIN,
        train_timeout: int = 0,
        ignore_result_error: bool = True,
        fatal_error_delay: int = 5,
        task_check_period: float = 0.5,
        persist_every_n_rounds: int = 1,
        memory_gc_rounds: int = 1,
    ) -> None:
        if not isinstance(model_locator_id, str):
            raise TypeError(f"model_locator_id must be a string but got {type(model_locator_id)}")
        # The base validates the shared args (incl. num_rounds = ae + dm); validate the per-phase
        # counts here since the base does not know about them.
        for _name, _value in (("num_rounds_ae", num_rounds_ae), ("num_rounds_dm", num_rounds_dm)):
            if not isinstance(_value, int) or _value < 0:
                raise ValueError(f"{_name} must be greater than or equal to 0 but got {_value!r}")
        # Delegate the shared FedAvg wiring/validation (and memory_gc_rounds / snapshot handling) to
        # FLIP's ScatterAndGather. num_rounds is the two-phase total so the inherited persist/state
        # code sees a valid self._num_rounds; control_flow narrows it to the per-phase count.
        super().__init__(
            model_id=model_id,
            min_clients=min_clients,
            num_rounds=num_rounds_ae + num_rounds_dm,
            start_round=start_round,
            wait_time_after_min_received=wait_time_after_min_received,
            aggregator_id=aggregator_id,
            persistor_id=persistor_id,
            shareable_generator_id=shareable_generator_id,
            train_task_name=train_task_name,
            train_timeout=train_timeout,
            ignore_result_error=ignore_result_error,
            task_check_period=task_check_period,
            persist_every_n_rounds=persist_every_n_rounds,
            memory_gc_rounds=memory_gc_rounds,
        )
        # LDM-specific state.
        self._num_rounds_ae = num_rounds_ae
        self._num_rounds_dm = num_rounds_dm
        self._fatal_error_delay = fatal_error_delay
        self.model_locator_id = model_locator_id
        self.model_locator = None
        # FedJob/recipe serialisation omits args equal to their declared defaults, but the
        # fl-server's deploy-time configure_server can only override workflow args that EXIST in
        # the exported config — min_clients must always be emitted or a deployed job silently runs
        # min_clients=1 and closes every round on the fastest trust's update. The per-phase round
        # counts are emitted too so the exported template documents them (runtime re-reads
        # GLOBAL_ROUNDS_AE/GLOBAL_ROUNDS_DM from config.json regardless).
        self._always_serialize_args = {"min_clients", "num_rounds_ae", "num_rounds_dm"}

    def start_controller(self, fl_ctx: FLContext) -> None:
        engine = fl_ctx.get_engine()
        if not engine:
            self.system_panic("Engine not found. ScatterAndGatherLDM exiting.", fl_ctx)
            return

        # LDM-specific: the phase-1 → phase-2 weight handoff needs a server-side model locator.
        self.model_locator = engine.get_component(self.model_locator_id)
        if not isinstance(self.model_locator, ModelLocator):
            self.system_panic(
                f"bad model locator {self.model_locator_id}: expect ModelLocator but got {type(self.model_locator)}",
                fl_ctx,
            )
            return

        # LDM-specific: the app config.json may override the per-phase round counts.
        app_dir = engine.get_workspace().get_app_dir(fl_ctx.get_job_id())
        config_path = os.path.join(app_dir, "custom", "config.json")
        if os.path.isfile(config_path):
            with open(config_path, "r") as f:
                self.config = json.load(f)
            self._num_rounds_ae = self.config.get("GLOBAL_ROUNDS_AE", self._num_rounds_ae)
            self._num_rounds_dm = self.config.get("GLOBAL_ROUNDS_DM", self._num_rounds_dm)
        # Keep the inherited two-phase total in sync with any override so persist/state code is
        # coherent before control_flow narrows it to this phase's count.
        self._num_rounds = self._num_rounds_ae + self._num_rounds_dm

        # Stock validates aggregator/shareable_gen/persistor, loads and type-checks the initial
        # global model, sets START_ROUND/NUM_ROUNDS and fires INITIAL_MODEL_LOADED.
        super().start_controller(fl_ctx)

    def control_flow(self, abort_signal: Signal, fl_ctx: FLContext) -> None:
        # Each instance runs exactly one phase: stock's loop, its NUM_ROUNDS prop/header and the
        # last-round persist fallback all key off self._num_rounds, so point it at this phase's
        # round count before delegating.
        if self.train_task_name == "train_ae":
            self._num_rounds = self._num_rounds_ae
        elif self.train_task_name == "train_dm":
            self._num_rounds = self._num_rounds_dm
        else:
            self.system_panic(
                f"ScatterAndGatherLDM expects train_task_name 'train_ae' or 'train_dm' "
                f"but got {self.train_task_name!r}",
                fl_ctx,
            )
            return

        # Phase-1 → phase-2 handoff: seed the DM phase with the autoencoder weights the phase-1
        # controller persisted into the server run dir. Skipped on a snapshot-restore resume
        # (_current_round is not None) so restored global weights are not overwritten.
        if self.train_task_name == "train_dm" and self._current_round is None:
            try:
                model_name = self.model_locator.get_model_names(fl_ctx)[0]
                if model_name != PTConstants.PTServerName:
                    raise RuntimeError(f"unexpected server model name {model_name!r}")
                server_run_dir = fl_ctx.get_engine().get_workspace().get_app_dir(fl_ctx.get_job_id())
                model_path = os.path.join(server_run_dir, PTConstants.PTFileModelName)
                self.persistor.source_ckpt_file_full_name = model_path
                self.log_info(fl_ctx, f"Loading LDM phase 1 weights at {model_path}")
                self._global_weights = self.persistor.load_model(fl_ctx)
            except Exception as e:
                self.system_panic(
                    f"Failed to load LDM phase-1 autoencoder weights: {secure_format_exception(e)}", fl_ctx
                )
                return

        super().control_flow(abort_signal, fl_ctx)

    def stop_controller(self, fl_ctx: FLContext) -> None:
        self._phase = AppConstants.PHASE_FINISHED
        self.cancel_all_tasks()
