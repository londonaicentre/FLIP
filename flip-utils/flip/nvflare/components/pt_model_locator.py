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
from typing import List, Union

import torch
import torch.cuda
from nvflare.apis.dxo import DXO, DataKind
from nvflare.apis.fl_context import FLContext
from nvflare.app_common.abstract.model import model_learnable_to_dxo
from nvflare.app_common.abstract.model_locator import ModelLocator
from nvflare.app_opt.pt import PTModelPersistenceFormatManager

from flip.constants import FlipConstants, PTConstants


class PTModelLocator(ModelLocator):
    def __init__(self, exclude_vars=None, model=None):
        super(PTModelLocator, self).__init__()

        if model is None:
            from models import get_model

            model = get_model()

        self.model = model
        self.exclude_vars = exclude_vars

    def get_model_names(self, fl_ctx: FLContext) -> List[str]:
        return [PTConstants.PTServerName]

    def locate_model(self, model_name, fl_ctx: FLContext) -> Union[DXO, None]:
        if model_name == PTConstants.PTServerName:
            try:
                server_run_dir = fl_ctx.get_engine().get_workspace().get_app_dir(fl_ctx.get_job_id())
                # Log server_run_dir
                self.log_info(fl_ctx, f"Server run directory: {server_run_dir}")
                if FlipConstants.LOCAL_DEV:
                    model_path = os.path.join(server_run_dir, PTConstants.PTFileModelName)
                else:
                    model_path = os.path.join(server_run_dir, "model", PTConstants.PTFileModelName)
                if not os.path.exists(model_path):
                    self.log_error(fl_ctx, f"Model file not found at {model_path}", fire_event=False)
                    return None

                # Load the torch model
                device = "cuda" if torch.cuda.is_available() else "cpu"
                data = torch.load(model_path, map_location=device)

                # Setup the persistence manager.
                if self.model:
                    default_train_conf = {"train": {"model": type(self.model).__name__}}
                else:
                    default_train_conf = None

                # Use persistence manager to get learnable
                persistence_manager = PTModelPersistenceFormatManager(data, default_train_conf=default_train_conf)
                ml = persistence_manager.to_model_learnable(exclude_vars=None)

                # Create dxo and return
                return model_learnable_to_dxo(ml)
            except Exception as e:
                self.log_error(fl_ctx, f"Error in retrieving {model_name}: {e}", fire_event=False)
                return None
        else:
            self.log_error(fl_ctx, f"PTModelLocator doesn't recognize name: {model_name}", fire_event=False)
            return None


class InitialPTModelLocator(ModelLocator):
    def __init__(self, exclude_vars=None, model=None):
        super(InitialPTModelLocator, self).__init__()

        if model is None:
            from models import get_model

            model = get_model()

        self.model = model
        self.exclude_vars = exclude_vars

    def get_model_names(self, fl_ctx: FLContext) -> List[str]:
        return [PTConstants.PTServerName]

    def locate_model(self, model_name, fl_ctx: FLContext) -> Union[DXO, None]:
        # We look for existing models
        self.log_info(fl_ctx, f"Trying to locate the model {model_name}")
        if model_name == PTConstants.PTServerName:
            try:
                server_run_dir = fl_ctx.get_engine().get_workspace().get_app_dir(fl_ctx.get_job_id())
                model_path = os.path.join(server_run_dir, PTConstants.PTFileModelName)
                self.log_info(fl_ctx, model_path)
                if not os.path.exists(model_path):
                    self.log_info(fl_ctx, f"Model does not exist at {model_path}")
                    # Safe house: constant safehouse should be defined. Here we are just getting it directly.
                    model_path = os.path.join("/safehouse", fl_ctx.get_job_id(), PTConstants.PTFileModelName)
                    if not os.path.exists(model_path):
                        self.log_info(fl_ctx, f"Model does not exist at safehouse ({model_path})")
                        return None

                # Load the torch model
                device = "cuda" if torch.cuda.is_available() else "cpu"
                data = torch.load(
                    model_path,
                    map_location=device,
                    weights_only=True,
                )

                # Setup the persistence manager.
                if self.model:
                    default_train_conf = {"train": {"model": type(self.model).__name__}}
                    self.log_info(fl_ctx, f"Default train conf: {default_train_conf}")
                else:
                    default_train_conf = None

                # Use persistence manager to get learnable
                try:
                    persistence_manager = PTModelPersistenceFormatManager(data, default_train_conf=default_train_conf)
                    ml = persistence_manager.to_model_learnable(exclude_vars=None)
                except RuntimeError:
                    self.log_info(fl_ctx, f"Could not load the weights from {model_path} into the model. ")
                    return None

                # Create dxo and return
                return ml
            except Exception as e:
                self.log_error(fl_ctx, f"Error in retrieving {model_name}: {e}", fire_event=False)
                return None
        else:
            self.log_error(
                fl_ctx,
                f"PTModelLocator doesn't recognize name: {model_name}",
                fire_event=False,
            )
            return None


class EvaluationPTModelLocator(ModelLocator):
    def __init__(self, exclude_vars=None):
        super(EvaluationPTModelLocator, self).__init__()
        self.models = None
        self.exclude_vars = exclude_vars

    def locate_model(self, fl_ctx: FLContext) -> Union[DXO, None]:
        if self.models is None:
            # Load config from workspace
            app_dir = fl_ctx.get_engine().get_workspace().get_app_dir(fl_ctx.get_job_id())
            config_path = os.path.join(app_dir, "custom", "config.json")

            with open(config_path, "r") as file:
                self.config = json.load(file)

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
                checkpoint_path = os.path.join(app_dir, "custom", model_checkpoint)

                if not os.path.isfile(checkpoint_path):
                    self.log_error(
                        fl_ctx,
                        f"Model checkpoint for model {name} not found at {checkpoint_path}",
                        fire_event=True,
                    )
                net = model_paths[models_config[name]["path"]]
                self.models[name] = torch.load(
                    checkpoint_path,
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

        # Create dxo and return
        return DXO(data_kind=DataKind.COLLECTION, data=all_model_dxo)
