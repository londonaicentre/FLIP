# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Server-side task-result reporting for client execution failures."""

from nvflare.apis.filter import Filter
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable
from nvflare.security.logging import secure_format_exception

from flip import FLIP
from flip.nvflare.runtime import get_flip_model_id


class ClientExceptionReporter(Filter):
    """Report client task failures to the FLIP hub without changing the result.

    The model ID is job-scoped data and is therefore resolved exclusively from
    ``meta.json['custom_props']['model_id']``. The sending client is obtained
    from NVFLARE's peer context, which is populated before server result filters
    run.
    """

    def __init__(self) -> None:
        super().__init__()
        self.flip = FLIP()

    def process(self, shareable: Shareable, fl_ctx: FLContext) -> Shareable:
        rc = shareable.get_return_code()
        if rc not in (ReturnCode.EXECUTION_EXCEPTION, ReturnCode.TASK_UNKNOWN):
            return shareable

        formatted_exception = shareable.get_header("exception")
        if not formatted_exception:
            return shareable

        try:
            peer_ctx = fl_ctx.get_peer_context()
            if not isinstance(peer_ctx, FLContext):
                self.log_error(fl_ctx, "Cannot report client exception: peer context is unavailable.")
                return shareable

            self.flip.send_handled_exception(
                formatted_exception=formatted_exception,
                client_name=peer_ctx.get_identity_name(),
                model_id=get_flip_model_id(fl_ctx),
            )
        except Exception as e:
            # Reporting is observational. It must not turn the client's original
            # failure into TASK_RESULT_FILTER_ERROR or hide its return code.
            self.log_error(fl_ctx, f"Failed to report client exception: {secure_format_exception(e)}")

        return shareable
