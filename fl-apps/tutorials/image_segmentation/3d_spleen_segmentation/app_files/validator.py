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

# Description: This file contains the FLIP_VALIDATOR class which is responsible for validating the model.

import numpy as np
import torch
from monai.data import DataLoader, Dataset, decollate_batch
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.app_common.app_constant import AppConstants
from trainer import FLIP_BASE


class FLIP_VALIDATOR(FLIP_BASE):
    def __init__(
        self,
        validate_task_name=AppConstants.TASK_VALIDATION,
        project_id="",
        query="",
    ):
        """
        Validation executor for the FLIP project. This executor is responsible for validating the model.
        """
        super(FLIP_VALIDATOR, self).__init__()

        self._validate_task_name = validate_task_name

        # Setup the dataset
        self.project_id = project_id
        self.query = query
        self.dataframe = self.flip.get_dataframe(self.project_id, self.query)

    def execute(
        self,
        task_name: str,
        shareable: Shareable,
        fl_ctx: FLContext,
        abort_signal: Signal,
    ) -> Shareable:
        test_dict = self.get_val_datalist()
        self._test_dataset = Dataset(test_dict, transform=self._val_transforms)
        self.test_loader = DataLoader(self._test_dataset, batch_size=1, shuffle=False)

        if task_name == self._validate_task_name:
            model_owner = "?"
            dxo = from_shareable(shareable)

            # Ensure data_kind is weights.
            if not dxo.data_kind == DataKind.WEIGHTS:
                self.log_exception(
                    fl_ctx,
                    f"DXO is of type {dxo.data_kind} but expected type WEIGHTS.",
                )
                return make_reply(ReturnCode.BAD_TASK_DATA)

            # Extract weights and ensure they are tensor.
            model_owner = shareable.get_header(AppConstants.MODEL_OWNER, "?")
            weights = {k: torch.as_tensor(v, device=self.device) for k, v in dxo.data.items()}

            # Get validation accuracy
            val_accuracy = self.do_validation(fl_ctx, weights, abort_signal)
            if abort_signal.triggered:
                return make_reply(ReturnCode.TASK_ABORTED)

            self.log_info(
                fl_ctx,
                f"Accuracy when validating {model_owner}'s model on {fl_ctx.get_identity_name()}s data: {val_accuracy}",
            )

            dxo = DXO(data_kind=DataKind.METRICS, data={"val_acc": val_accuracy})
            return dxo.to_shareable()

        else:
            return make_reply(ReturnCode.TASK_UNKNOWN)

    def do_validation(self, fl_ctx, weights, abort_signal):
        self.model.load_state_dict(weights)
        self.model.eval()

        val_dice = []
        num_images = 0

        with torch.no_grad():
            for i, batch in enumerate(self.test_loader):
                if abort_signal.triggered:
                    return 0

                images, labels = (
                    batch["image"].to(self.device),
                    batch["label"].to(self.device),
                )

                # Use the same inferer as trainer
                predictions = self.inferer(images, self.model)

                # Use same post-processing as trainer
                self.dice_acc.reset()
                predictions = decollate_batch(predictions)
                predictions = torch.stack([self.post_pred(i) for i in predictions], 0)
                labels = torch.stack([self.post_pred_gt(i) for i in labels], 0)
                self.dice_acc(y_pred=predictions, y=labels)
                acc = self.dice_acc.aggregate()
                val_dice.append(acc.item())

                batch_size = images.shape[0]
                num_images += batch_size
                self.logger.info(f"Validator Iteration: {i}, Num Images: {num_images}")

            # Compute final mean Dice score
            metric = np.mean(val_dice)
            self.logger.info(f"Validator Iteration finished: {i}, Metric: {metric}")
            self.flip.send_metrics_value(label="TEST_DICE", value=metric, round=0, fl_ctx=fl_ctx)

        return metric
