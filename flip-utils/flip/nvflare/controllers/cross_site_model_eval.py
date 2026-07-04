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

from nvflare.apis.controller_spec import Task
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.workflows.cross_site_model_eval import CrossSiteModelEval as NVFlareCrossSiteModelEval
from nvflare.security.logging import secure_format_exception

from flip import FLIP
from flip.constants import FlipTasks
from flip.nvflare.runtime import get_flip_model_id


class CrossSiteModelEval(NVFlareCrossSiteModelEval):
    """Stock NVFLARE cross-site model evaluation plus FLIP integration.

    Subclasses ``nvflare.app_common.workflows.cross_site_model_eval.CrossSiteModelEval`` so the
    evaluation orchestration, T2 multi-DXO handling (``get_leaf_dxos``) and run-dir path-safety
    (``resolve_path_under_root``) track upstream instead of drifting from a vendored copy. FLIP
    adds only:

    * ``model_id`` — attributes any client execution exception reported back to the hub;
    * a ``POST_VALIDATION`` cleanup task broadcast to clients once evaluation completes normally;
    * reporting client execution exceptions to the hub via ``FLIP.send_handled_exception``.
    """

    # Declared for type-checkers: stock sets this in __init__ (as None then a client-name list);
    # annotating it here lets subclasses (e.g. ModelEval) reassign it without a has-type error.
    _participating_clients: list[str] | None

    def __init__(
        self,
        task_check_period=0.5,
        cross_val_dir=AppConstants.CROSS_VAL_DIR,
        submit_model_timeout=600,
        validation_timeout: int = 6000,
        model_locator_id="",
        formatter_id="",
        submit_model_task_name=AppConstants.TASK_SUBMIT_MODEL,
        validation_task_name=AppConstants.TASK_VALIDATION,
        cleanup_models=False,
        participating_clients=None,
        wait_for_clients_timeout=300,
        cleanup_timeout=600,
        model_id="",
    ):
        """Cross-site model evaluation workflow.

        All arguments other than the two below are validated and stored by stock
        ``CrossSiteModelEval``; see its docstring.

        Args:
            cleanup_timeout (int): Timeout in seconds for the POST_VALIDATION cleanup broadcast
                that runs once evaluation completes. Defaults to 600.
            model_id (str): ID of the model being evaluated, attached to any client execution
                exception reported to the hub. Resolved lazily from the FL context, falling back
                to this value.
        """
        super().__init__(
            task_check_period=task_check_period,
            cross_val_dir=cross_val_dir,
            submit_model_timeout=submit_model_timeout,
            validation_timeout=validation_timeout,
            model_locator_id=model_locator_id,
            formatter_id=formatter_id,
            submit_model_task_name=submit_model_task_name,
            validation_task_name=validation_task_name,
            cleanup_models=cleanup_models,
            participating_clients=participating_clients,
            wait_for_clients_timeout=wait_for_clients_timeout,
        )

        if not isinstance(cleanup_timeout, int):
            raise TypeError("cleanup_timeout must be int but got {}".format(type(cleanup_timeout)))
        if cleanup_timeout < 0:
            raise ValueError("cleanup_timeout must be greater than or equal to 0.")

        self.flip = FLIP()
        self._cleanup_timeout = cleanup_timeout
        self._model_id_fallback = model_id
        self._model_id: str | None = None

    def _resolve_model_id(self, fl_ctx: FLContext) -> str:
        """Lazily resolve the FLIP model id from the FL context, falling back to the ctor value."""
        if self._model_id is None:
            self._model_id = get_flip_model_id(fl_ctx, fallback=self._model_id_fallback)
        return self._model_id

    def control_flow(self, abort_signal: Signal, fl_ctx: FLContext):
        """Run stock cross-site evaluation, then broadcast a POST_VALIDATION cleanup task.

        Stock ``control_flow`` returns early on every abort/failure path, and ``system_panic``
        synchronously triggers ``abort_signal`` (via the server runner's FATAL_SYSTEM_ERROR
        handler). Guarding the cleanup on ``abort_signal.triggered`` therefore keeps it from
        running after an abort or a fatal error — a naive ``super() + append`` would wrongly run
        it post-abort. An empty participating-clients list means stock quit before evaluating, so
        there is nothing to clean up.
        """
        super().control_flow(abort_signal, fl_ctx)

        if abort_signal.triggered:
            self.log_info(fl_ctx, "Abort signal triggered - skipping post-validation cleanup.")
            return
        if not self._participating_clients:
            self.log_info(fl_ctx, "No participating clients - skipping post-validation cleanup.")
            return

        try:
            self.log_info(fl_ctx, "Beginning post validation cleanup task...")
            cleanup_task = Task(name=FlipTasks.POST_VALIDATION.value, data=Shareable(), timeout=self._cleanup_timeout)
            self.broadcast_and_wait(
                task=cleanup_task,
                min_responses=len(self._participating_clients),
                wait_time_after_min_received=0,
                fl_ctx=fl_ctx,
                abort_signal=abort_signal,
            )
            self.log_info(fl_ctx, "Post validation cleanup completed")
        except Exception as e:
            error_msg = f"Exception in post-validation cleanup: {secure_format_exception(e)}"
            self.log_exception(fl_ctx, error_msg)
            self.system_panic(error_msg, fl_ctx)

    def _accept_local_model(self, client_name: str, result: Shareable, fl_ctx: FLContext):
        """Report any client execution exception to the hub, then delegate to stock handling."""
        self._report_client_exception(client_name, result, fl_ctx)
        super()._accept_local_model(client_name=client_name, result=result, fl_ctx=fl_ctx)

    def _accept_val_result(self, client_name: str, result: Shareable, fl_ctx: FLContext):
        """Report any client execution exception to the hub, then delegate to stock handling."""
        self._report_client_exception(client_name, result, fl_ctx)
        super()._accept_val_result(client_name=client_name, result=result, fl_ctx=fl_ctx)

    def _report_client_exception(self, client_name: str, result: Shareable, fl_ctx: FLContext):
        """Surface a client-side execution exception to the hub, if the result carries one.

        Stock only logs execution exceptions; FLIP additionally forwards the client's formatted
        traceback to the hub so it can be shown against the model run.

        Args:
            client_name (str): Name of the client that produced the result.
            result (Shareable): The task result received from the client.
            fl_ctx (FLContext): The current FL context.
        """
        rc = result.get_return_code()
        if rc in (ReturnCode.EXECUTION_EXCEPTION, ReturnCode.TASK_UNKNOWN):
            formatted_exception = result.get_header("exception")
            if formatted_exception is not None:
                self.log_error(fl_ctx, formatted_exception)
                self.flip.send_handled_exception(
                    formatted_exception=formatted_exception,
                    client_name=client_name,
                    model_id=self._resolve_model_id(fl_ctx),
                )
