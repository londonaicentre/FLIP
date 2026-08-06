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
import traceback
from pathlib import Path

from nvflare.apis.fl_component import FLComponent
from nvflare.apis.fl_context import FLContext
from nvflare.app_common.app_constant import AppConstants, DefaultCheckpointFileName
from nvflare.app_opt.pt.file_model_persistor import PTFileModelPersistor

from flip import FLIP
from flip.constants import FlipConstants, FlipEvents, PTConstants
from flip.nvflare.runtime import get_flip_model_id


class PersistToS3AndCleanup(FLComponent):
    def __init__(self, model_id: str = "", persistor_id: str = AppConstants.DEFAULT_PERSISTOR_ID, flip: FLIP = FLIP()):
        """The component that is executed post training and is a part of the FLIP training model

        The PersistToS3AndCleanup workflow saves the aggregated model (once training has finished) to an S3 bucket, and
        then deletes files created as part of the run

        Args:
            model_id (str, optional): ID of the model that the training is being performed under. When empty,
                the model ID is resolved lazily from job metadata via ``get_flip_model_id`` on first use.
            persistor_id (str, optional): ID of the persistor component. Defaults to "persistor".

        Raises:
            FileNotFoundError: boto3 error for when the zip file does not exist.
        """

        super().__init__()
        self._model_id_fallback = model_id
        self._model_id: str | None = None
        self.persistor_id: str = persistor_id
        self.model_persistor: PTFileModelPersistor | None = None
        self.model_inventory: dict = {}
        self.model_dir: str = ""
        self.bucket_name: str = ""

        self.flip = flip

    def _resolve_model_id(self, fl_ctx: FLContext) -> str:
        """Resolve model ID lazily from job metadata, falling back to the constructor arg.

        Args:
            fl_ctx (FLContext): The FL context for the current job.

        Returns:
            str: The resolved model ID.
        """
        if self._model_id is None:
            self._model_id = get_flip_model_id(fl_ctx, fallback=self._model_id_fallback)
        return self._model_id

    def execute(self, fl_ctx: FLContext) -> None:
        try:
            self.log_info(fl_ctx, "Initializing PersistToS3AndCleanup")
            engine = fl_ctx.get_engine()
            if not engine:
                self.system_panic("Engine not found. PersistToS3AndCleanup exiting.", fl_ctx)
                return

            self.model_persistor = engine.get_component(self.persistor_id)
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
            raise

    def upload_results_to_s3_bucket(self, fl_ctx: FLContext) -> None:
        """
        Uploads the final aggregated model and reports to an S3 bucket as a zip file.
        """
        run_dir = fl_ctx.get_engine().get_workspace().get_run_dir(fl_ctx.get_job_id())

        try:
            self.log_info(fl_ctx, "Attempting to upload the final aggregated model to the s3 bucket...")

            app_server_path = os.path.join(run_dir, "app_server")

            if FlipConstants.LOCAL_DEV:
                fl_global_model_filepath = os.path.join(app_server_path, PTConstants.PTFileModelName)
            else:
                fl_global_model_filepath = os.path.join(app_server_path, "model", PTConstants.PTFileModelName)

            # Move global model to run_dir
            if os.path.isfile(fl_global_model_filepath):
                self.log_info(fl_ctx, f"Found global model: {fl_global_model_filepath}")
                shutil.move(fl_global_model_filepath, run_dir)

            # Move the best global model too, when best-model selection saved one (FLIP#673). The
            # persistor writes it next to the final model on GLOBAL_BEST_MODEL_AVAILABLE (fired by
            # IntimeModelSelector). Absent the selector no file exists and none is fabricated — a
            # "best" artefact in the results must mean a selection actually happened.
            fl_best_model_filepath = os.path.join(
                os.path.dirname(fl_global_model_filepath), DefaultCheckpointFileName.BEST_GLOBAL_MODEL
            )
            if os.path.isfile(fl_best_model_filepath):
                self.log_info(fl_ctx, f"Found best global model: {fl_best_model_filepath}")
                shutil.move(fl_best_model_filepath, run_dir)

            # For certain workflows (e.g., diffusion_model), also move trainer.py and validator.py
            trainer_path = os.path.join(app_server_path, "custom", "trainer.py")
            validator_path = os.path.join(app_server_path, "custom", "validator.py")

            if os.path.isfile(trainer_path):
                self.log_info(fl_ctx, f"Found trainer.py: {trainer_path}")
                shutil.move(trainer_path, run_dir)

            if os.path.isfile(validator_path):
                self.log_info(fl_ctx, f"Found validator.py: {validator_path}")
                shutil.move(validator_path, run_dir)

            # Remove app_server directory before zipping
            if os.path.isdir(app_server_path):
                self.log_info(fl_ctx, f"Removing app_server directory: {app_server_path}")
                shutil.rmtree(app_server_path)

            # Drop the cross-site-eval model artefacts before zipping. CrossSiteModelEval persists
            # every validated model to cross_site_val/model_shareables/ purely to broadcast it to the
            # clients for scoring — e.g. the server-loaded evaluation checkpoint (~759 MiB for Ark+,
            # ~1.5 GiB for the multimodel variant), which the user already uploaded. It is an input to
            # the eval, not an output, so re-shipping it in the results zip is pure waste. The metrics
            # live in evaluation_results/evaluation_results.json (EvaluationJsonGenerator) and the raw
            # per-model/per-site shareables in cross_site_val/result_shareables/ — both retained. For
            # training jobs this directory does not exist, so the guard makes this a no-op there.
            cross_val_models_dir = os.path.join(
                run_dir, AppConstants.CROSS_VAL_DIR, AppConstants.CROSS_VAL_MODEL_DIR_NAME
            )
            if os.path.isdir(cross_val_models_dir):
                self.log_info(fl_ctx, f"Removing cross-site-eval model artefacts before upload: {cross_val_models_dir}")
                shutil.rmtree(cross_val_models_dir)

            self.flip.upload_results_to_s3(run_dir, self._resolve_model_id(fl_ctx))

        except Exception as e:
            self.log_error(fl_ctx, "Upload to the s3 bucket failed. Attempting to cleanup")
            self.cleanup(fl_ctx)
            self.log_error(fl_ctx, str(e))
            raise

    def cleanup(self, fl_ctx: FLContext) -> None:
        """
        Cleans up the workspace by deleting the transfer and save directories for the model ID.
        """
        workspace_dir = fl_ctx.get_engine().get_workspace().get_root_dir()
        model_id = self._resolve_model_id(fl_ctx)

        transfer_job_dir = os.path.join(workspace_dir, "transfer", model_id)
        save_dir = os.path.join(workspace_dir, "save", model_id)

        for path in [save_dir, transfer_job_dir]:
            if not os.path.isdir(path):
                continue
            self.flip.cleanup(Path(path))
