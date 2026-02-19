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

# Description: This file contains the FLIP_EVALUATOR class which is responsible for validating the model.
import json
from pathlib import Path

import nibabel as nib
import numpy as np
import torch
from models import model_paths
from monai.data import DataLoader, Dataset, decollate_batch
from monai.metrics import DiceMetric
from monai.networks.utils import one_hot
from monai.transforms import AsDiscrete
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from transforms import get_eval_transforms, get_sliding_window_inferer

from flip import FLIP
from flip.constants import PTConstants, ResourceType


class FLIP_EVALUATOR(Executor):
    def __init__(self, evaluate_task_name=PTConstants.EvalTaskName, project_id="", query=""):
        """
        Evaluation executor for the FLIP project. This executor is responsible for validating the model.
        """
        super(FLIP_EVALUATOR, self).__init__()

        self._evaluate_task_name = evaluate_task_name

        # Load the config
        self.config = {}
        working_dir = Path(__file__).parent.resolve()
        with open(str(working_dir / "config.json")) as file:
            self.config = json.load(file)
            self._test_split = self.config["TEST_SPLIT"]
            self.num_classes = self.config["num_classes"]

        # Setup the model
        self.device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
        self.models = {}
        for model_name, model_values in self.config["models"].items():
            self.models[model_name] = model_paths[model_values["path"]]

        # NB val transforms differ from the train transforms. No random affine augmentation is applied and the data is
        # not cropped into patches.
        self.val_transforms = get_eval_transforms()
        # Setup the training dataset
        self.flip = FLIP()
        self.project_id = project_id
        self.query = query
        self.dataframe = self.flip.get_dataframe(self.project_id, self.query)
        self.post_pred = AsDiscrete(argmax=True, to_onehot=self.num_classes)
        self.swi = get_sliding_window_inferer(sw_device=self.device)

    def get_image_and_label_list(self, dataframe):
        """Returns a list of dicts, each dict containing the path to an image and its corresponding label."""

        datalist = []
        # loop over each accession id in the train set
        for accession_id in dataframe["accession_id"]:
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
                print(f"Could not get image data folder path for {accession_id}: {err}")
                continue
            # accession_folder_path = Path(f"/app/data/images/net-1/{accession_id}")

            print(accession_folder_path)

            all_images = list(accession_folder_path.rglob("input_*.nii.gz"))
            print(all_images)

            this_accession_matches = 0
            print(f"Total base count found for accession_id {accession_id}: {len(all_images)}")
            for img in all_images:
                # for each image, find the corresponding segmentation mask
                seg = str(img).replace("/input_", "/label_")

                if not Path(seg).exists():
                    print(f"No matching segmentation mask for {img}.")
                    continue

                try:
                    img_header = nib.load(str(img))
                except nib.filebasedimages.ImageFileError as err:
                    print(f"Problem loading header of base image {str(img)}.")
                    print(f"{err=}")
                    print(f"{type(err)=}")
                    print(f"{err.args=}")
                    continue

                try:
                    seg_header = nib.load(seg)
                except nib.filebasedimages.ImageFileError as err:
                    print(f"Problem loading header of segmentation {str(seg)}.")
                    print(f"{err=}")
                    print(f"{type(err)=}")
                    print(f"{err.args=}")
                    continue

                # check is 3D and at least 128x128x128 in size and seg is the same
                if len(img_header.shape) != 3:
                    print(f"Image has other than 3 dimensions (it has {len(img_header.shape)}.)")
                    continue
                elif any([img_dim != seg_dim for img_dim, seg_dim in zip(img_header.shape, seg_header.shape)]):
                    print(
                        f"Image dimensions ({img_header.shape}) do not match "
                        f"segmentation dimensions ({seg_header.shape})."
                    )
                    continue
                else:
                    # defines keys for image and segmentation
                    datalist.append({"image": str(img), "label": seg})
                    print("Matching base image and segmentation added.")
                    this_accession_matches += 1

            print(f"Added {this_accession_matches} matched image + segmentation pairs for {accession_id}.")

        print(f"Found {len(datalist)} files in total.")

        # split into the training and testing data
        _, test_datalist = np.split(datalist, [int((1 - self._test_split) * len(datalist))])

        print(f"Found {len(test_datalist)} files in testing.")

        return test_datalist

    def _get_json_results_from_numpy(self, metric_results):
        output_results = {}
        for model_name, dice_scores in metric_results.items():
            output_results[model_name] = {
                "spleen": {
                    "mean_dice": np.mean(dice_scores[:, 1]).item(),
                    "raw_dice": [float(i) for i in dice_scores[:, 1]],
                }
            }

        return output_results

    def do_validation(self, fl_ctx, abort_signal):
        metric_results = {}
        for model_name, _ in self.models.items():
            self.models[model_name].eval()
            metric_results[model_name] = DiceMetric(reduction="none")  # Create DiceMetric instance

        num_images = 0
        self.logger.info(len(self.test_loader))

        with torch.no_grad():
            for i, batch in enumerate(self.test_loader):
                if abort_signal.triggered:
                    return 0

                images, labels = (
                    batch["image"].to(self.device),
                    batch["label"].to(self.device),
                )

                for model_name, model in self.models.items():
                    self.models[model_name].to(self.device)
                    # perform sliding window inference to get a prediction for the whole volume.
                    output = self.swi(inputs=images, network=self.models[model_name])

                    # Softmax
                    output = torch.softmax(output, dim=1)
                    # Ensure labels are one-hot encoded
                    output = decollate_batch(output)
                    output = torch.stack([self.post_pred(i) for i in output], 0)
                    labels_one_hot = one_hot(labels, num_classes=self.num_classes)

                    # Compute Dice metric using DiceMetric
                    metric_results[model_name](output, labels_one_hot)  # Accumulate Dice scores
                    self.models[model_name].cpu()

                batch_size = images.shape[0]
                num_images += batch_size
                self.logger.info(f"Validator Iteration: {i}, Num Images: {num_images}")

            # Compute final Dice score
            for model_name, dice_metric in metric_results.items():
                metric_results[model_name] = dice_metric.aggregate().cpu().numpy()

            dice_metric.reset()  # Reset metric for next validation phase

            json_results = self._get_json_results_from_numpy(metric_results)
            message = ""
            for model_name in self.models.keys():
                message += f"{json_results[model_name]['spleen']['mean_dice']:.4f} ({model_name})"
            self.log_info(fl_ctx, f"Validator finished on the client side: {message}")

        return json_results

    def execute(
        self,
        task_name: str,
        shareable: Shareable,
        fl_ctx: FLContext,
        abort_signal: Signal,
    ) -> Shareable:
        # Get images and labels and create dataset.
        test_dict = self.get_image_and_label_list(self.dataframe)
        self._test_dataset = Dataset(test_dict, transform=self.val_transforms)
        self.test_loader = DataLoader(self._test_dataset, batch_size=1, shuffle=False)

        self.log_info(fl_ctx, f"Received task name: {task_name}")
        if task_name == self._evaluate_task_name:
            dxo = from_shareable(shareable)

            # Process data kind.
            for model_name, _ in self.models.items():
                self.log_info(fl_ctx, f"Loading model {model_name} at client {fl_ctx.get_identity_name()}...")
                dxo_model = dxo.data.get(model_name)
                if not dxo_model.data_kind == DataKind.WEIGHTS:
                    self.log_exception(
                        fl_ctx,
                        f"DXO for model {model_name} is of type {dxo_model.data_kind} but expected type WEIGHTS.",
                    )
                    return make_reply(ReturnCode.BAD_TASK_DATA)

                # Extract weights and ensure they are tensor.
                weights = {k: torch.as_tensor(v, device=self.device) for k, v in dxo_model.data.items()}
                self.models[model_name].load_state_dict(weights)

            # Get validation results:
            json_results = self.do_validation(fl_ctx, abort_signal)
            if abort_signal.triggered:
                return make_reply(ReturnCode.TASK_ABORTED)

            dxo = DXO(data_kind=DataKind.METRICS, data=json_results)
            return dxo.to_shareable()

        else:
            return make_reply(ReturnCode.TASK_UNKNOWN)
