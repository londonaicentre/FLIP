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

from nvflare.apis.client import Client
from nvflare.apis.controller_spec import ClientTask
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.event_type import EventType
from nvflare.apis.fl_constant import FLContextKey, ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.impl.controller import Controller, Task
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal
from nvflare.app_common.abstract.aggregator import Aggregator
from nvflare.app_common.abstract.learnable import Learnable
from nvflare.app_common.abstract.learnable_persistor import LearnablePersistor
from nvflare.app_common.abstract.shareable_generator import ShareableGenerator
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.app_event_type import AppEventType
from nvflare.security.logging import secure_format_exception
from nvflare.widgets.info_collector import GroupInfoCollector, InfoCollector

from flip import FLIP
from flip.constants import FlipEvents, ModelStatus
from flip.utils import Utils


def _check_non_neg_int(data, name: str):
    if not isinstance(data, int):
        raise ValueError(f"{name} must be int but got {type(data)}")

    if data < 0:
        raise ValueError(f"{name} must be greater than or equal to 0.")


class ScatterAndGather(Controller):
    def __init__(
        self,
        model_id: str = "",
        min_clients: int = 1,
        num_rounds: int = 5,
        start_round: int = 0,
        wait_time_after_min_received: int = 10,
        aggregator_id=AppConstants.DEFAULT_AGGREGATOR_ID,
        persistor_id=AppConstants.DEFAULT_PERSISTOR_ID,
        shareable_generator_id=AppConstants.DEFAULT_SHAREABLE_GENERATOR_ID,
        train_task_name=AppConstants.TASK_TRAIN,
        train_timeout: int = 0,
        ignore_result_error: bool = False,
        fatal_error_delay: int = 5,
        task_check_period: float = 0.5,
        persist_every_n_rounds: int = 1,
    ):
        """The controller for FederatedAveraging Workflow.

        The ScatterAndGather workflow defines Federated training on all clients.
        The model persistor (persistor_id) is used to load the initial global model which is sent to all clients.
        Each clients sends it's updated weights after local training which is aggregated (aggregator_id). The
        shareable generator is used to convert the aggregated weights to shareable and shareable back to weights.
        The model_persistor also saves the model after training.

        Args:
            model_id (str, required): ID of the model that the training is being performed under.
            min_clients (int, optional): Min number of clients in training. Defaults to 1.
            num_rounds (int, optional): The total number of training rounds. Defaults to 5.
            start_round (int, optional): Start round for training. Defaults to 0.
            wait_time_after_min_received (int, optional): Time to wait before beginning aggregation after
                contributions received. Defaults to 10.
            aggregator_id (str, optional): ID of the aggregator component. Defaults to "aggregator".
            persistor_id (str, optional): ID of the persistor component. Defaults to "persistor".
            shareable_generator_id (str, optional): ID of the shareable generator. Defaults to "shareable_generator".
            train_task_name (str, optional): Name of the train task. Defaults to "train".
            train_timeout (int, optional): Time to wait for clients to do local training.
            ignore_result_error (bool, optional): whether this controller can proceed if result has errors. Defaults to
                False.
            fatal_error_delay (int, optional): Time in seconds to delay before calling 'system_panic' if a task returns
                an error result and ignore_result_error is set to false
            task_check_period (float, optional): interval for checking status of tasks. Defaults to 0.5.
            persist_every_n_rounds (int, optional): persist the global model every n rounds. Defaults to 0.
                If n is 0 then no persist.

        Raises:
            TypeError: when any of input arguments does not have correct type
            ValueError: when any of input arguments is out of range or are in an incorrect format
        """
        super().__init__(task_check_period=task_check_period)

        # flip
        self.flip = FLIP()

        # Check arguments
        try:
            _check_non_neg_int(min_clients, "min_clients")
            _check_non_neg_int(num_rounds, "num_rounds")
            _check_non_neg_int(start_round, "start_round")
            _check_non_neg_int(wait_time_after_min_received, "wait_time_after_min_received")
            _check_non_neg_int(train_timeout, "train_timeout")
            _check_non_neg_int(persist_every_n_rounds, "persist_every_n_rounds")

            if not isinstance(aggregator_id, str):
                raise TypeError(f"aggregator_id must be a string but got {type(aggregator_id)}")
            if not isinstance(persistor_id, str):
                raise TypeError(f"persistor_id must be a string but got {type(persistor_id)}")
            if not isinstance(shareable_generator_id, str):
                raise TypeError(f"shareable_generator_id must be a string but got {type(shareable_generator_id)}")
            if not isinstance(train_task_name, str):
                raise TypeError(f"train_task_name must be a string but got {type(train_task_name)}")
            if not isinstance(ignore_result_error, bool):
                raise TypeError(f"ignore_result_error must be a bool but got {type(ignore_result_error)}")
            if not Utils.is_valid_uuid(model_id):
                raise ValueError(f"The model ID: {model_id} is not a valid UUID")
        except Exception as e:
            self.flip.update_status(model_id, ModelStatus.ERROR)
            raise Exception(e)

        self.model_id = model_id

        self.aggregator_id = aggregator_id
        self.persistor_id = persistor_id
        self.shareable_generator_id = shareable_generator_id
        self.train_task_name = train_task_name
        self.aggregator = None
        self.persistor = None
        self.shareable_gen = None

        # config data
        self._min_clients = min_clients
        self._num_rounds = num_rounds
        self._wait_time_after_min_received = wait_time_after_min_received  # 5 minutes
        self._start_round = start_round
        self._train_timeout = train_timeout
        self._ignore_result_error = ignore_result_error
        self._fatal_error_delay = fatal_error_delay
        self._persist_every_n_rounds = persist_every_n_rounds

        # workflow phases: init, train, validate
        self._phase = AppConstants.PHASE_INIT
        self._global_weights: Learnable | None = None
        self._current_round: int | None = None

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
                f"Shareable generator {self.shareable_generator_id} must be a ShareableGenerator type object"
                f"but got {type(self.shareable_gen)}",
                fl_ctx,
            )
            return

        self.persistor = engine.get_component(self.persistor_id)
        if not isinstance(self.persistor, LearnablePersistor):
            self.system_panic(
                f"Model Persistor {self.persistor_id} must be a LearnablePersistor type object but"
                "got {type(self.persistor)}",
                fl_ctx,
            )
            return

        # initialize global model
        fl_ctx.set_prop(AppConstants.START_ROUND, self._start_round, private=True, sticky=True)
        fl_ctx.set_prop(AppConstants.NUM_ROUNDS, self._num_rounds, private=True, sticky=False)
        self._global_weights = self.persistor.load(fl_ctx)
        fl_ctx.set_prop(AppConstants.GLOBAL_MODEL, self._global_weights, private=True, sticky=False)
        self.fire_event(AppEventType.INITIAL_MODEL_LOADED, fl_ctx)

    def control_flow(self, abort_signal: Signal, fl_ctx: FLContext) -> None:
        try:
            self.log_info(fl_ctx, "Beginning ScatterAndGather training phase.")
            self._phase = AppConstants.PHASE_TRAIN

            fl_ctx.set_prop(AppConstants.PHASE, self._phase, private=True, sticky=False)
            fl_ctx.set_prop(AppConstants.NUM_ROUNDS, self._num_rounds, private=True, sticky=False)
            self.fire_event(AppEventType.TRAINING_STARTED, fl_ctx)

            if self._current_round is None:
                self._current_round = self._start_round
            while self._current_round < self._start_round + self._num_rounds:
                if self._check_abort_signal(fl_ctx, abort_signal):
                    return

                self.log_info(fl_ctx, f"Round {self._current_round} started.")
                fl_ctx.set_prop(AppConstants.GLOBAL_MODEL, self._global_weights, private=True, sticky=False)
                fl_ctx.set_prop(AppConstants.CURRENT_ROUND, self._current_round, private=True, sticky=False)
                self.fire_event(AppEventType.ROUND_STARTED, fl_ctx)

                # Create train_task
                data_shareable: Shareable = self.shareable_gen.learnable_to_shareable(self._global_weights, fl_ctx)
                data_shareable.set_header(AppConstants.CURRENT_ROUND, self._current_round)
                data_shareable.set_header(AppConstants.NUM_ROUNDS, self._num_rounds)
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

                self.log_info(fl_ctx, "Start aggregation.")
                self.fire_event(AppEventType.BEFORE_AGGREGATION, fl_ctx)
                aggr_result = self.aggregator.aggregate(fl_ctx)
                fl_ctx.set_prop(AppConstants.AGGREGATION_RESULT, aggr_result, private=True, sticky=False)
                self.fire_event(AppEventType.AFTER_AGGREGATION, fl_ctx)
                self.log_info(fl_ctx, "End aggregation.")

                if self._check_abort_signal(fl_ctx, abort_signal):
                    return

                self.fire_event(AppEventType.BEFORE_SHAREABLE_TO_LEARNABLE, fl_ctx)
                self._global_weights = self.shareable_gen.shareable_to_learnable(aggr_result, fl_ctx)
                fl_ctx.set_prop(AppConstants.GLOBAL_MODEL, self._global_weights, private=True, sticky=False)
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
                self.log_info(fl_ctx, f"Round {self._current_round} finished.")
                self._current_round += 1

            self._phase = AppConstants.PHASE_FINISHED
            self.log_info(fl_ctx, "Finished ScatterAndGather Training.")
        except BaseException as e:
            error_msg = f"Exception in ScatterAndGather control_flow: {secure_format_exception(e)}"
            self.log_exception(fl_ctx, error_msg)
            self.system_panic(error_msg, fl_ctx)

    def stop_controller(self, fl_ctx: FLContext) -> None:
        self._phase = AppConstants.PHASE_FINISHED
        self.cancel_all_tasks()

    def handle_event(self, event_type: str, fl_ctx: FLContext):
        super().handle_event(event_type, fl_ctx)
        if event_type == InfoCollector.EVENT_TYPE_GET_STATS:
            collector = fl_ctx.get_prop(InfoCollector.CTX_KEY_STATS_COLLECTOR, None)
            if collector:
                if not isinstance(collector, GroupInfoCollector):
                    self.fire_event(EventType.FATAL_SYSTEM_ERROR)
                    raise TypeError("collector must be GroupInfoCollector but got {}".format(type(collector)))

                collector.add_info(
                    group_name=self._name,
                    info={"phase": self._phase, "current_round": self._current_round, "num_rounds": self._num_rounds},
                )

        if event_type == FlipEvents.SEND_RESULT:
            event_data = fl_ctx.get_prop(FLContextKey.EVENT_DATA, None)
            if event_data is None:
                self.log_error(fl_ctx, "Metrics Error: metrics result event was fired but no data found")
                return

            self.flip.handle_metrics_event(event_data, self._current_round, self.model_id)

    def _prepare_train_task_data(self, client_task: ClientTask, fl_ctx: FLContext) -> None:
        fl_ctx.set_prop(AppConstants.TRAIN_SHAREABLE, client_task.task.data, private=True, sticky=False)
        self.fire_event(AppEventType.BEFORE_TRAIN_TASK, fl_ctx)

    def _process_train_result(self, client_task: ClientTask, fl_ctx: FLContext) -> None:
        result = client_task.result
        client_name = client_task.client.name

        self._accept_train_result(client_name=client_name, result=result, fl_ctx=fl_ctx)

        # Cleanup task result
        client_task.result = None

    def process_result_of_unknown_task(
        self, client: Client, task_name, client_task_id, result: Shareable, fl_ctx: FLContext
    ) -> None:
        if self._phase == AppConstants.PHASE_TRAIN and task_name == self.train_task_name:
            self._accept_train_result(client_name=client.name, result=result, fl_ctx=fl_ctx)
            self.log_info(fl_ctx, f"Result of unknown task {task_name} sent to aggregator.")
        else:
            self.log_error(fl_ctx, "Ignoring result from unknown task.")

    def _accept_train_result(self, client_name: str, result: Shareable, fl_ctx: FLContext) -> bool:
        rc = result.get_return_code()
        contribution_round = result.get_cookie(AppConstants.CONTRIBUTION_ROUND)
        result.set_header(AppConstants.CONTRIBUTION_ROUND, contribution_round)

        # Raise errors if bad peer context or execution exception.
        if rc and rc != ReturnCode.OK:
            if self._ignore_result_error:
                self.log_error(fl_ctx, f"Ignore the client train result. Train result error code: {rc}")
                return False
            else:
                if rc in [ReturnCode.MISSING_PEER_CONTEXT, ReturnCode.BAD_PEER_CONTEXT]:
                    self.system_panic("Peer context is bad or missing. ScatterAndGather exiting.", fl_ctx=fl_ctx)
                    return False
                elif rc in [ReturnCode.EXECUTION_EXCEPTION, ReturnCode.TASK_UNKNOWN]:
                    formatted_exception = result.get_header("exception")

                    if formatted_exception is not None:
                        self.log_error(fl_ctx, formatted_exception)
                        self.flip.send_handled_exception(
                            formatted_exception=formatted_exception, client_name=client_name, model_id=self.model_id
                        )

                    self.system_panic(
                        "Execution Exception in client training. ScatterAndGather exiting.", fl_ctx=fl_ctx
                    )
                    return False
                elif rc in [
                    ReturnCode.EXECUTION_RESULT_ERROR,
                    ReturnCode.TASK_DATA_FILTER_ERROR,
                    ReturnCode.TASK_RESULT_FILTER_ERROR,
                ]:
                    self.system_panic("Execution result is not a shareable. ScatterAndGather exiting.", fl_ctx=fl_ctx)
                    return False

        try:
            dxo = from_shareable(result)
            if dxo.data_kind == DataKind.WEIGHT_DIFF:
                # FedAVG receives WEIGHTS, so we need to update the WEIGHTS with the contents of WEIGHT_DIFF passed.
                global_weights = self._global_weights["weights"]
                diff = dxo.data
                new_weights = {}
                for k in global_weights:
                    new_weights[k] = global_weights[k] + diff[k]
                new_dxo = DXO(data_kind=DataKind.WEIGHTS, data=new_weights, meta=dxo.meta)
                result = new_dxo.update_shareable(result)
        except Exception as e:
            self.log_error(fl_ctx, f"Error while adding client WEIGHT_DIFF to global weights at server: {e}")

        fl_ctx.set_prop(AppConstants.CURRENT_ROUND, self._current_round, private=True, sticky=False)
        fl_ctx.set_prop(AppConstants.TRAINING_RESULT, result, private=True, sticky=False)
        fl_ctx.set_prop(AppConstants.CONTRIBUTION_ROUND, contribution_round, private=True, sticky=False)
        self.fire_event(AppEventType.BEFORE_CONTRIBUTION_ACCEPT, fl_ctx)

        accepted = self.aggregator.accept(result, fl_ctx)
        accepted_msg = "ACCEPTED" if accepted else "REJECTED"
        self.log_info(fl_ctx, f"Contribution from {client_name} {accepted_msg} by the aggregator.")

        fl_ctx.set_prop(AppConstants.AGGREGATION_ACCEPTED, accepted, private=True, sticky=False)
        self.fire_event(AppEventType.AFTER_CONTRIBUTION_ACCEPT, fl_ctx)

        return accepted

    def _check_abort_signal(self, fl_ctx, abort_signal: Signal):
        if abort_signal.triggered:
            self._phase = AppConstants.PHASE_FINISHED
            self.log_info(fl_ctx, f"Abort signal received. Exiting at round {self._current_round}.")
            self.fire_event(FlipEvents.ABORTED, fl_ctx)
            return True
        return False

    def get_persist_state(self, fl_ctx: FLContext) -> dict:
        return {
            "current_round": self._current_round,
            "start_round": self._start_round,
            "num_rounds": self._num_rounds,
            "global_weights": self._global_weights,
        }

    def restore(self, state_data: dict, fl_ctx: FLContext):
        try:
            self._current_round = state_data.get("current_round")
            self._start_round = state_data.get("start_round")
            self._num_rounds = state_data.get("num_rounds")
            self._global_weights = state_data.get("global_weights")
        finally:
            pass
