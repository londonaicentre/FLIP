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
from pathlib import Path

import monai
import numpy as np
import pydicom
import torch
from data_utils import Lesion, LesionDict, get_labels_from_radiology_row, get_lesion_label, get_xray_transforms
from loss_and_metrics import compute_precision_recall_f1, get_bce_loss
from models import get_model
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.app_common.app_constant import AppConstants

from flip import FLIP
from flip.constants import ResourceType


class FLIP_VALIDATOR(Executor):
    def __init__(
        self,
        validate_task_name=AppConstants.TASK_VALIDATION,
        project_id="",
        query="",
        local_training_nvflare: bool = False,
    ):
        super(FLIP_VALIDATOR, self).__init__()

        # Logger
        self.logger = logging.getLogger(self.__class__.__name__)

        # FLIP-specific: do not modify these variables
        self._validate_task_name = validate_task_name

        self.config = {}
        working_dir = Path(__file__).parent.resolve()
        self.working_dir = working_dir
        with open(str(working_dir / "config.json")) as file:
            self.config = json.load(file)
            self._val_split = self.config["VAL_SPLIT"]
            self._test_split = self.config["TEST_SPLIT"]
            self._lesions = self.config["LESIONS"]
            if "-1" in self._lesions.keys():
                self._normal_key = self._lesions["-1"]
                del self._lesions["-1"]
            else:
                self._normal_key = "Normal"
            self._value_to_numerical = {int(i): j for i, j in self.config["value_to_numerical"].items()}
            if 0 not in self._value_to_numerical.keys() and 1 not in self._value_to_numerical.keys():
                raise ValueError("value_to_numerical must contain mappings for 0 and 1.")
            self._batch_size = self.config["BATCH_SIZE"]

        self._lesions = LesionDict(items=[Lesion(id=int(k), lesion=v) for k, v in self._lesions.items()])

        # Model creation
        self.model = get_model()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)

        # Setup the transforms
        self._test_transforms = get_xray_transforms(is_validation=True)

        # Data loading
        self.flip = FLIP()
        self.project_id = project_id
        self.query = query
        self.dataframe = self.flip.get_dataframe(self.project_id, self.query)

        # Data dict
        self.test_dict = self.get_image_and_label_list()
        self.test_dataset = monai.data.Dataset(self.test_dict, transform=self._test_transforms)

        # Get dataset and dataloader
        self.test_dataloader = monai.data.DataLoader(self.test_dataset, batch_size=self._batch_size, shuffle=False)

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
            # First, we load the labels;
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
        _, _, test_datalist = np.split(
            datalist,
            [
                int(len(datalist) * (1 - self._val_split - self._test_split)),
                int(len(datalist) * (1 - self._test_split)),
            ],
        )

        print(f"Found {len(test_datalist)} files for testing.")

        return test_datalist

    def do_validation(self, fl_ctx, weights, abort_signal):
        self.model.load_state_dict(weights)

        self.model.eval()

        metrics = {"loss": [], "f1-score": {}, "precision": {}, "recall": {}}
        for lesion_name in self._lesions.get_lesion_list():
            metrics["f1-score"][lesion_name] = []
            metrics["precision"][lesion_name] = []
            metrics["recall"][lesion_name] = []

        with torch.no_grad():
            for i, batch in enumerate(self.test_dataloader):
                if abort_signal.triggered:
                    return 0

                images = batch["image"].to(self.device)
                labels = get_lesion_label(batch, self._lesions).to(self.device)
                output = self.model(images)
                loss = get_bce_loss(output, labels).item()
                metrics["loss"].append(loss)
                output = torch.sigmoid(output)
                for pathology in self._lesions.get_lesion_list():
                    precision, recall, f1_score = compute_precision_recall_f1(
                        output, labels, pathology, lesions=self._lesions
                    )
                    metrics["precision"][pathology].append(precision)
                    metrics["recall"][pathology].append(recall)
                    metrics["f1-score"][pathology].append(f1_score)

        # Get text
        message = "testing round finished - "
        for metric, metric_values in metrics.items():
            if metric == "loss":
                message += f"{metric}: {metric_values[-1]:.4f};\t"
            else:
                for lesion_name, lesion_values in metric_values.items():
                    message += f"{metric}-{lesion_name}: {lesion_values[-1]:.4f};\t"

        message += "\n"
        print(message)
        self.log_info(fl_ctx, message)

        # Send metrics over to FLIP
        self.flip.send_metrics_value(label="TEST_LOSS", value=metrics["loss"][-1], fl_ctx=fl_ctx, round=0)
        for metric in ["f1-score", "precision", "recall"]:
            for lesion_name in self._lesions.get_lesion_list():
                self.flip.send_metrics_value(
                    label=f"{'test'.upper()}-{metric.upper()}",
                    value=metrics[metric][lesion_name][-1],
                    fl_ctx=fl_ctx,
                    round=0,
                )

        return metrics

    def execute(
        self,
        task_name: str,
        shareable: Shareable,
        fl_ctx: FLContext,
        abort_signal: Signal,
    ) -> Shareable:
        if task_name == self._validate_task_name:
            # model_owner = "?"
            dxo = from_shareable(shareable)

            # Ensure data_kind is weights.
            if not dxo.data_kind == DataKind.WEIGHTS:
                self.log_exception(
                    fl_ctx,
                    f"DXO is of type {dxo.data_kind} but expected type WEIGHTS.",
                )
                return make_reply(ReturnCode.BAD_TASK_DATA)

            # Extract weights and ensure they are tensor.
            # model_owner = shareable.get_header(AppConstants.MODEL_OWNER, "?")
            weights = {k: torch.as_tensor(v, device=self.device) for k, v in dxo.data.items()}

            # Get validation accuracy
            metrics = self.do_validation(fl_ctx, weights, abort_signal)
            if abort_signal.triggered:
                return make_reply(ReturnCode.TASK_ABORTED)

            dxo = DXO(data_kind=DataKind.METRICS, data=metrics)

            return dxo.to_shareable()

        else:
            return make_reply(ReturnCode.TASK_UNKNOWN)
            return make_reply(ReturnCode.TASK_UNKNOWN)
