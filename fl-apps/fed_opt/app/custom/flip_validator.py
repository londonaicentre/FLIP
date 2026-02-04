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
from validator import FLIP_VALIDATOR as UPLOADED_VALIDATOR


class RUN_VALIDATOR(Executor):
    """Executes the uploaded validator and handles any errors."""

    def __init__(self, validate_task_name=AppConstants.TASK_VALIDATION, project_id="", query=""):
        super(RUN_VALIDATOR, self).__init__()

        self._validate_task_name = validate_task_name
        self._project_id = project_id
        self._query = query
        self._validator = None

    def execute(
        self,
        task_name: str,
        shareable: Shareable,
        fl_ctx: FLContext,
        abort_signal: Signal,
    ) -> Shareable:
        try:
            if self._validator is None:
                self._validator = UPLOADED_VALIDATOR(
                    validate_task_name=AppConstants.TASK_VALIDATION,
                    project_id=self._project_id,
                    query=self._query,
                )

            return self._validator.execute(task_name, shareable, fl_ctx, abort_signal)
        except Exception:
            self.log_info(fl_ctx, "An exception has been caught in the FLIP_VALIDATOR")

            formatted_exception = secure_format_traceback()

            self.log_error(fl_ctx, formatted_exception)

            return make_reply(ReturnCode.EXECUTION_EXCEPTION, headers={"exception": formatted_exception})
