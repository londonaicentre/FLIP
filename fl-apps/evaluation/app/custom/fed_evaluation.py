# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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
from typing import Union

from nvflare.apis.client import Client
from nvflare.apis.controller_spec import ClientTask, Task
from nvflare.apis.dxo import DXO, from_bytes
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
from nvflare.security.logging import secure_format_exception
from nvflare.widgets.info_collector import GroupInfoCollector, InfoCollector
from utils.utils import Utils

from flip import FLIP
from flip.constants import FlipTasks, PTConstants


class ModelEval(Controller):
    def __init__(
        self,
        task_check_period=0.5,
        submit_model_timeout=600,
        validation_timeout: int = 6000,
        model_locator_id="",
        formatter_id="",
        submit_model_task_name=AppConstants.TASK_SUBMIT_MODEL,
        evaluation_task_name=PTConstants.EvalTaskName,
        cleanup_models=False,
        participating_clients=None,
        wait_for_clients_timeout=300,
        cleanup_timeout=600,
        fatal_error_delay=5,
        model_id="",
    ):
        """Model Evaluation workflow.

        Args:
            task_check_period (float, optional): How often to check for new tasks or tasks being finished.
                Defaults to 0.5.
            submit_model_timeout (int, optional): Timeout of submit_model_task. Defaults to 600 secs.
            validation_timeout (int, optional): Timeout for validate_model task. Defaults to 6000 secs.
            model_locator_id (str, optional): ID for model_locator component. Defaults to "".
            formatter_id (str, optional): ID for formatter component. Defaults to "".
            submit_model_task_name (str, optional): Name of submit_model task. Defaults to "".
            validation_task_name (str, optional): Name of validate_model task. Defaults to "validate".
            cleanup_models (bool, optional): Whether models should be deleted after run. Defaults to False.
            participating_clients (list, optional): List of participating client names. If not provided, defaults
                to all clients connected at start of controller.
            wait_for_clients_timeout (int, optional): Timeout for clients to appear. Defaults to 300 secs
            fatal_error_delay (int, optional): Time in seconds to delay before calling 'system_panic'
                if a task returns an error result and ignore_result_error is set to false
            model_id (str, required): ID of the model that the training is being performed under.
        """
        super(ModelEval, self).__init__(task_check_period=task_check_period)

        # flip
        self.flip = FLIP()

        if not isinstance(task_check_period, float):
            raise TypeError("task_check_period must be float but got {}".format(type(task_check_period)))
        if not isinstance(submit_model_timeout, int):
            raise TypeError("submit_model_timeout must be int but got {}".format(type(submit_model_timeout)))
        if not isinstance(validation_timeout, int):
            raise TypeError("validation_timeout must be int but got {}".format(type(validation_timeout)))
        if not isinstance(model_locator_id, str):
            raise TypeError("model_locator_id must be a string but got {}".format(type(model_locator_id)))
        if not isinstance(formatter_id, str):
            raise TypeError("formatter_id must be a string but got {}".format(type(formatter_id)))
        if not isinstance(submit_model_task_name, str):
            raise TypeError("submit_model_task_name must be a string but got {}".format(type(submit_model_task_name)))
        if not isinstance(evaluation_task_name, str):
            raise TypeError("evaluation_task_name must be a string but got {}".format(type(evaluation_task_name)))
        if not isinstance(cleanup_models, bool):
            raise TypeError("cleanup_models must be bool but got {}".format(type(cleanup_models)))
        if not Utils.is_valid_uuid(model_id):
            raise ValueError(f"The model ID: {model_id} is not a valid UUID")

        if participating_clients:
            if not isinstance(participating_clients, list):
                raise TypeError("participating_clients must be a list but got {}".format(type(participating_clients)))
            if not all(isinstance(x, str) for x in participating_clients):
                raise TypeError("participating_clients must be strings")

        if submit_model_timeout < 0:
            raise ValueError("submit_model_timeout must be greater than or equal to 0.")
        if validation_timeout < 0:
            raise ValueError("model_validate_timeout must be greater than or equal to 0.")
        if wait_for_clients_timeout < 0:
            raise ValueError("wait_for_clients_timeout must be greater than or equal to 0.")
        if cleanup_timeout < 0:
            raise ValueError("cleanup_timeout must be greater than or equal to 0.")

        self._eval_dir = PTConstants.EvalDir
        self._model_locator_id = model_locator_id
        self._formatter_id = formatter_id
        self._submit_model_task_name = submit_model_task_name
        # This stays VALIDATION for NVFLARE compatibility.
        self._evaluation_task_name = evaluation_task_name
        self._submit_model_timeout = submit_model_timeout
        self._validation_timeout = validation_timeout
        self._wait_for_clients_timeout = wait_for_clients_timeout
        self._cleanup_models = cleanup_models
        self._participating_clients = participating_clients

        self._eval_results = {}
        self._models = {}
        self._all_models_dxo = None
        self._client_models = {}

        self._formatter = None
        self._eval_results_dir = PTConstants.EvalDir
        self._model_locator = None
        self._cleanup_timeout = cleanup_timeout
        self._fatal_error_delay = fatal_error_delay
        self._model_id = model_id

    def start_controller(self, fl_ctx: FLContext):
        engine = fl_ctx.get_engine()
        if not engine:
            self.system_panic("Engine not found. Workflow exiting.", fl_ctx)
            return

        # If the list of participating clients is not provided, include all clients currently available.
        if not self._participating_clients:
            clients = engine.get_clients()
            self._participating_clients = [c.name for c in clients]

        # Create shareable dirs for models and results
        workspace: Workspace = engine.get_workspace()
        run_dir = workspace.get_run_dir(fl_ctx.get_job_id())
        eval_path = os.path.join(run_dir, self._eval_results_dir)
        self._eval_results_dir = eval_path
        # Initialise the model locator
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

        # Fire the init event.
        fl_ctx.set_prop(AppConstants.CROSS_VAL_RESULTS_PATH, self._eval_results_dir)
        self.fire_event(AppEventType.CROSS_VAL_INIT, fl_ctx)

        # Cleanup/create the cross val models and results directories
        if os.path.exists(self._eval_results_dir):
            shutil.rmtree(self._eval_results_dir)

        # Recreate new directories.
        os.makedirs(self._eval_results_dir)

        # Get components
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

    def control_flow(self, abort_signal: Signal, fl_ctx: FLContext):
        try:
            # wait until there are some clients
            engine = fl_ctx.get_engine()
            start_time = time.time()
            while not self._participating_clients:
                self._participating_clients = engine.get_clients()
                if time.time() - start_time > self._wait_for_clients_timeout:
                    self.log_info(fl_ctx, "No clients available - quit model validation.")
                    return

                self.log_info(fl_ctx, "No clients available - waiting ...")
                time.sleep(2.0)
                if abort_signal.triggered:
                    self.log_info(fl_ctx, "Abort signal triggered. Finishing model validation.")
                    return

            self.log_info(fl_ctx, f"Beginning model validation with clients: {self._participating_clients}.")

            # This loads models in the evaluation directory, and saves them in self._models.
            if self._model_locator:
                success = self._locate_server_models(fl_ctx)
                if not success:
                    return

            # Do the validation task

            task = Task(
                name=self._evaluation_task_name,
                data=Shareable(),
                before_task_sent_cb=self._before_send_validate_task_cb,
                after_task_sent_cb=self._after_send_validate_task_cb,
                result_received_cb=self._receive_val_result_cb,
                timeout=self._validation_timeout,
            )

            self.broadcast(
                task=task,
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
                    self.log_info(fl_ctx, "Abort signal triggered. Finishing cross site validation.")
                    return
                self.log_debug(fl_ctx, "Checking standing tasks to see if cross site validation finished.")
                time.sleep(self._task_check_period)

            self.log_info(fl_ctx, "Beginning post validation cleanup task...")

            cleanup_task = Task(name=FlipTasks.POST_TASK, data=Shareable(), timeout=self._cleanup_timeout)

            self.broadcast_and_wait(
                task=cleanup_task,
                min_responses=len(self._participating_clients),
                wait_time_after_min_received=0,
                fl_ctx=fl_ctx,
                abort_signal=abort_signal,
            )

            self.log_info(fl_ctx, "Post validation cleanup completed")

        except BaseException as e:
            error_msg = f"Exception in cross site validator control_flow: {secure_format_exception(e)}"
            self.log_exception(fl_ctx, error_msg)
            self.system_panic(error_msg, fl_ctx)

    def stop_controller(self, fl_ctx: FLContext):
        self.cancel_all_tasks(fl_ctx=fl_ctx)

        if self._cleanup_models:
            self.log_info(fl_ctx, "Removing local models kept for validation.")
            for model_name, model_path in self._models.items():
                if model_path and os.path.isfile(model_path):
                    os.remove(model_path)
                    self.log_debug(fl_ctx, f"Removing server model {model_name} at {model_path}.")
            for model_name, model_path in self._client_models.items():
                if model_path and os.path.isfile(model_path):
                    os.remove(model_path)
                    self.log_debug(fl_ctx, f"Removing client {model_name}'s model at {model_path}.")

    def _receive_local_model_cb(self, client_task: ClientTask, fl_ctx: FLContext):
        client_name = client_task.client.name
        result: Shareable = client_task.result

        self._accept_local_model(client_name=client_name, result=result, fl_ctx=fl_ctx)

        # Cleanup task result
        client_task.result = None

    def _after_send_validate_task_cb(self, client_task: ClientTask, fl_ctx: FLContext):
        # Once task is sent clear data to restore memory
        client_task.task.data = None

    def _receive_val_result_cb(self, client_task: ClientTask, fl_ctx: FLContext):
        # Find name of the client sending this
        result = client_task.result
        client_name = client_task.client.name

        self.log_info(fl_ctx, f"Receiving validation result from client: {client_name}: {result}")
        self._accept_val_result(client_name=client_name, result=result, fl_ctx=fl_ctx)

        client_task.result = None

    def _locate_server_models(self, fl_ctx: FLContext) -> bool:
        # Load models from model_locator
        self.log_info(fl_ctx, "Locating server models.")

        # Evaluation allows us to load a collection of models to test:
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
        """Before the evaluation task, we load all the models DXO into a Shareable that is sent to the clients."""

        if not self._all_models_dxo:
            self.system_panic(
                "Model contents empty.",
                fl_ctx=fl_ctx,
            )
            return

        model_shareable = self._all_models_dxo.to_shareable()
        client_task.task.data = model_shareable

        fl_ctx.set_prop(AppConstants.DATA_CLIENT, client_task.client, private=True, sticky=False)
        fl_ctx.set_prop(AppConstants.MODEL_TO_VALIDATE, model_shareable, private=True, sticky=False)
        fl_ctx.set_prop(AppConstants.PARTICIPATING_CLIENTS, self._participating_clients, private=True, sticky=False)
        self.fire_event(AppEventType.SEND_MODEL_FOR_VALIDATION, fl_ctx)

    def _accept_local_model(self, client_name: str, result: Shareable, fl_ctx: FLContext):
        fl_ctx.set_prop(AppConstants.RECEIVED_MODEL, result, private=False, sticky=False)
        fl_ctx.set_prop(AppConstants.CROSS_VAL_DIR, self._cross_val_dir, private=False, sticky=False)
        self.fire_event(AppEventType.RECEIVE_BEST_MODEL, fl_ctx)

        # get return code
        rc = result.get_return_code()
        if rc and rc != ReturnCode.OK:
            time.sleep(self._fatal_error_delay)
            # Raise errors if bad peer context or execution exception.
            if rc in [ReturnCode.MISSING_PEER_CONTEXT, ReturnCode.BAD_PEER_CONTEXT]:
                self.log_error(fl_ctx, "Peer context is bad or missing. No model submitted for this client.")
            elif rc in [ReturnCode.EXECUTION_EXCEPTION, ReturnCode.TASK_UNKNOWN]:
                formatted_exception = result.get_header("exception")

                if formatted_exception is not None:
                    self.log_error(fl_ctx, formatted_exception)
                    self.flip.send_handled_exception(
                        formatted_exception=formatted_exception, client_name=client_name, model_id=self._model_id
                    )

                self.log_error(
                    fl_ctx, "Execution Exception on client during model submission. No model submitted for this client."
                )
            # Ignore contribution if result invalid.
            elif rc in [
                ReturnCode.EXECUTION_RESULT_ERROR,
                ReturnCode.TASK_DATA_FILTER_ERROR,
                ReturnCode.TASK_RESULT_FILTER_ERROR,
                ReturnCode.TASK_UNKNOWN,
            ]:
                self.log_error(fl_ctx, "Execution result is not a shareable. Model submission will be ignored.")
            else:
                self.log_error(fl_ctx, "Return code set. Model submission from client will be ignored.")
        else:
            # Save shareable in models directory.
            # try:
            #    self.log_debug(fl_ctx, "Extracting DXO from shareable.")
            #    dxo = from_shareable(result)
            #    save_path = self._save_validation_content(client_name, self._cross_val_models_dir, dxo, fl_ctx)
            # except ValueError as v_e:
            #    self.log_error(
            #        fl_ctx, f"Unable to save shareable contents of {client_name}'s model. Exception: {str(v_e)}"
            #    )
            #    self.log_warning(fl_ctx, f"Ignoring client {client_name}'s model.")
            #    return

            self.log_info(fl_ctx, f"Received local model from client {client_name}.")

            # self._client_models[client_name] = save_path

            # Send a model to this client to validate
            self._send_validation_task(client_name, fl_ctx)

    def _accept_val_result(self, client_name: str, result: Shareable, fl_ctx: FLContext):
        # Fire event. This needs to be a new local context per each client

        fl_ctx.set_prop(AppConstants.DATA_CLIENT, client_name, private=True, sticky=False)
        fl_ctx.set_prop(AppConstants.VALIDATION_RESULT, result, private=True, sticky=False)
        self.fire_event(AppEventType.VALIDATION_RESULT_RECEIVED, fl_ctx)

        rc = result.get_return_code()
        if rc and rc != ReturnCode.OK:
            # Raise errors if bad peer context or execution exception.
            if rc in [ReturnCode.MISSING_PEER_CONTEXT, ReturnCode.BAD_PEER_CONTEXT]:
                self.log_error(fl_ctx, "Peer context is bad or missing.")
            elif rc in [ReturnCode.EXECUTION_EXCEPTION, ReturnCode.TASK_UNKNOWN]:
                formatted_exception = result.get_header("exception")

                if formatted_exception is not None:
                    self.log_error(fl_ctx, formatted_exception)
                    self.flip.send_handled_exception(
                        formatted_exception=formatted_exception, client_name=client_name, model_id=self._model_id
                    )

                self.log_error(fl_ctx, "Execution Exception in model validation.")
            elif rc in [
                ReturnCode.EXECUTION_RESULT_ERROR,
                ReturnCode.TASK_DATA_FILTER_ERROR,
                ReturnCode.TASK_RESULT_FILTER_ERROR,
            ]:
                self.log_error(fl_ctx, "Execution result is not a shareable. Validation results will be ignored.")
            else:
                self.log_error(
                    fl_ctx,
                    f"Client {client_name} sent results with return code set. Logging empty results.",
                )

            self._eval_results[client_name] = {}
        else:
            save_file_name = client_name

            try:
                self._eval_results[client_name] = os.path.join(self._eval_results_dir, save_file_name)

                self.log_info(fl_ctx, f"Client {client_name} sent results for validating model.")
            except ValueError as v_e:
                reason = f"Unable to save validation result from {client_name}. Exception: {str(v_e)}"
                self.log_exception(fl_ctx, reason)

    def _load_validation_content(self, name: str, load_dir: str, fl_ctx: FLContext) -> Union[DXO, None]:
        # Load shareable from disk
        shareable_filename = os.path.join(load_dir, name)
        dxo: DXO = None

        # load shareable
        try:
            with open(shareable_filename, "rb") as f:
                data = f.read()

            dxo: DXO = from_bytes(data)

            self.log_debug(fl_ctx, f"Loading cross validation shareable content with name: {name}.")
        except Exception as e:
            raise ValueError(f"Exception in loading shareable content for {name}: {secure_format_exception(e)}")

        return dxo

    def handle_event(self, event_type: str, fl_ctx: FLContext):
        super().handle_event(event_type=event_type, fl_ctx=fl_ctx)
        if event_type == InfoCollector.EVENT_TYPE_GET_STATS:
            if self._formatter:
                collector = fl_ctx.get_prop(InfoCollector.CTX_KEY_STATS_COLLECTOR, None)
                if collector:
                    if not isinstance(collector, GroupInfoCollector):
                        raise TypeError("collector must be GroupInfoCollector but got {}".format(type(collector)))

                    fl_ctx.set_prop(AppConstants.VALIDATION_RESULT, self._eval_results, private=True, sticky=False)
                    val_info = self._formatter.format(fl_ctx)

                    collector.add_info(
                        group_name=self._name,
                        info={"val_results": val_info},
                    )
            else:
                self.log_warning(fl_ctx, "No formatter provided. Validation results can't be printed.")

    def process_result_of_unknown_task(
        self, client: Client, task_name: str, client_task_id: str, result: Shareable, fl_ctx: FLContext
    ):
        if task_name == self._submit_model_task_name:
            self._accept_local_model(client_name=client.name, result=result, fl_ctx=fl_ctx)
        elif task_name == self._validation_task_name:
            self._accept_val_result(client_name=client.name, result=result, fl_ctx=fl_ctx)
        else:
            self.log_error(fl_ctx, "Ignoring result from unknown task.")
