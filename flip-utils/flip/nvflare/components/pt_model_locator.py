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

import json
import os

import torch
import torch.cuda
from nvflare.apis.dxo import DXO, DataKind
from nvflare.apis.fl_context import FLContext
from nvflare.app_common.abstract.model import model_learnable_to_dxo
from nvflare.app_common.abstract.model_locator import ModelLocator
from nvflare.app_opt.pt import PTModelPersistenceFormatManager

from flip.constants import FlipConstants, FlipMetaKey, PTConstants
from flip.nvflare.runtime import get_flip_model_id


class PTModelLocator(ModelLocator):
    def __init__(self, exclude_vars: list[str] | None = None, model: torch.nn.Module | None = None) -> None:
        super(PTModelLocator, self).__init__()

        if model is None:
            from models import get_model

            model = get_model()

        self.model = model
        self.exclude_vars = exclude_vars

    def get_model_names(self, fl_ctx: FLContext) -> list[str]:
        return [PTConstants.PTServerName]

    def locate_model(self, model_name: str, fl_ctx: FLContext) -> DXO | None:
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
    def __init__(self, exclude_vars: list[str] | None = None, model: torch.nn.Module | None = None) -> None:
        super(InitialPTModelLocator, self).__init__()

        if model is None:
            from models import get_model

            model = get_model()

        self.model = model
        self.exclude_vars = exclude_vars

    def get_model_names(self, fl_ctx: FLContext) -> list[str]:
        return [PTConstants.PTServerName]

    def locate_model(self, model_name: str, fl_ctx: FLContext) -> DXO | None:
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
    def __init__(self, exclude_vars: list[str] | None = None, model_id: str = "") -> None:
        super(EvaluationPTModelLocator, self).__init__()
        self.models = None
        self.exclude_vars = exclude_vars
        self.model_id = model_id

    def _resolve_checkpoint_path(self, fl_ctx: FLContext, app_dir: str, name: str, model_checkpoint: str):
        """Locate a model checkpoint for server-side loading.

        Resolution order:
          1. ``<app_dir>/custom/<checkpoint>`` — a checkpoint bundled in the app. Used by
             the local simulator (which copies the ``.pt`` into ``custom/``) and any legacy
             bundling.
          2. ``<SERVER_CHECKPOINT_ROOT>/<model_id>/<checkpoint>`` — the de-bundled checkpoint
             the FL API staged on the hub-local shared volume (production). Read straight from
             disk; the checkpoint is intentionally NOT shipped in the app bundle, so it never
             reaches the clients. This mirrors the Flower backend's ``/app/src`` shared mount.

        Returns the resolved path, or ``None`` (after logging an error) when neither exists.
        """
        bundled_path = os.path.join(app_dir, "custom", model_checkpoint)
        if os.path.isfile(bundled_path):
            return bundled_path

        if not FlipConstants.LOCAL_DEV:
            shared_path = os.path.join(FlipConstants.SERVER_CHECKPOINT_ROOT, self.model_id, model_checkpoint)
            if os.path.isfile(shared_path):
                self.log_info(fl_ctx, f"Loading checkpoint for model '{name}' from shared volume: {shared_path}")
                return shared_path
            self.log_error(
                fl_ctx,
                f"Checkpoint for model '{name}' not found. Tried bundled path '{bundled_path}' and "
                f"shared-volume path '{shared_path}'.",
                fire_event=True,
            )
            return None

        self.log_error(
            fl_ctx,
            f"Checkpoint for model '{name}' not found at '{bundled_path}' "
            f"(LOCAL_DEV; shared-volume fetch skipped).",
            fire_event=True,
        )
        return None

    def locate_model(self, fl_ctx: FLContext) -> DXO | None:
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
                checkpoint_path = self._resolve_checkpoint_path(fl_ctx, app_dir, name, model_checkpoint)
                if checkpoint_path is None:
                    # Resolution already logged the failure (fire_event=True); skip this model.
                    continue

                net = model_paths[models_config[name]["path"]]
                self.models[name] = torch.load(
                    checkpoint_path,
                    weights_only=True,
                    map_location="cuda" if torch.cuda.is_available() else "cpu",
                )
                try:
                    # Validate against the same weights that are sent to clients
                    # below: a bare state_dict is used as-is, while an NVFLARE
                    # persistence-format checkpoint nests them under a "model" key
                    # (alongside "train_conf"/"meta_props"). PTModelPersistenceFormatManager
                    # normalises both, so the probe matches what is actually delivered
                    # instead of false-failing on persistence-format checkpoints.
                    var_dict = PTModelPersistenceFormatManager(
                        self.models[name], default_train_conf=None
                    ).var_dict
                    net.load_state_dict(var_dict, strict=True)
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


class EvaluationModelLocator(ModelLocator):
    """Locate uploaded checkpoint(s) for Client-API evaluation under the *standard* ModelLocator interface.

    Unlike :class:`EvaluationPTModelLocator` — which returns a single ``DataKind.COLLECTION`` DXO for the
    bespoke ``ModelEval`` controller — this exposes the stock ``get_model_names`` + ``locate_model(model_name,
    fl_ctx)`` contract so it drives NVFLARE's stock ``CrossSiteModelEval`` validate
    workflow directly. Each model named in ``config.json['models']`` becomes one ``DataKind.WEIGHTS`` DXO that
    the server broadcasts to clients as a single ``FLModel`` for the Client-API ``is_evaluate()`` path.

    The checkpoints are loaded server-side only — from the app's ``custom/`` directory when bundled
    (simulator / legacy bundling), else from the FL API's de-bundled staging volume at
    ``<SERVER_CHECKPOINT_ROOT>/<model_id>/`` (production; same resolution order as
    :class:`EvaluationPTModelLocator`). Clients never read the ``.pt`` files — they receive the
    weights over the ``validate`` task. ``model_id`` is resolved lazily from
    ``meta.json['custom_props']`` (recipe-built job types carry no component args).
    """

    def __init__(self) -> None:
        super().__init__()
        self.models: dict | None = None

    def _resolve_checkpoint_path(self, fl_ctx: FLContext, app_dir: str, name: str, model_checkpoint: str):
        """Locate a checkpoint: bundled ``custom/`` first, then the FL API's shared staging volume."""
        bundled_path = os.path.join(app_dir, "custom", model_checkpoint)
        if os.path.isfile(bundled_path):
            return bundled_path

        if not FlipConstants.LOCAL_DEV:
            try:
                model_id = get_flip_model_id(fl_ctx, fallback="")
            except ValueError:
                self.log_error(
                    fl_ctx,
                    f"Checkpoint for model '{name}' not bundled at '{bundled_path}' and no model_id "
                    "available (meta.json custom_props) to resolve the shared-volume path.",
                    fire_event=True,
                )
                return None
            shared_path = os.path.join(FlipConstants.SERVER_CHECKPOINT_ROOT, model_id, model_checkpoint)
            if os.path.isfile(shared_path):
                self.log_info(fl_ctx, f"Loading checkpoint for model '{name}' from shared volume: {shared_path}")
                return shared_path
            self.log_error(
                fl_ctx,
                f"Checkpoint for model '{name}' not found. Tried bundled path '{bundled_path}' and "
                f"shared-volume path '{shared_path}'.",
                fire_event=True,
            )
            return None

        self.log_error(
            fl_ctx,
            f"Checkpoint for model '{name}' not found at '{bundled_path}' "
            f"(LOCAL_DEV; shared-volume fetch skipped).",
            fire_event=True,
        )
        return None

    def _load_models(self, fl_ctx: FLContext) -> None:
        """Read ``config.json['models']`` and load each named checkpoint into ``self.models``."""
        app_dir = fl_ctx.get_engine().get_workspace().get_app_dir(fl_ctx.get_job_id())
        config_path = os.path.join(app_dir, "custom", "config.json")

        with open(config_path) as file:
            config = json.load(file)

        if "models" not in config:
            self.log_error(
                fl_ctx,
                "In the evaluation pipeline, a 'models' key mapping each model to its checkpoint must be "
                "present in config.json.",
                fire_event=True,
            )
            self.models = {}
            return

        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.models = {}
        for name, model_info in config["models"].items():
            checkpoint_path = self._resolve_checkpoint_path(fl_ctx, app_dir, name, model_info["checkpoint"])
            if checkpoint_path is None:
                continue
            self.models[name] = torch.load(checkpoint_path, weights_only=True, map_location=device)

    def get_model_names(self, fl_ctx: FLContext) -> list[str]:
        if self.models is None:
            self._load_models(fl_ctx)
        assert self.models is not None  # _load_models always assigns a dict
        return list(self.models.keys())

    def locate_model(self, model_name: str, fl_ctx: FLContext) -> DXO | None:
        if self.models is None:
            self._load_models(fl_ctx)
        assert self.models is not None  # _load_models always assigns a dict

        weights = self.models.get(model_name)
        if weights is None:
            self.log_error(
                fl_ctx, f"EvaluationModelLocator has no checkpoint for model: {model_name}", fire_event=False
            )
            return None

        persistence_manager = PTModelPersistenceFormatManager(weights, default_train_conf=None)
        ml = persistence_manager.to_model_learnable(exclude_vars=None)
        dxo = model_learnable_to_dxo(ml)
        # Name the broadcast in the DXO meta itself: CrossSiteModelEval's MODEL_OWNER header does not
        # survive the Client-API FLModel conversion, but DXO meta rides inside the payload to
        # flare.receive() on every client. This is what lets a Client-API evaluator attribute each
        # validate task's weights (e.g. the multimodel DeLong comparison).
        # `.value`, not the StrEnum member: FOBS serialises meta keys by type and rejects any type not
        # on its whitelist, so an enum key fails every task with "Type 'FlipMetaKey' is not allowed".
        dxo.set_meta_prop(FlipMetaKey.EVAL_MODEL_NAME.value, model_name)
        return dxo
