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

import json
import logging
import os.path
from pathlib import Path

import nibabel as nib
import numpy as np
import torch
from flip import FLIP
from flip.constants import PTConstants, ResourceType
from flip.nvflare.metrics import send_metrics_value
from flip.utils import get_model_weights_diff
from models import get_model
from monai.data import DataLoader, Dataset, decollate_batch
from monai.losses import DiceCELoss
from monai.metrics import DiceMetric
from monai.transforms import Activations, AsDiscrete, Compose
from nvflare.apis.dxo import DataKind, from_shareable
from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReservedKey, ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.app_common.abstract.model import make_model_learnable, model_learnable_to_dxo
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_opt.pt.model_persistence_format_manager import PTModelPersistenceFormatManager
from tqdm import tqdm
from transforms import get_sliding_window_inferer, get_train_transforms, get_val_transforms


class FLIP_BASE(Executor):
    """
    Shares common functionality for both trainer and validator (e.g. get_image_and_label_list)
    """

    def __init__(self):
        super().__init__()

        # --- Core FLIP object ---
        self.flip = FLIP()

        # --- Logging setup ---
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(logging.INFO)

        # --- Config ---
        self.config = {}
        with open(str(Path(__file__).parent.resolve() / "config.json")) as file:
            self.config = json.load(file)
            self._epochs = self.config["LOCAL_ROUNDS"]
            self._lr = self.config["LEARNING_RATE"]
            self._val_split = self.config["VAL_SPLIT"]

        # --- Device setup ---
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        # --- Common transforms (shared utilities) ---
        self._train_transforms = get_train_transforms()
        self._val_transforms = get_val_transforms()
        self.inferer = get_sliding_window_inferer(self.device)

        # --- Model setup ---
        self.model = get_model()
        self.model.to(self.device)

        # --- Common MONAI postprocessing and metrics ---
        self.post_sigmoid = Activations(softmax=True)
        self.post_pred = AsDiscrete(argmax=True, to_onehot=2)
        self.post_pred_gt = AsDiscrete(to_onehot=2)
        self.dice_acc = DiceMetric(include_background=False, reduction="mean")

        # These will be set in the child classes
        self.project_id = ""
        self.query = ""
        self.dataframe = None

    def get_image_and_label_list(self):
        """Returns a list of dicts, each dict containing the path to an image and its corresponding label."""

        datalist = []
        images_found = 0

        # loop over each accession id in the train set
        for accession_id in tqdm(self.dataframe["accession_id"], desc="Preparing dataset", unit="accession"):
            try:
                accession_folder_path = self.flip.get_by_accession_number(
                    self.project_id,
                    accession_id,
                    resource_type=[
                        ResourceType.NIFTI,
                        # ResourceType.SEGMENTATION,
                    ],
                )
            except Exception as err:
                self.logger.info("⚠️ Could not fetch images for accession_id=%s: %s", accession_id, err)
                continue

            # get all images in the accession folder that match the pattern "input_*.nii.gz"
            all_images = list(accession_folder_path.rglob("input_*.nii.gz"))
            images_found += len(all_images)

            for img in all_images:
                # for each image, find the corresponding segmentation mask
                seg = str(img).replace("/input_", "/label_")

                if not Path(seg).exists():
                    self.logger.info("⚠️ No matching segmentation for %s", img.name)
                    continue

                try:
                    img_header = nib.load(str(img))
                    seg_header = nib.load(seg)
                except nib.filebasedimages.ImageFileError as err:
                    self.logger.info("⚠️ Invalid image pair for %s: %s", img.name, err)
                    continue

                # Some QC checks to ensure the image and segmentation are valid and match
                # check is 3D and at least 128x128x128 in size and seg is the same shape as the image
                if len(img_header.shape) != 3:
                    self.logger.info("⚠️ Skipping non-3D image %s", img.name)
                    continue

                if img_header.shape != seg_header.shape:
                    self.logger.info(
                        "⚠️ Shape mismatch for %s: image=%s label=%s",
                        img.name,
                        img_header.shape,
                        seg_header.shape,
                    )
                    continue

                # defines keys for image and segmentation
                datalist.append({"image": str(img), "label": seg})

        self.logger.info("Dataset ready: %d image/label pairs", len(datalist))

        # Fail here rather than letting an empty dataset surface downstream as torch's opaque
        # "num_samples should be a positive integer value, but got num_samples=0".
        if not datalist:
            raise RuntimeError(
                f"No image/label pairs found: {images_found} input_*.nii.gz image(s) across "
                f"{len(self.dataframe['accession_id'])} accession(s), none with a matching label_*.nii.gz. "
                "Supervised training needs a label beside each image in XNAT — was the data-enrichment step "
                "run, and did it run after DICOM-to-NIfTI conversion? See the Data Enrichment user guide."
            )

        # split into the training and testing data
        train_datalist, val_datalist = np.split(datalist, [int((1 - self._val_split) * len(datalist))])

        return train_datalist, val_datalist

    def get_train_datalist(self):
        """Returns a list of dicts, each dict containing the path to an image and its corresponding label."""
        train_datalist, _ = self.get_image_and_label_list()
        self.logger.info("Found %d files in train.", len(train_datalist))
        return train_datalist

    def get_val_datalist(self):
        """Returns a list of dicts, each dict containing the path to an image and its corresponding label."""
        _, val_datalist = self.get_image_and_label_list()
        self.logger.info("Found %d files in validation.", len(val_datalist))
        return val_datalist


class FLIP_TRAINER(FLIP_BASE):
    def __init__(
        self,
        train_task_name=AppConstants.TASK_TRAIN,
        submit_model_task_name=AppConstants.TASK_SUBMIT_MODEL,
        exclude_vars=None,
        project_id="",
        query="",
    ):
        """This CT Spleen  Trainer handles train and submit_model tasks. During train_task, it trains a
        3D Unet on paired CT images and segmentation labels. For submit_model task, it sends the locally trained model
        (if present) to the server.

        Args:
            train_task_name (str, optional): Task name for train task. Defaults to "train".
            submit_model_task_name (str, optional): Task name for submit model. Defaults to "submit_model".
            exclude_vars (list): List of variables to exclude during model loading.
        """
        super(FLIP_TRAINER, self).__init__()

        self._train_task_name = train_task_name
        self._submit_model_task_name = submit_model_task_name
        self._exclude_vars = exclude_vars

        self.loss = DiceCELoss(
            to_onehot_y=True, softmax=True, squared_pred=False, batch=True, lambda_ce=0.2, lambda_dice=0.8
        )
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self._lr)

        self.validation_step = 1

        # Setup the dataset
        self.project_id = project_id
        self.query = query
        self.dataframe = self.flip.get_dataframe(self.project_id, self.query)

        # Setup the persistence manager to save PT model.
        # The default training configuration is used by persistence manager
        # in case no initial model is found.
        self._default_train_conf = {"train": {"model": type(self.model).__name__}}
        self.persistence_manager = PTModelPersistenceFormatManager(
            data=self.model.state_dict(), default_train_conf=self._default_train_conf
        )

    def get_num_epochs(self):
        """Returns the number of epochs for training."""
        return self._epochs

    def local_train(self, fl_ctx: FLContext, weights, abort_signal, global_round):
        # Set the model weights
        self.model.load_state_dict(state_dict=weights)

        # Metrics
        last_val_loss = 0.0
        last_val_dice = 0.0

        # Basic training
        self.model.train()
        self.log_info(fl_ctx, f"Starting local train on device {self.device}")
        for epoch in range(self._epochs):
            running_loss = 0.0
            num_images = 0
            for i, batch in enumerate(self._train_loader):
                if abort_signal.triggered:
                    # If abort_signal is triggered, we simply return.
                    # The outside function will check it again and decide steps to take.
                    return

                images, labels = (
                    batch["image"].to(self.device),
                    batch["label"].to(self.device),
                )
                self.optimizer.zero_grad()

                predictions = self.model(images)
                cost = self.loss(predictions, labels)
                cost.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                self.optimizer.step()
                batch_size = images.shape[0]
                num_images += batch_size
                running_loss += cost.cpu().detach().numpy() * batch_size

                self.log_info(
                    fl_ctx,
                    f"Epoch: {epoch + 1}, Iteration: {i + 1}, Loss: {cost.cpu().item()}",
                )

            average_loss = running_loss / num_images

            self.log_info(fl_ctx, f"Epoch: {epoch + 1}, Finished, Average loss: {average_loss}")

            # Per-epoch point: plot on an "epoch" axis at the cumulative epoch count; the FL global
            # round is recorded alongside as provenance.
            cumulative_epoch = global_round * (self._epochs) + epoch + 1
            send_metrics_value(
                label="TRAIN_LOSS",
                value=average_loss,
                fl_ctx=fl_ctx,
                x_value=cumulative_epoch,
                x_label="epoch",
                flip=self.flip,
            )

            # Validation loop
            if epoch % self.validation_step == 0:
                val_dice = []
                self.model.eval()

                with torch.no_grad():
                    running_loss = 0.0
                    for i, batch in enumerate(self._val_loader):
                        images, labels = (
                            batch["image"].to(self.device),
                            batch["label"].to(self.device),
                        )

                        # For validation, we use the Sliding Window Inferer
                        predictions = self.inferer(images, self.model)

                        # predictions = self.model(images)
                        cost = self.loss(predictions, labels)

                        self.dice_acc.reset()
                        # We need to decollate predictions and labels
                        predictions = decollate_batch(predictions)
                        predictions = torch.stack([self.post_pred(i) for i in predictions], 0)
                        labels = torch.stack([self.post_pred_gt(i) for i in labels], 0)
                        self.dice_acc(y_pred=predictions, y=labels)
                        acc = self.dice_acc.aggregate()
                        val_dice.append(acc.item())
                        running_loss += cost.cpu().item()

                last_val_loss = running_loss / len(self._val_loader)
                last_val_dice = np.mean(val_dice)

                self.log_info(
                    fl_ctx=fl_ctx,
                    msg=f"Validation - Epoch: {epoch + 1}, Loss: {last_val_loss}\nMean Dice: {last_val_dice}",
                )

            # Report the latest validation metrics every epoch (non-validation epochs re-send the
            # previous values) at this epoch's coordinate.
            send_metrics_value(
                label="VAL_LOSS",
                value=last_val_loss,
                fl_ctx=fl_ctx,
                x_value=cumulative_epoch,
                x_label="epoch",
                flip=self.flip,
            )
            send_metrics_value(
                label="VAL_DICE",
                value=last_val_dice,
                fl_ctx=fl_ctx,
                x_value=cumulative_epoch,
                x_label="epoch",
                flip=self.flip,
            )

            self.model.train()

    def execute(
        self,
        task_name: str,
        shareable: Shareable,
        fl_ctx: FLContext,
        abort_signal: Signal,
    ) -> Shareable:
        global_round = shareable.get_header(AppConstants.CURRENT_ROUND)

        train_dict, val_dict = self.get_image_and_label_list()

        self._train_dataset = Dataset(train_dict, transform=Compose(self._train_transforms))
        self._val_dataset = Dataset(val_dict, transform=Compose(self._val_transforms))
        self._train_loader = DataLoader(self._train_dataset, batch_size=3, shuffle=True, num_workers=1)
        self._val_loader = DataLoader(self._val_dataset, batch_size=1, shuffle=False, num_workers=1)
        self._n_iterations = len(self._train_loader)

        if task_name == self._train_task_name:
            # Get model weights
            dxo = from_shareable(shareable)

            # Ensure data kind is weights.
            if not dxo.data_kind == DataKind.WEIGHTS:
                self.log_error(
                    fl_ctx,
                    f"data_kind expected WEIGHTS but got {dxo.data_kind} instead.",
                )
                return make_reply(ReturnCode.BAD_TASK_DATA)

            # Convert weights to tensor. Run training
            torch_weights = {k: torch.as_tensor(v) for k, v in dxo.data.items()}
            self.local_train(fl_ctx, torch_weights, abort_signal, global_round)

            # Check the abort_signal after training.
            # local_train returns early if abort_signal is triggered.
            if abort_signal.triggered:
                return make_reply(ReturnCode.TASK_ABORTED)

            # Save the local model after training.
            self.save_local_model(fl_ctx)

            # Get the new state dict and send as weights
            new_weights = self.model.state_dict()
            outgoing_dxo = get_model_weights_diff(dxo.data, new_weights, self._n_iterations)
            return outgoing_dxo.to_shareable()

        elif task_name == self._submit_model_task_name:
            # Load local model
            ml = self.load_local_model(fl_ctx)

            # Get the model parameters and create dxo from it
            dxo = model_learnable_to_dxo(ml)
            return dxo.to_shareable()
        else:
            return make_reply(ReturnCode.TASK_UNKNOWN)

    def save_local_model(self, fl_ctx: FLContext):
        run_dir = fl_ctx.get_engine().get_workspace().get_run_dir(fl_ctx.get_prop(ReservedKey.RUN_NUM))
        models_dir = os.path.join(run_dir, PTConstants.PTModelsDir)
        if not os.path.exists(models_dir):
            os.makedirs(models_dir)
        model_path = os.path.join(models_dir, PTConstants.PTLocalModelName)

        ml = make_model_learnable(self.model.state_dict(), {})
        self.persistence_manager.update(ml)
        torch.save(self.persistence_manager.to_persistence_dict(), model_path)

    def load_local_model(self, fl_ctx: FLContext):
        run_dir = fl_ctx.get_engine().get_workspace().get_run_dir(fl_ctx.get_prop(ReservedKey.RUN_NUM))
        models_dir = os.path.join(run_dir, PTConstants.PTModelsDir)
        if not os.path.exists(models_dir):
            return None
        model_path = os.path.join(models_dir, PTConstants.PTLocalModelName)

        self.persistence_manager = PTModelPersistenceFormatManager(
            data=torch.load(model_path), default_train_conf=self._default_train_conf
        )
        ml = self.persistence_manager.to_model_learnable(exclude_vars=self._exclude_vars)
        return ml
