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

import os
import shutil
import time

from nvflare.apis.controller_spec import ClientTask, Task
from nvflare.apis.dxo import DXO
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.impl.controller import Controller
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal
from nvflare.apis.workspace import Workspace
from nvflare.app_common.abstract.formatter import Formatter
from nvflare.app_common.abstract.model_locator import ModelLocator
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.app_event_type import AppEventType
from nvflare.app_common.workflows.cross_site_model_eval import CrossSiteModelEval
from nvflare.security.logging import secure_format_exception
from nvflare.widgets.info_collector import GroupInfoCollector, InfoCollector

from flip.constants import PTConstants


class ModelEval(CrossSiteModelEval):
    """FLIP model-collection evaluation: evaluate a set of server-provided models on every client.

    Subclasses NVFLARE's :class:`CrossSiteModelEval` to inherit its constructor validation,
    unknown-task routing, and stop logic. Unlike cross-site validation, where each client's
    submitted model is validated by every
    other client, evaluation loads a *collection* of models server-side and bundles them into a
    single evaluation task broadcast to all clients.

    Sharing the base also defines the ``_validation_task_name`` / ``_cross_val_dir`` attributes the
    previous standalone fork referenced but never assigned.
    """

    def __init__(
        self,
        task_check_period: float = 0.5,
        submit_model_timeout: int = 600,
        validation_timeout: int = 6000,
        model_locator_id: str = "",
        formatter_id: str = "",
        submit_model_task_name: str = AppConstants.TASK_SUBMIT_MODEL,
        evaluation_task_name: str = PTConstants.EvalTaskName,
        cleanup_models: bool = False,
        participating_clients: list[str] | None = None,
        wait_for_clients_timeout: int = 300,
    ) -> None:
        """Model-collection evaluation workflow.

        All arguments are validated and stored by the FLIP/stock base; the evaluation task is named
        by ``evaluation_task_name``, which is also wired into the stock ``validation_task_name``
        plumbing (so unknown-task routing works).

        Args:
            evaluation_task_name (str): Name of the evaluation task broadcast to clients. Defaults
                to ``PTConstants.EvalTaskName``.
        """
        super().__init__(
            task_check_period=task_check_period,
            submit_model_timeout=submit_model_timeout,
            validation_timeout=validation_timeout,
            model_locator_id=model_locator_id,
            formatter_id=formatter_id,
            submit_model_task_name=submit_model_task_name,
            validation_task_name=evaluation_task_name,
            cleanup_models=cleanup_models,
            participating_clients=participating_clients,
            wait_for_clients_timeout=wait_for_clients_timeout,
        )

        self._evaluation_task_name = evaluation_task_name
        self._eval_results_dir = PTConstants.EvalDir
        self._eval_results: dict = {}
        self._all_models_dxo = None

    def start_controller(self, fl_ctx: FLContext) -> None:
        engine = fl_ctx.get_engine()
        if not engine:
            self.system_panic("Engine not found. Workflow exiting.", fl_ctx)
            return

        # If the list of participating clients is not provided, include all clients currently available.
        if not self._participating_clients:
            self._participating_clients = [c.name for c in engine.get_clients()]

        workspace: Workspace = engine.get_workspace()
        run_dir = workspace.get_run_dir(fl_ctx.get_job_id())
        self._eval_results_dir = os.path.join(run_dir, self._eval_results_dir)

        # Fire the init event.
        fl_ctx.set_prop(AppConstants.CROSS_VAL_RESULTS_PATH, self._eval_results_dir)
        self.fire_event(AppEventType.CROSS_VAL_INIT, fl_ctx)

        # Cleanup/create the evaluation results directory.
        if os.path.exists(self._eval_results_dir):
            shutil.rmtree(self._eval_results_dir)
        os.makedirs(self._eval_results_dir)

        if self._model_locator_id:
            self._model_locator = engine.get_component(self._model_locator_id)
            if not isinstance(self._model_locator, ModelLocator):
                self.system_panic(
                    reason="bad model locator {}: expect ModelLocator but got {}".format(
                        self._model_locator_id, type(self._model_locator)
                    ),
                    fl_ctx=fl_ctx,
                )
                return

        if self._formatter_id:
            self._formatter = engine.get_component(self._formatter_id)
            if not isinstance(self._formatter, Formatter):
                self.system_panic(
                    reason=f"formatter {self._formatter_id} is not an instance of Formatter.", fl_ctx=fl_ctx
                )
                return

        if not self._formatter:
            self.log_info(fl_ctx, "Formatter not found. Stats will not be printed.")

        for c_name in self._participating_clients:
            self._client_models[c_name] = None
            self._eval_results[c_name] = {}

    def control_flow(self, abort_signal: Signal, fl_ctx: FLContext) -> None:
        try:
            engine = fl_ctx.get_engine()
            start_time = time.time()
            while not self._participating_clients:
                self._participating_clients = engine.get_clients()
                if time.time() - start_time > self._wait_for_clients_timeout:
                    self.log_info(fl_ctx, "No clients available - quit model evaluation.")
                    return

                self.log_info(fl_ctx, "No clients available - waiting ...")
                time.sleep(2.0)
                if abort_signal.triggered:
                    self.log_info(fl_ctx, "Abort signal triggered. Finishing model evaluation.")
                    return

            self.log_info(fl_ctx, f"Beginning model evaluation with clients: {self._participating_clients}.")

            # Load the collection of server models to evaluate into self._all_models_dxo.
            if self._model_locator:
                if not self._locate_server_models(fl_ctx):
                    return

            eval_task = Task(
                name=self._evaluation_task_name,
                data=Shareable(),
                before_task_sent_cb=self._before_send_validate_task_cb,
                after_task_sent_cb=self._after_send_validate_task_cb,
                result_received_cb=self._receive_val_result_cb,
                timeout=self._validation_timeout,
            )
            self.broadcast(
                task=eval_task,
                fl_ctx=fl_ctx,
                targets=self._participating_clients,
                min_responses=len(self._participating_clients),
                wait_time_after_min_received=0,
            )

            if abort_signal.triggered:
                self.log_info(fl_ctx, "Abort signal triggered. Finishing model evaluation.")
                return

            while self.get_num_standing_tasks():
                if abort_signal.triggered:
                    self.log_info(fl_ctx, "Abort signal triggered. Finishing model evaluation.")
                    return
                self.log_debug(fl_ctx, "Checking standing tasks to see if model evaluation finished.")
                time.sleep(self._task_check_period)

        except Exception as e:
            error_msg = f"Exception in model evaluation control_flow: {secure_format_exception(e)}"
            self.log_exception(fl_ctx, error_msg)
            self.system_panic(error_msg, fl_ctx)

    def _locate_server_models(self, fl_ctx: FLContext) -> bool:
        """Load the whole collection of models to evaluate from the model locator.

        Unlike stock (one DXO located per model name), evaluation loads every model in a single
        ``locate_model`` call and keeps the collection DXO to broadcast to clients.
        """
        self.log_info(fl_ctx, "Locating server models.")

        all_models = self._model_locator.locate_model(fl_ctx)
        for name in self._model_locator.model_names:
            model_dxo = all_models.data.get(name)
            if not isinstance(model_dxo, DXO):
                self.system_panic(f"ModelLocator gave a collection of models for which {name} is not a dxo.", fl_ctx)
                return False
            self._eval_results[name] = {}

        if self._model_locator.model_names:
            self.log_info(fl_ctx, f"Server models loaded: {self._model_locator.model_names}.")
        else:
            self.log_info(fl_ctx, "no server models to validate!")

        self._all_models_dxo = all_models
        return True

    def _before_send_validate_task_cb(self, client_task: ClientTask, fl_ctx: FLContext):
        """Bundle the whole model collection into the task data sent to each client."""
        if not self._all_models_dxo:
            self.system_panic("Model contents empty.", fl_ctx=fl_ctx)
            return

        model_shareable = self._all_models_dxo.to_shareable()
        client_task.task.data = model_shareable

        fl_ctx.set_prop(AppConstants.DATA_CLIENT, client_task.client, private=True, sticky=False)
        fl_ctx.set_prop(AppConstants.MODEL_TO_VALIDATE, model_shareable, private=True, sticky=False)
        fl_ctx.set_prop(AppConstants.PARTICIPATING_CLIENTS, self._participating_clients, private=True, sticky=False)
        self.fire_event(AppEventType.SEND_MODEL_FOR_VALIDATION, fl_ctx)

    def _accept_val_result(self, client_name: str, result: Shareable, fl_ctx: FLContext):
        """Record each client's evaluation result, keyed flatly by client."""
        fl_ctx.set_prop(AppConstants.DATA_CLIENT, client_name, private=True, sticky=False)
        fl_ctx.set_prop(AppConstants.VALIDATION_RESULT, result, private=True, sticky=False)
        self.fire_event(AppEventType.VALIDATION_RESULT_RECEIVED, fl_ctx)

        rc = result.get_return_code()
        if rc and rc != ReturnCode.OK:
            if rc in (ReturnCode.MISSING_PEER_CONTEXT, ReturnCode.BAD_PEER_CONTEXT):
                self.log_error(fl_ctx, "Peer context is bad or missing.")
            elif rc in (ReturnCode.EXECUTION_EXCEPTION, ReturnCode.TASK_UNKNOWN):
                self.log_error(fl_ctx, "Execution Exception in model evaluation.")
            elif rc in (
                ReturnCode.EXECUTION_RESULT_ERROR,
                ReturnCode.TASK_DATA_FILTER_ERROR,
                ReturnCode.TASK_RESULT_FILTER_ERROR,
            ):
                self.log_error(fl_ctx, "Execution result is not a shareable. Validation results will be ignored.")
            else:
                self.log_error(
                    fl_ctx, f"Client {client_name} sent results with return code set. Logging empty results."
                )
            self._eval_results[client_name] = {}
        else:
            self._eval_results[client_name] = os.path.join(self._eval_results_dir, client_name)
            self.log_info(fl_ctx, f"Client {client_name} sent results for validating model.")

    def handle_event(self, event_type: str, fl_ctx: FLContext) -> None:
        # Bypass stock CrossSiteModelEval.handle_event (it reports the unused, nested
        # self._val_results); run the base controller event handling, then report ModelEval's own
        # flat self._eval_results to the stats collector.
        Controller.handle_event(self, event_type=event_type, fl_ctx=fl_ctx)
        if event_type == InfoCollector.EVENT_TYPE_GET_STATS:
            if self._formatter:
                collector = fl_ctx.get_prop(InfoCollector.CTX_KEY_STATS_COLLECTOR, None)
                if collector:
                    if not isinstance(collector, GroupInfoCollector):
                        raise TypeError("collector must be GroupInfoCollector but got {}".format(type(collector)))

                    fl_ctx.set_prop(AppConstants.VALIDATION_RESULT, self._eval_results, private=True, sticky=False)
                    val_info = self._formatter.format(fl_ctx)

                    collector.add_info(group_name=self._name, info={"val_results": val_info})
            else:
                self.log_warning(fl_ctx, "No formatter provided. Validation results can't be printed.")
