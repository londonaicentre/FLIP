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

import json
import logging
import os.path
from pathlib import Path

import numpy as np
import pydicom
import torch
from data_utils import Lesion, LesionDict, get_labels_from_radiology_row, get_lesion_label, get_xray_transforms
from flip import FLIP
from loss_and_metrics import compute_precision_recall_f1, get_bce_loss
from models import get_model
from monai.data import DataLoader, Dataset
from nvflare.apis.dxo import DataKind, from_shareable
from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReservedKey, ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.app_common.abstract.model import (
    make_model_learnable,
    model_learnable_to_dxo,
)
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_opt.pt.model_persistence_format_manager import PTModelPersistenceFormatManager
from pt_constants import PTConstants
from utils.flip_constants import ResourceType
from utils.model_weights_handling import get_model_weights_diff


class FLIP_TRAINER(Executor):
    def __init__(
        self,
        train_task_name=AppConstants.TASK_TRAIN,
        submit_model_task_name=AppConstants.TASK_SUBMIT_MODEL,
        exclude_vars=None,
        project_id="",
        query="",
    ):
        """Trainer for FLIP-based X-ray image classification.

        Args:
            train_task_name (str, optional): Task name for train task. Defaults to "train".
            submit_model_task_name (str, optional): Task name for submit model. Defaults to "submit_model".
            exclude_vars (list): List of variables to exclude during model loading.
        """
        super(FLIP_TRAINER, self).__init__()

        self._train_task_name = train_task_name
        self._submit_model_task_name = submit_model_task_name
        self._exclude_vars = exclude_vars

        # Logger
        self.logger = logging.getLogger(self.__class__.__name__)

        # Load the config
        self.config = {}
        working_dir = Path(__file__).parent.resolve()
        with open(str(working_dir / "config.json")) as file:
            self.config = json.load(file)
            self._epochs = self.config["LOCAL_ROUNDS"]
            self._lr_start = self.config["LR_START"]
            self._lr_end = self.config["LR_END"]
            self._val_split = self.config["VAL_SPLIT"]
            self._test_split = self.config["TEST_SPLIT"]
            self._lesions = self.config["LESIONS"]
            self._value_to_numerical = {int(i): j for i, j in self.config["value_to_numerical"].items()}
            if 0 not in self._value_to_numerical.keys() and 1 not in self._value_to_numerical.keys():
                raise ValueError("value_to_numerical must contain mappings for 0 and 1.")
            if "-1" in self._lesions.keys():
                self._normal_key = self._lesions["-1"]
                del self._lesions["-1"]
            else:
                self._normal_key = "Normal"
            self._batch_size = self.config["BATCH_SIZE"]
            self.validate_every = self.config["VALIDATE_EVERY"] if "VALIDATE_EVERY" in self.config.keys() else 1

        self._lesions = LesionDict(items=[Lesion(id=int(k), lesion=v) for k, v in self._lesions.items()])

        # Setup the model
        self.model = get_model()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)

        # Setup the transforms

        self._train_transforms = get_xray_transforms()
        self._val_transforms = get_xray_transforms(is_validation=True)

        # Setup the training dataset
        self.flip = FLIP()
        self.project_id = project_id
        self.query = query
        self.dataframe = self.flip.get_dataframe(self.project_id, self.query)
        if "accession_id" not in self.dataframe.columns:
            raise ValueError("The dataframe must contain 'accession_id' column.")
        self.train_dict, self.val_dict = self.get_image_and_label_list()

        # Setup the dataset
        self.training_dataset = Dataset(self.train_dict, transform=self._train_transforms)
        self.training_dataloader = DataLoader(self.training_dataset, batch_size=self._batch_size, shuffle=True)
        self.validation_dataset = Dataset(self.val_dict, transform=self._val_transforms)
        self.validation_dataloader = DataLoader(self.validation_dataset, batch_size=self._batch_size, shuffle=False)

        # Define optimizer
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self._lr_start)
        gamma_lr = (self._lr_end / self._lr_start) ** (1 / self._epochs)
        self.scheduler = torch.optim.lr_scheduler.ExponentialLR(self.optimizer, gamma=gamma_lr)

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

    def get_image_and_label_list(self):
        """
        Returns a list of dictionaries containing a field "image" and a fields corresponding to each lesion with its
        label value.

        Args:
            dataframe (_type_): dataframe output by FLIP, which has to contain accession_id and
                columns for each of the lesions.

        Returns:
            _type_: list of dictionaries for data loading.
        """
        datalist = []

        # loop over each accession id in the train set
        for _, row in self.dataframe.iterrows():
            accession_id = row["accession_id"]
            # First, we load the radiology note; format should be: [project] - [lesion1,lesion2,lesion3_lesion3]
            pathology_dict = get_labels_from_radiology_row(
                row, self._lesions, self._value_to_numerical, self._normal_key
            )

            try:
                accession_folder_path = self.flip.get_by_accession_number(
                    self.project_id,
                    accession_id,
                    resource_type=[
                        ResourceType.DICOM,
                    ],
                )
            except Exception as err:
                print(f"Could not get image data folder path for {accession_id}: {err}")
                continue

            all_images = list(accession_folder_path.rglob("*.dcm"))
            this_accession_matches = 0
            print(f"Total base count found for accession_id {accession_id}: {len(all_images)}")

            for img in all_images:
                try:
                    _ = pydicom.dcmread(str(img))
                except Exception as e:
                    print(f"Problem loading header of base image {str(img)}.")
                    print(f"{e=}")
                    print(f"{type(e)=}")
                    print(f"{e.args=}")
                    continue

                # defines keys for image and segmentation
                item_ = {"image": str(img)}
                item_.update(pathology_dict)
                datalist.append(item_)
                this_accession_matches += 1

            print(f"Added {this_accession_matches} image / label pairs for {accession_id}.")

        print(f"Found {len(datalist)} files in total.")

        # split into the training and testing data
        train_datalist, val_datalist, test_datalist = np.split(
            datalist,
            [
                int(len(datalist) * (1 - self._val_split - self._test_split)),
                int(len(datalist) * (1 - self._test_split)),
            ],
        )

        print(
            f"Found {len(train_datalist)} files for training, {len(val_datalist)} files for validation and "
            f"{len(test_datalist)} files for testing."
        )

        return train_datalist, val_datalist

    def local_train(self, fl_ctx: FLContext, weights, abort_signal, global_round):
        # Set the model weights
        self.model.load_state_dict(state_dict=weights)

        # Basic training
        self.model.train()
        print(f"Starting local train on device {self.device}")
        training_metrics = {"loss": {"train": [], "val": []}, "f1-score": {}, "precision": {}, "recall": {}}
        for lesion_name in self._lesions.get_lesion_list():
            training_metrics["f1-score"][lesion_name] = {"train": [], "val": []}
            training_metrics["precision"][lesion_name] = {"train": [], "val": []}
            training_metrics["recall"][lesion_name] = {"train": [], "val": []}

        self._n_iterations = 0
        for epoch in range(self._epochs):
            training_metrics_ = {"loss": {"train": [], "val": []}, "f1-score": {}, "precision": {}, "recall": {}}
            for lesion_name in self._lesions.get_lesion_list():
                training_metrics_["f1-score"][lesion_name] = {"train": [], "val": []}
                training_metrics_["precision"][lesion_name] = {"train": [], "val": []}
                training_metrics_["recall"][lesion_name] = {"train": [], "val": []}

            for i, batch in enumerate(self.training_dataloader):
                if abort_signal.triggered:
                    # If abort_signal is triggered, we simply return.
                    # The outside function will check it again and decide steps to take.
                    return

                images = batch["image"].to(self.device)
                labels = get_lesion_label(batch, self._lesions).to(self.device)
                self.optimizer.zero_grad()
                output = self.model(images)
                loss = get_bce_loss(output, labels)
                loss.backward()
                self.optimizer.step()
                training_metrics_["loss"]["train"].append(loss.item())
                output = torch.sigmoid(output)
                for pathology in self._lesions.get_lesion_list():
                    precision, recall, f1_score = compute_precision_recall_f1(
                        output, labels, pathology, lesions=self._lesions
                    )
                    training_metrics_["precision"][pathology]["train"].append(precision)
                    training_metrics_["recall"][pathology]["train"].append(recall)
                    training_metrics_["f1-score"][pathology]["train"].append(f1_score)
                self._n_iterations += 1
            if epoch % self.validate_every == 0:
                self.model.eval()
                for i, batch in enumerate(self.validation_dataloader):
                    if abort_signal.triggered:
                        # If abort_signal is triggered, we simply return.
                        # The outside function will check it again and decide steps to take.
                        return

                    images = batch["image"].to(self.device)
                    labels = get_lesion_label(batch, self._lesions).to(self.device)
                    output = self.model(images)
                    loss = get_bce_loss(output, labels).item()
                    training_metrics_["loss"]["val"].append(loss)
                    output = torch.sigmoid(output)
                    for pathology in self._lesions.get_lesion_list():
                        precision, recall, f1_score = compute_precision_recall_f1(
                            output, labels, pathology, lesions=self._lesions
                        )
                        training_metrics_["precision"][pathology]["val"].append(precision)
                        training_metrics_["recall"][pathology]["val"].append(recall)
                        training_metrics_["f1-score"][pathology]["val"].append(f1_score)
                self.model.train()

            self.scheduler.step()

            # Aggregate metrics:

            for metric, metric_dump in training_metrics_.items():
                if metric == "loss":
                    training_metrics["loss"]["train"].append(np.mean(metric_dump["train"]))
                    if epoch % self.validate_every == 0:
                        training_metrics["loss"]["val"].append(np.mean(metric_dump["val"]))
                    else:
                        if len(training_metrics_["loss"]["val"]) == 0:
                            training_metrics["loss"]["val"].append(0)
                        else:
                            training_metrics["loss"]["val"].append(training_metrics["loss"]["val"][-1])
                else:
                    for lesion_name in self._lesions.get_lesion_list():
                        training_metrics[metric][lesion_name]["train"].append(
                            np.mean(metric_dump[lesion_name]["train"])
                        )
                        if epoch % self.validate_every == 0:
                            training_metrics[metric][lesion_name]["val"].append(
                                np.mean(metric_dump[lesion_name]["val"])
                            )
                        else:
                            if len(training_metrics[metric][lesion_name]["val"]) == 0:
                                training_metrics[metric][lesion_name]["val"].append(0)
                            else:
                                training_metrics[metric][lesion_name]["val"].append(
                                    training_metrics[metric][lesion_name]["val"][-1]
                                )

            # Get text
            message = f"epoch {epoch + 1}/{self.get_num_epochs()} - "
            for metric, metric_values in training_metrics.items():
                if metric == "loss":
                    message += (
                        f"{metric}: train={metric_values['train'][-1]:.4f}, val={metric_values['val'][-1]:.4f};\t"
                    )
                else:
                    for lesion_name, lesion_values in metric_values.items():
                        lvt = lesion_values["train"][-1]
                        lvv = lesion_values["val"][-1]
                        message += f"{metric}-{lesion_name}: train={lvt:.4f}, val={lvv:.4f};\t"

            message += "\n"
            print(message)
            self.log_info(fl_ctx, message)

            # Send metrics over to FLIP
            round = global_round * (self._epochs) + epoch + 1
            self.flip.send_metrics_value(
                label="TRAIN_LOSS", round=round, value=training_metrics["loss"]["train"][-1], fl_ctx=fl_ctx
            )
            self.flip.send_metrics_value(
                label="VAL_LOSS", round=round, value=training_metrics["loss"]["val"][-1], fl_ctx=fl_ctx
            )

            for metric in ["f1-score", "precision", "recall"]:
                for lesion_name in self._lesions.get_lesion_list():
                    self.flip.send_metrics_value(
                        label=f"{'train'.upper()}-{metric.upper()}",
                        round=round,
                        value=training_metrics[metric][lesion_name]["train"][-1],
                        fl_ctx=fl_ctx,
                    )
                    self.flip.send_metrics_value(
                        label=f"{'val'.upper()}-{metric.upper()}",
                        round=round,
                        value=training_metrics[metric][lesion_name]["val"][-1],
                        fl_ctx=fl_ctx,
                    )

    def execute(
        self,
        task_name: str,
        shareable: Shareable,
        fl_ctx: FLContext,
        abort_signal: Signal,
    ) -> Shareable:
        if task_name == self._train_task_name:
            global_round = shareable.get_header(AppConstants.CURRENT_ROUND)

            # Get model weights
            dxo = from_shareable(shareable)

            # Ensure data kind is weights.
            if not dxo.data_kind == DataKind.WEIGHTS:
                self.log_error(
                    fl_ctx,
                    f"data_kind expected WEIGHTS but got {dxo.data_kind} instead.",
                )
                return make_reply(ReturnCode.BAD_TASK_DATA)

            # Convert weights to tensor../ Run training
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
