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
from pathlib import Path

from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.security.logging import secure_format_traceback
from utils.flip_constants import FlipConstants, FlipTasks


class CleanupImages(Executor):
    def __init__(self):
        """CleanupImages takes place at the start and end of the run.
        All the images used for the training are deleted to prevent the build-up of unnecessary
        files on the storage space. Executing at the start of a run ensures that any training code
        is executed with a clean slate.

        Args:

        Raises:
        """

        super().__init__()

    def execute(self, task_name: str, shareable: Shareable, fl_ctx: FLContext, abort_signal: Signal):
        try:
            if task_name == FlipTasks.POST_VALIDATION:
                cwd = os.getcwd()
                job_dir = os.path.join(cwd, fl_ctx.get_job_id())

                if os.path.isdir(job_dir):
                    if not FlipConstants.LOCAL_DEV:
                        self.log_info(fl_ctx, f"Deleting job directory {job_dir}")
                        shutil.rmtree(job_dir)
                    else:
                        self.log_info(fl_ctx, f"[DEV] Running in local dev mode, skipping deletion of {job_dir}")

            if task_name == FlipTasks.INIT_TRAINING or task_name == FlipTasks.POST_VALIDATION:
                if not FlipConstants.LOCAL_DEV:
                    net_directory = os.path.join(FlipConstants.IMAGES_DIR, FlipConstants.NET_ID)

                    size_in_bytes = sum(f.stat().st_size for f in Path(net_directory).glob("**/*") if f.is_file())
                    size_in_gb = round(size_in_bytes / pow(1024, 3), 2)

                    self.log_info(
                        fl_ctx,
                        f"Attempting to delete the images stored under: {net_directory} - Total {size_in_gb}gb",
                    )

                    if not os.path.exists(net_directory):
                        self.log_info(
                            fl_ctx,
                            f"Directory {net_directory} does not exist, nothing to clean up.",
                        )
                        return make_reply(ReturnCode.OK)

                    # Delete all files and directories in the net_directory
                    for filename in os.listdir(net_directory):
                        file_path = os.path.join(net_directory, filename)

                        if os.path.isfile(file_path) or os.path.islink(file_path):
                            self.log_info(fl_ctx, f"Deleting file {file_path}")
                            os.unlink(file_path)
                        elif os.path.isdir(file_path):
                            self.log_info(fl_ctx, f"Deleting directory {file_path}")
                            shutil.rmtree(file_path)

                    self.log_info(
                        fl_ctx,
                        "Cleanup executed successfully, images and job folder have been deleted.",
                    )

                    return make_reply(ReturnCode.OK)
                else:
                    self.log_info(fl_ctx, "[DEV] Running in local dev mode, skipping cleanup of images.")
                    return make_reply(ReturnCode.OK)

            return make_reply(ReturnCode.TASK_UNKNOWN)
        except Exception:
            self.log_info(fl_ctx, "An exception has been caught during cleanup")

            formatted_exception = secure_format_traceback()

            self.log_error(fl_ctx, formatted_exception)

            return make_reply(ReturnCode.EXECUTION_EXCEPTION, headers={"exception": formatted_exception})
