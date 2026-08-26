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

from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.security.logging import secure_format_traceback

from flip.constants import FlipConstants, FlipTasks


class CleanupJobDir(Executor):
    def __init__(self):
        """Deletes the client-side NVFLARE job workspace on the ``post_validation`` broadcast.

        The job directory holds the deployed app code, logs and anything training wrote to
        its working directory; without this it accumulates per job for the lifetime of the
        fl-client container. The broadcast is end-of-run in the single-phase job types;
        the multi-phase diffusion job fires it at the end of *each* phase, so the workspace
        is also cleared mid-run between the AE and DM phases (as it was under the retired
        executor).

        This replaces the retired ``CleanupImages`` executor, which additionally wiped the
        net's imaging directory at job start and end. Imaging retention is now owned
        trust-side by imaging-api's TTL sweeper (FLIP#1050), so cached studies survive
        across jobs and consecutive runs of the same project reuse them instead of
        re-downloading the cohort.
        """

        super().__init__()

    def execute(self, task_name: str, shareable: Shareable, fl_ctx: FLContext, abort_signal: Signal) -> Shareable:
        try:
            if task_name == FlipTasks.POST_VALIDATION:
                job_dir = os.path.join(os.getcwd(), fl_ctx.get_job_id())

                if os.path.isdir(job_dir):
                    if not FlipConstants.LOCAL_DEV:
                        self.log_info(fl_ctx, f"Deleting job directory {job_dir}")
                        shutil.rmtree(job_dir)
                    else:
                        self.log_info(fl_ctx, f"[DEV] Cleanup → job directory {job_dir}")

                return make_reply(ReturnCode.OK)

            return make_reply(ReturnCode.TASK_UNKNOWN)
        except Exception:
            self.log_info(fl_ctx, "An exception has been caught during cleanup")

            formatted_exception = secure_format_traceback()

            self.log_error(fl_ctx, formatted_exception)

            return make_reply(ReturnCode.EXECUTION_EXCEPTION, headers={"exception": formatted_exception})
