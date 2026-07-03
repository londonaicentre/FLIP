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

from nvflare.apis.dxo import DXO
from nvflare.apis.fl_context import FLContext
from nvflare.apis.impl.controller import Task
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal
from nvflare.app_common.abstract.aggregator import Aggregator
from nvflare.app_common.abstract.learnable_persistor import LearnablePersistor
from nvflare.app_common.abstract.model_locator import ModelLocator
from nvflare.app_common.abstract.shareable_generator import ShareableGenerator
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.app_event_type import AppEventType
from nvflare.security.logging import secure_format_exception

from flip.constants import PTConstants
from flip.nvflare.controllers.scatter_and_gather import ScatterAndGather


class ScatterAndGatherLDM(ScatterAndGather):
    """Two-phase (autoencoder → diffusion model) FedAvg controller for latent diffusion.

    Subclasses FLIP's :class:`ScatterAndGather` (itself a thin subclass of stock NVFLARE
    ScatterAndGather). It inherits all the shared FLIP hooks — the partial-safe ``WEIGHT_DIFF``
    reconstruction and hub exception reporting (``_accept_train_result``), the metrics relay
    (``handle_event``), ``FlipEvents.ABORTED`` (``_check_abort_signal``), the per-round allocator
    cleanup (``_maybe_cleanup_memory`` / ``memory_gc_rounds``), and the train-task plumbing — and
    overrides only what is genuinely LDM-specific:

      * :meth:`__init__` — two per-phase round counts (``num_rounds_ae`` / ``num_rounds_dm``) and a
        server-side ``model_locator`` for the phase-1 → phase-2 weight handoff. Delegates the shared
        FedAvg wiring/validation to the base, passing ``num_rounds = ae + dm`` so the inherited
        persist/state code has a valid ``self._num_rounds`` (previously never assigned — a latent
        ``AttributeError`` masked only because ``persist_every_n_rounds=1`` short-circuits the ``or``).
      * :meth:`start_controller` — resolves the ``model_locator`` and reads per-phase round counts
        from the app ``config.json``.
      * :meth:`control_flow` — runs the two phases; for ``train_dm`` it first loads the phase-1
        autoencoder weights via the persistor/model-locator.
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
        aggregator_id=AppConstants.DEFAULT_AGGREGATOR_ID,
        persistor_id=AppConstants.DEFAULT_PERSISTOR_ID,
        shareable_generator_id=AppConstants.DEFAULT_SHAREABLE_GENERATOR_ID,
        train_task_name=AppConstants.TASK_TRAIN,
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
        # code sees a valid self._num_rounds.
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
        self.shareable_gen = None

    def start_controller(self, fl_ctx: FLContext) -> None:
        self.log_info(fl_ctx, "Initializing ScatterAndGather workflow.")
        self._phase = AppConstants.PHASE_INIT
        engine = fl_ctx.get_engine()
        if not engine:
            self.system_panic("Engine not found. ScatterAndGather exiting.", fl_ctx)
            return

        self.aggregator = engine.get_component(self.aggregator_id)
        if not isinstance(self.aggregator, Aggregator):
            self.system_panic(
                f"aggregator {self.aggregator_id} must be an Aggregator type object but got {type(self.aggregator)}",
                fl_ctx,
            )
            return

        self.shareable_gen = engine.get_component(self.shareable_generator_id)
        if not isinstance(self.shareable_gen, ShareableGenerator):
            self.system_panic(
                f"Shareable generator {self.shareable_generator_id} must be a ShareableGenerator \
                type object but got {type(self.shareable_gen)}",
                fl_ctx,
            )
            return

        self.persistor = engine.get_component(self.persistor_id)
        if not isinstance(self.persistor, LearnablePersistor):
            self.system_panic(
                f"Model Persistor {self.persistor_id} must be a LearnablePersistor type \
                object but got {type(self.persistor)}",
                fl_ctx,
            )
            return

        self.model_locator = engine.get_component(self.model_locator_id)
        if not isinstance(self.model_locator, ModelLocator):
            self.system_panic(
                reason="bad model locator {}: expect ModelLocator but got {}".format(
                    self.model_locator_id, type(self.model_locator)
                ),
                fl_ctx=fl_ctx,
            )
            return

        # We find config and override global rounds for both phases:
        app_dir = fl_ctx.get_engine().get_workspace().get_app_dir(fl_ctx.get_job_id())
        if os.path.isfile(os.path.join(app_dir, "custom", "config.json")):
            with open(os.path.join(app_dir, "custom", "config.json"), "r") as f:
                self.config = json.load(f)
            if "GLOBAL_ROUNDS_AE" in self.config.keys():
                self._num_rounds_ae = self.config["GLOBAL_ROUNDS_AE"]
            if "GLOBAL_ROUNDS_DM" in self.config.keys():
                self._num_rounds_dm = self.config["GLOBAL_ROUNDS_DM"]

        # initialize global model
        fl_ctx.set_prop(AppConstants.START_ROUND, self._start_round, private=True, sticky=True)
        fl_ctx.set_prop(AppConstants.NUM_ROUNDS, self._num_rounds_ae + self._num_rounds_dm, private=True, sticky=False)
        self._global_weights = self.persistor.load(fl_ctx)
        fl_ctx.set_prop(AppConstants.GLOBAL_MODEL, self._global_weights, private=True, sticky=True)
        self.fire_event(AppEventType.INITIAL_MODEL_LOADED, fl_ctx)

    def locate_server_models(self, fl_ctx: FLContext) -> bool:
        """Locate server models for the current task.

        Args:
            fl_ctx (FLContext): _description_

        Returns:
            bool: _description_
        """

        self.log_info(fl_ctx, "Locating pre-trained model for task...")
        server_model_names = self.model_locator.get_model_names(fl_ctx)
        for name in server_model_names:
            # Get the model
            dxo = self.model_locator.locate_model(name, fl_ctx)
            if not isinstance(dxo, DXO):
                self.system_panic(
                    f"ModelLocator produced invalid data: expect DXO but got {type(dxo)}.",
                    fl_ctx,
                )
                return None
            else:
                return dxo
        return True

    def control_flow(self, abort_signal: Signal, fl_ctx: FLContext) -> None:
        if self.train_task_name == "train_dm":
            # If task is diffusion model training, we need to load the autoencoder
            # weights first using model_locator.
            model_name = self.model_locator.get_model_names(fl_ctx)[0]
            if model_name == PTConstants.PTServerName:
                try:
                    server_run_dir = fl_ctx.get_engine().get_workspace().get_app_dir(fl_ctx.get_job_id())
                    model_path = os.path.join(server_run_dir, PTConstants.PTFileModelName)
                except Exception:
                    self.log_error(fl_ctx, "Couldn't find the model nor load.")
            self.persistor.source_ckpt_file_full_name = model_path
            self.log_info(fl_ctx, f"Loading LDM phase 1 weights at {model_path}")
            self._global_weights = self.persistor.load_model(fl_ctx)
            self.log_info(fl_ctx, f"Loaded LDM phase 1 weights at {model_path}")

        try:
            self.log_info(fl_ctx, "Beginning ScatterAndGatherLDM training phase.")
            self._phase = AppConstants.PHASE_TRAIN

            fl_ctx.set_prop(AppConstants.PHASE, self._phase, private=True, sticky=False)

            # We set the NUM_ROUNDS AppConstant to AE or DM one depending on phase.
            if self.train_task_name == "train_ae":
                fl_ctx.set_prop(AppConstants.NUM_ROUNDS, self._num_rounds_ae, private=True, sticky=False)
                num_rounds = self._num_rounds_ae
            elif self.train_task_name == "train_dm":
                fl_ctx.set_prop(AppConstants.NUM_ROUNDS, self._num_rounds_dm, private=True, sticky=False)
                num_rounds = self._num_rounds_dm

            self.fire_event(AppEventType.TRAINING_STARTED, fl_ctx)

            for self._current_round in range(self._start_round, self._start_round + num_rounds):
                if self._check_abort_signal(fl_ctx, abort_signal):
                    return

                self.log_info(fl_ctx, f"Global round {self._current_round} started for {self.train_task_name}.")
                fl_ctx.set_prop(AppConstants.GLOBAL_MODEL, self._global_weights, private=True, sticky=True)
                fl_ctx.set_prop(AppConstants.CURRENT_ROUND, self._current_round, private=False, sticky=False)
                self.fire_event(AppEventType.ROUND_STARTED, fl_ctx)

                # Create train_task
                data_shareable: Shareable = self.shareable_gen.learnable_to_shareable(self._global_weights, fl_ctx)
                data_shareable.set_header(AppConstants.CURRENT_ROUND, self._current_round)
                data_shareable.set_header(AppConstants.NUM_ROUNDS, num_rounds)
                data_shareable.add_cookie(AppConstants.CONTRIBUTION_ROUND, self._current_round)

                train_task = Task(
                    name=self.train_task_name,
                    data=data_shareable,
                    props={},
                    timeout=self._train_timeout,
                    before_task_sent_cb=self._prepare_train_task_data,
                    result_received_cb=self._process_train_result,
                )

                self.broadcast_and_wait(
                    task=train_task,
                    min_responses=self._min_clients,
                    wait_time_after_min_received=self._wait_time_after_min_received,
                    fl_ctx=fl_ctx,
                    abort_signal=abort_signal,
                )

                if self._check_abort_signal(fl_ctx, abort_signal):
                    return

                self.fire_event(AppEventType.BEFORE_AGGREGATION, fl_ctx)
                aggr_result = self.aggregator.aggregate(fl_ctx)
                fl_ctx.set_prop(AppConstants.AGGREGATION_RESULT, aggr_result, private=True, sticky=False)
                self.fire_event(AppEventType.AFTER_AGGREGATION, fl_ctx)

                if self._check_abort_signal(fl_ctx, abort_signal):
                    return

                self.fire_event(AppEventType.BEFORE_SHAREABLE_TO_LEARNABLE, fl_ctx)
                self._global_weights = self.shareable_gen.shareable_to_learnable(aggr_result, fl_ctx)
                fl_ctx.set_prop(AppConstants.GLOBAL_MODEL, self._global_weights, private=True, sticky=True)
                fl_ctx.sync_sticky()
                self.fire_event(AppEventType.AFTER_SHAREABLE_TO_LEARNABLE, fl_ctx)

                if self._check_abort_signal(fl_ctx, abort_signal):
                    return

                if (
                    self._persist_every_n_rounds != 0 and (self._current_round + 1) % self._persist_every_n_rounds == 0
                ) or self._current_round == self._start_round + self._num_rounds - 1:
                    self.log_info(fl_ctx, "Start persist model on server.")
                    self.fire_event(AppEventType.BEFORE_LEARNABLE_PERSIST, fl_ctx)
                    self.persistor.save(self._global_weights, fl_ctx)
                    self.fire_event(AppEventType.AFTER_LEARNABLE_PERSIST, fl_ctx)
                    self.log_info(fl_ctx, "End persist model on server.")

                self.fire_event(AppEventType.ROUND_DONE, fl_ctx)
                self.log_info(fl_ctx, f"Round {self._current_round} for {self.train_task_name} finished.")
                self._current_round += 1

                # Return this round's transient allocations to the OS so server RSS does not climb
                # across rounds and OOM a large-model job. Inherited from FLIP's ScatterAndGather
                # (stock NVFLARE's memory_gc_rounds cleanup).
                self._maybe_cleanup_memory()

            self._phase = AppConstants.PHASE_FINISHED
            self.log_info(fl_ctx, f"Finished ScatterAndGatherLDM Training {self.train_task_name}")
        except BaseException as e:
            error_msg = f"Exception in ScatterAndGather control_flow: {secure_format_exception(e)}"
            self.log_exception(fl_ctx, error_msg)
            self.system_panic(error_msg, fl_ctx)

    def stop_controller(self, fl_ctx: FLContext) -> None:
        self._phase = AppConstants.PHASE_FINISHED
        self.cancel_all_tasks()
