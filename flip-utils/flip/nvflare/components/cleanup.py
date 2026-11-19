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
from pathlib import Path

from nvflare.apis.event_type import EventType
from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.security.logging import secure_format_traceback

from flip.constants import FlipConstants, FlipTasks
from flip.nvflare.runtime import get_flip_project_id


class CleanupImages(Executor):
    """Keeps one job's images on a net from reaching the next job, where the client mounts the net's whole folder.

    At the start of a job it deletes every other project's images, never the job's own: the Client-API trainer
    starts before the first task arrives and may already be downloading them (FLIP#1830). At the end of the run it
    deletes them all, on ABOUT_TO_END_RUN, which NVFLARE fires when a job completes, fails or is aborted. An aborted
    job's process is killed 10 s after the abort, possibly before that event, so the start-of-job wipe is what keeps
    projects apart; imaging-api's retention sweep removes what is left.
    """

    def handle_event(self, event_type: str, fl_ctx: FLContext) -> None:
        if event_type == EventType.ABOUT_TO_END_RUN:
            try:
                self._delete_images(fl_ctx)
            except Exception:
                self.log_error(fl_ctx, secure_format_traceback())

    def execute(self, task_name: str, shareable: Shareable, fl_ctx: FLContext, abort_signal: Signal) -> Shareable:
        try:
            if task_name in (FlipTasks.INIT_TRAINING, FlipTasks.INIT_TASK):
                project_id = get_flip_project_id(fl_ctx)
                if project_id is None:
                    self.log_warning(fl_ctx, "No project id in the client config; deleting every project's images")
                self._delete_images(fl_ctx, keep=project_id)
                return make_reply(ReturnCode.OK)

            if task_name in (FlipTasks.POST_VALIDATION, FlipTasks.POST_TASK):
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

    def _delete_images(self, fl_ctx: FLContext, keep: str | None = None) -> None:
        """Delete everything in the net's images folder except the project folder named ``keep``."""
        if FlipConstants.IMAGES_TRANSPORT != "mount":
            # Only a client that mounts the net's whole images folder wipes it. A job pod that mounts
            # its project's folder (read-only, FLIP#1678) or none (FLIP#1049) leaves the cache to
            # imaging-api, which keeps and bounds it.
            self.log_info(fl_ctx, "Cleanup → the image cache is imaging-api's; nothing to delete here")
            return
        if FlipConstants.LOCAL_DEV:
            self.log_info(fl_ctx, "[DEV] Cleanup → images")
            return

        net_directory = Path(FlipConstants.IMAGES_DIR, FlipConstants.NET_ID)
        if not net_directory.is_dir():
            self.log_info(fl_ctx, f"Directory {net_directory} does not exist, nothing to clean up.")
            return

        for entry in net_directory.iterdir():
            if entry.name == keep:
                continue
            self.log_info(fl_ctx, f"Deleting {entry}")
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
