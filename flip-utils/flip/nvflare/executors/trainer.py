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

"""
RUN_TRAINER Executor.

This module provides the RUN_TRAINER executor that wraps user-provided
FLIP_TRAINER classes with error handling.
"""

from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.app_common.app_constant import AppConstants
from nvflare.security.logging import secure_format_traceback


class RUN_TRAINER(Executor):
    """
    Wrapper executor that runs user-provided FLIP_TRAINER implementations.

    This executor handles:
    - Dynamic importing of the user's FLIP_TRAINER class
    - Error handling and exception logging
    - Lazy initialization of the trainer instance
    """

    def __init__(
        self,
        train_task_name=AppConstants.TASK_TRAIN,
        submit_model_task_name=AppConstants.TASK_SUBMIT_MODEL,
        exclude_vars=None,
        project_id="",
        query="",
    ):
        """
        Initialize the RUN_TRAINER executor.

        Args:
            train_task_name: Task name for train task. Defaults to "train".
            submit_model_task_name: Task name for submit model. Defaults to "submit_model".
            exclude_vars: List of variables to exclude during model loading.
            project_id: The ID of the project the model belongs to.
            query: The cohort query that is associated with the project.
        """
        super(RUN_TRAINER, self).__init__()

        self._train_task_name = train_task_name
        self._submit_model_task_name = submit_model_task_name
        self._exclude_vars = exclude_vars
        self._project_id = project_id
        self._query = query
        self._flip_trainer = None
        self._epochs = None

    def execute(
        self,
        task_name: str,
        shareable: Shareable,
        fl_ctx: FLContext,
        abort_signal: Signal,
    ) -> Shareable:
        """
        Execute the training task.

        This method:
        1. Lazily imports and initializes the user's FLIP_TRAINER
        2. Delegates execution to the user's trainer
        3. Catches and reports any exceptions

        Args:
            task_name: The name of the task to execute
            shareable: The input shareable data
            fl_ctx: The FL context
            abort_signal: Signal for aborting the task

        Returns:
            Shareable: The result of the training task
        """
        try:
            # Diagnostic logging: log incoming task name
            self.log_info(
                fl_ctx,
                f"[RUN_TRAINER] Received task_name='{task_name}', configured train_task_name='{self._train_task_name}'",
            )

            if self._flip_trainer is None:
                # Import the user-provided trainer module dynamically
                # The 'trainer' module should be in the Python path (job's custom folder)
                # Diagnostic logging: confirm which trainer file was imported
                import trainer as trainer_module
                from trainer import FLIP_TRAINER as UPLOADED_TRAINER

                self.log_info(fl_ctx, f"[RUN_TRAINER] Imported FLIP_TRAINER from: {trainer_module.__file__}")

                self._flip_trainer = UPLOADED_TRAINER(
                    train_task_name=self._train_task_name,
                    submit_model_task_name=self._submit_model_task_name,
                    exclude_vars=self._exclude_vars,
                    project_id=self._project_id,
                    query=self._query,
                )
                # Some user-provided trainers implement `get_num_epochs()`, others expose
                # an `_epochs` attribute. Be tolerant and fallback to a sensible default
                # if neither is present to avoid crashing the executor.
                if hasattr(self._flip_trainer, "get_num_epochs") and callable(
                    getattr(self._flip_trainer, "get_num_epochs")
                ):
                    try:
                        self._epochs = self._flip_trainer.get_num_epochs()
                    except Exception:
                        self._epochs = getattr(self._flip_trainer, "_epochs", None)
                else:
                    self._epochs = getattr(self._flip_trainer, "_epochs", None)

                if self._epochs is None:
                    # Last resort default to 1 epoch and log the condition via info.
                    self._epochs = 1

            return self._flip_trainer.execute(task_name, shareable, fl_ctx, abort_signal)
        except Exception:
            self.log_info(fl_ctx, "An exception has been caught in the FLIP_TRAINER")

            formatted_exception = secure_format_traceback()

            self.log_error(fl_ctx, formatted_exception)

            return make_reply(ReturnCode.EXECUTION_EXCEPTION, headers={"exception": formatted_exception})
