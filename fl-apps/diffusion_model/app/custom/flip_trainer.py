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

from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.app_common.app_constant import AppConstants
from nvflare.security.logging import secure_format_traceback
from trainer import FLIP_TRAINER as UPLOADED_TRAINER


class RUN_TRAINER(Executor):
    def __init__(
        self,
        train_task_name=AppConstants.TASK_TRAIN,
        submit_model_task_name=AppConstants.TASK_SUBMIT_MODEL,
        exclude_vars=None,
        project_id="",
        query="",
    ):
        """Executes the uploaded trainer and handles any errors.

        Args:
            train_task_name (str, optional): Task name for train task. Defaults to "train".
            submit_model_task_name (str, optional): Task name for submit model. Defaults to "submit_model".
            exclude_vars (list): List of variables to exclude during model loading.
            project_id (str, optional): The ID of the project the model belongs to.
            query (str, optional): The cohort query that is associated with the project.
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
        try:
            if self._flip_trainer is None:
                self._flip_trainer = UPLOADED_TRAINER(
                    train_task_name=self._train_task_name,
                    submit_model_task_name=self._submit_model_task_name,
                    exclude_vars=self._exclude_vars,
                    project_id=self._project_id,
                    query=self._query,
                )

            return self._flip_trainer.execute(task_name, shareable, fl_ctx, abort_signal)
        except Exception:
            self.log_info(fl_ctx, "An exception has been caught in the FLIP_TRAINER")

            formatted_exception = secure_format_traceback()

            self.log_error(fl_ctx, formatted_exception)

            return make_reply(ReturnCode.EXECUTION_EXCEPTION, headers={"exception": formatted_exception})
