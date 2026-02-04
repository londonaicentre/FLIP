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
import traceback
from datetime import datetime
from urllib.parse import urlparse

import boto3
from flip import FLIP
from nvflare.apis.fl_component import FLComponent
from nvflare.apis.fl_context import FLContext
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_opt.pt.file_model_persistor import PTFileModelPersistor
from pt_constants import PTConstants
from utils.flip_constants import FlipConstants, FlipEvents, ModelStatus
from utils.utils import Utils


class PersistToS3AndCleanup(FLComponent):
    def __init__(self, model_id: str, persistor_id: str = AppConstants.DEFAULT_PERSISTOR_ID, flip: FLIP = FLIP()):
        """The component that is executed post training and is a part of the FLIP training model

        The PersistToS3AndCleanup workflow saves the aggregated model (once training has finished) to an S3 bucket, and
        then deletes files created as part of the run

        Args:
            model_id (str): ID of the model that the training is being performed under.
            persistor_id (str, optional): ID of the persistor component. Defaults to "persistor".

        Raises:
           ValueError:
            - when the model ID is not a valid UUID.

            FileNotFoundError: boto3 error for when the zip file does not exist.
        """

        super().__init__()
        self.model_id = model_id
        self.persistor_id = persistor_id
        self.model_persistor = None
        self.model_inventory: dict = {}
        self.model_dir: str = None
        self.bucket_name: str = None

        self.flip = flip

        if Utils.is_valid_uuid(self.model_id) is False:
            self.flip.update_status(self.model_id, ModelStatus.ERROR)
            raise ValueError(f"The model ID: {self.model_id} is not a valid UUID")

    def execute(self, fl_ctx: FLContext):
        try:
            self.log_info(fl_ctx, "Initializing PersistToS3AndCleanup")
            engine = fl_ctx.get_engine()
            if not engine:
                self.system_panic("Engine not found. PersistToS3AndCleanup exiting.", fl_ctx)
                return

            self.model_persistor: PTFileModelPersistor = engine.get_component(self.persistor_id)
            if self.model_persistor is None or not isinstance(self.model_persistor, PTFileModelPersistor):
                self.system_panic(
                    f"'persistor_id' component must be PTFileModelPersistor. But got: {type(self.model_persistor)}",
                    fl_ctx,
                )
                return

            self.log_info(fl_ctx, "Beginning PersistToS3AndCleanup")
            self.model_inventory = self.model_persistor.get_model_inventory(fl_ctx)

            if (self.model_inventory.get(PTConstants.PTFileModelName) is not None) and (
                PTConstants.PTFileModelName in self.model_inventory
            ):
                self.model_dir = self.model_inventory[PTConstants.PTFileModelName].location
                self.log_info(fl_ctx, f"Model dir: {self.model_dir}")
            else:
                self.log_warning(
                    fl_ctx,
                    "Unable to retrieve the details of the aggregated model. "
                    "Will attempt to zip everything within the final run using a manual path.",
                )

            self.fire_event(FlipEvents.RESULTS_UPLOAD_STARTED, fl_ctx)

            self.upload_results_to_s3_bucket(fl_ctx)

            self.fire_event(FlipEvents.RESULTS_UPLOAD_COMPLETED, fl_ctx)

            self.log_info(fl_ctx, "Attempting to delete the zip file containing the final aggregated run on disk...")
            self.cleanup(fl_ctx)
            self.log_info(fl_ctx, "Zip file has been deleted successfully")

            self.log_info(fl_ctx, "PersistToS3AndCleanup completed")

        except BaseException as e:
            traceback.print_exc()
            error_msg = f"Exception in PersistToS3AndCleanup control_flow: {e}"
            self.log_exception(fl_ctx, error_msg)
            raise Exception

    def upload_results_to_s3_bucket(self, fl_ctx: FLContext):
        """
        Uploads the final aggregated model and reports to an S3 bucket as a zip file.
        """
        workspace_dir = fl_ctx.get_engine().get_workspace().get_root_dir()
        run_dir = fl_ctx.get_engine().get_workspace().get_run_dir(fl_ctx.get_job_id())
        try:
            self.log_info(fl_ctx, "Attempting to upload the final aggregated model to the s3 bucket...")

            app_server_path = os.path.join(run_dir, "app_server")

            if FlipConstants.LOCAL_DEV:
                fl_global_model_filepath = os.path.join(app_server_path, PTConstants.PTFileModelName)
            else:
                fl_global_model_filepath = os.path.join(app_server_path, "model", PTConstants.PTFileModelName)

            trainer_path = os.path.join(app_server_path, "custom", "trainer.py")
            validator_path = os.path.join(app_server_path, "custom", "validator.py")

            if os.path.isfile(fl_global_model_filepath):
                self.log_info(fl_ctx, f"Found global model: {fl_global_model_filepath}")
                shutil.move(fl_global_model_filepath, run_dir)

            # TODO Why are trainer.py and validator.py being included in the zip file, and not models.py or config.json
            # This should be reviewed
            if os.path.isfile(trainer_path):
                self.log_info(fl_ctx, f"Found trainer.py: {trainer_path}")
                shutil.move(trainer_path, run_dir)

            if os.path.isfile(validator_path):
                self.log_info(fl_ctx, f"Found validator.py: {validator_path}")
                shutil.move(validator_path, run_dir)

            if os.path.isdir(run_dir):
                self.log_info(fl_ctx, f"Removing app_server directory: {app_server_path}")
                shutil.rmtree(app_server_path)

            self.log_info(fl_ctx, "Zipping the final model and the reports...")
            zip_name = datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_path = os.path.join(workspace_dir, "save", self.model_id, zip_name)
            self.log_info(fl_ctx, f"Source folder to be zipped: {run_dir}")
            shutil.make_archive(zip_path, "zip", run_dir)
            self.log_info(fl_ctx, f"Zip file created at: {zip_path}.zip")

            if not FlipConstants.LOCAL_DEV:
                self.bucket_name = FlipConstants.UPLOADED_FEDERATED_DATA_BUCKET

                self.log_info(fl_ctx, "Uploading zip file...")
                bucket_zip_path = f"{self.model_id}/{zip_name}"

                # Use boto3 to upload the zip file to the S3 bucket
                s3_client = boto3.client("s3")

                self.log_info(fl_ctx, f"Uploading zip file {zip_path} to {self.bucket_name}/{bucket_zip_path}...")

                parsed = urlparse(self.bucket_name)  # e.g. "s3://my-bucket/some/path"
                bucket = parsed.netloc  # "my-bucket"
                prefix = parsed.path.lstrip("/")  # "some/path"

                s3_client.upload_file(f"{zip_path}.zip", bucket, f"{prefix}/{bucket_zip_path}.zip")

                self.log_info(fl_ctx, "Upload .zip to the s3 bucket successful")

            else:
                self.log_info(fl_ctx, "[DEV] Skipping upload of .zip file to S3 bucket in LOCAL_DEV mode.")

        except FileNotFoundError as e:
            self.log_error(fl_ctx, f"File or directory: {e.filename} does not exist")
            self.log_error(fl_ctx, str(e))
            raise Exception
        except Exception as e:
            self.log_error(fl_ctx, "Upload to the s3 bucket failed. Attempting to cleanup")
            self.cleanup(fl_ctx)
            self.log_error(fl_ctx, str(e))
            raise Exception

    def cleanup(self, fl_ctx: FLContext):
        """
        Cleans up the workspace by deleting the transfer and save directories for the model ID.
        """
        try:
            workspace_dir = fl_ctx.get_engine().get_workspace().get_root_dir()

            transfer_job_dir = os.path.join(workspace_dir, "transfer", self.model_id)
            save_dir = os.path.join(workspace_dir, "save", self.model_id)

            for path in [save_dir, transfer_job_dir]:
                if not os.path.isdir(path):
                    continue

                if FlipConstants.LOCAL_DEV:
                    self.log_info(fl_ctx, f"[DEV] Skipping cleanup of path: {path} in LOCAL_DEV mode.")
                    continue
                else:
                    self.log_info(fl_ctx, f"Cleaning up path: {path}")
                    shutil.rmtree(path)
        except Exception as e:
            self.log_error(fl_ctx, "Cleanup step to delete the images used for training failed")
            self.log_error(fl_ctx, str(e))
            raise Exception
