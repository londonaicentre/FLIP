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
from nvflare.apis.fl_context import FLContext
from nvflare.app_common.abstract.model import ModelLearnable
from nvflare.app_opt.pt.file_model_persistor import PTFileModelPersistor

from flip.constants import FlipConstants
from flip.nvflare.runtime import get_flip_model_id


class InitialCheckpointPTModelPersistor(PTFileModelPersistor):
    """Persistor that seeds the initial global model from a large backbone checkpoint
    staged **server-side**, so the checkpoint never has to be bundled into the job app
    (bundling a ~759 MiB file collapses NVFLARE's app-deploy to remote clients).

    The backbone filename is declared in the job's ``config.json`` under ``SERVER_CHECKPOINT``.
    At load time it is resolved in this order:

      1. ``<app>/custom/<checkpoint>`` — a checkpoint bundled in the app. Used by the local
         simulator (which copies the file into ``custom/``) and any legacy bundling.
      2. ``<SERVER_CHECKPOINT_ROOT>/<model_id>/<checkpoint>`` — the de-bundled checkpoint the
         FL API staged on the hub-local shared volume (production). Read straight from disk;
         the checkpoint is intentionally NOT shipped in the app bundle, so it never reaches
         the clients. Mirrors the Flower backend's ``/app/src`` shared mount and the eval
         ``EvaluationPTModelLocator``.

    The resolved checkpoint is loaded (``strict=False``) into the ``get_model()`` architecture
    so the round-0 global model that ScatterAndGather broadcasts carries the backbone **plus**
    freshly-initialised heads — a full state dict the clients can load. Clients therefore build
    only a bare architecture and receive all weights at round 0; they need no checkpoint file.

    When ``config.json`` declares no ``SERVER_CHECKPOINT`` (every other standard training job),
    this behaves exactly like the stock ``PTFileModelPersistor`` (initial weights from the model
    object), so it is a safe drop-in for the shared standard base app.
    """

    def __init__(self, model: torch.nn.Module | None = None, model_id: str = "", **kwargs) -> None:
        super().__init__(model=model, **kwargs)
        self._model_id_arg = model_id

    def _resolve_backbone(self, fl_ctx: FLContext):
        """Locate the declared backbone checkpoint, or ``None`` if none is declared/found."""
        app_dir = fl_ctx.get_engine().get_workspace().get_app_dir(fl_ctx.get_job_id())
        config_path = os.path.join(app_dir, "custom", "config.json")
        try:
            with open(config_path) as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

        checkpoint = config.get("SERVER_CHECKPOINT")
        if not checkpoint:
            # No backbone declared — stock persistor behaviour (initial weights from the model).
            return None

        bundled_path = os.path.join(app_dir, "custom", checkpoint)
        if os.path.isfile(bundled_path):
            return bundled_path

        if not FlipConstants.LOCAL_DEV:
            model_id = get_flip_model_id(fl_ctx, fallback=self._model_id_arg)
            shared_path = os.path.join(FlipConstants.SERVER_CHECKPOINT_ROOT, model_id, checkpoint)
            if os.path.isfile(shared_path):
                self.log_info(fl_ctx, f"Initial backbone from shared volume: {shared_path}")
                return shared_path
            self.log_error(
                fl_ctx,
                f"SERVER_CHECKPOINT '{checkpoint}' not found. Tried bundled path '{bundled_path}' and "
                f"shared-volume path '{shared_path}'.",
                fire_event=True,
            )
            return None

        self.log_error(
            fl_ctx,
            f"SERVER_CHECKPOINT '{checkpoint}' not found at '{bundled_path}' "
            f"(LOCAL_DEV; shared-volume fetch skipped).",
            fire_event=True,
        )
        return None

    def load_model(self, fl_ctx: FLContext) -> ModelLearnable:
        backbone_path = self._resolve_backbone(fl_ctx)
        if backbone_path is not None and isinstance(self.model, torch.nn.Module):
            # Load the backbone INTO the architecture (strict=False: the checkpoint is
            # backbone-only, heads stay freshly initialised). super().load_model() then
            # captures self.model.state_dict() (the no-source_ckpt path) as the initial
            # global model — a full state dict broadcast to clients at round 0.
            data = torch.load(backbone_path, map_location="cpu", weights_only=True)
            missing, unexpected = self.model.load_state_dict(data, strict=False)
            self.log_info(
                fl_ctx,
                f"Loaded backbone into initial global model from {backbone_path} "
                f"(missing={len(missing)}, unexpected={len(unexpected)} keys).",
            )
        return super().load_model(fl_ctx)
