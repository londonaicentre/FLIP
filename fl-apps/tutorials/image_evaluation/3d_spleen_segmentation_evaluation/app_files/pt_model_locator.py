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

import json
import os
from pathlib import Path
from typing import Union

import torch
import torch.cuda
from nvflare.apis.dxo import DXO, DataKind
from nvflare.apis.fl_context import FLContext
from nvflare.app_common.abstract.model import model_learnable_to_dxo
from nvflare.app_common.abstract.model_locator import ModelLocator
from nvflare.app_opt.pt import PTModelPersistenceFormatManager


class PTModelLocator(ModelLocator):
    def __init__(self, exclude_vars=None):
        super(PTModelLocator, self).__init__()

        working_dir = Path(__file__).parent.resolve()
        with open(os.path.join(working_dir, "config.json"), "r") as file:
            self.config = json.load(file)

        self.models = None
        working_dir = Path(__file__).parent.resolve()

        self.model_weights = os.path.join(Path(__file__).parent.resolve(), "model.pt")

        self.exclude_vars = exclude_vars

    def locate_model(self, fl_ctx: FLContext) -> Union[DXO, None]:
        if self.models is None:
            if "models" not in self.config.keys():
                self.log_error(
                    fl_ctx,
                    "In this pipeline, there must be a models key-element object in the config.json file, "
                    "pointing to the getter function as well the architecture.",
                    fire_event=True,
                )
            else:
                models_config = self.config["models"]
                self.model_names = models_config.keys()
                self.models = {}

            from models import model_paths

            for name in self.model_names:
                model_checkpoint = models_config[name]["checkpoint"]
                if not os.path.isfile(os.path.join(Path(__file__).parent.resolve(), model_checkpoint)):
                    self.log_error(
                        fl_ctx,
                        f"Model checkpoint for model {name} not found at "
                        f"{os.path.join(Path(__file__).parent.resolve(), model_checkpoint)}",
                        fire_event=True,
                    )
                net = model_paths[models_config[name]["path"]]
                self.models[name] = torch.load(
                    os.path.join(Path(__file__).parent.resolve(), model_checkpoint),
                    weights_only=True,
                    map_location="cuda" if torch.cuda.is_available() else "cpu",
                )
                try:
                    net.load_state_dict(self.models[name], strict=True)
                except Exception as e:
                    self.log_error(
                        fl_ctx,
                        f"The weights for network {name} could not be loaded into the object: {e}",
                        fire_event=True,
                    )

        all_model_dxo = {}
        for model_name, weight in self.models.items():
            # We convert this into a DXO
            persistence_manager = PTModelPersistenceFormatManager(weight, default_train_conf=None)
            # Model learnable to DXO:
            ml = persistence_manager.to_model_learnable(exclude_vars=None)
            # We convert this into a DXO:
            all_model_dxo[model_name] = model_learnable_to_dxo(ml)

        # Create dxo and returnf
        return DXO(data_kind=DataKind.COLLECTION, data=all_model_dxo)
