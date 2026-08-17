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

import torch
from nvflare.apis.event_type import EventType
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable
from nvflare.app_common.abstract.model import ModelLearnable, ModelLearnableKey
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_opt.pt.fedopt import PTFedOptModelShareableGenerator
from nvflare.fuel.utils.class_utils import instantiate_class
from nvflare.security.logging import secure_format_exception


class FlipFedOptShareableGenerator(PTFedOptModelShareableGenerator):
    """Stock FedOpt shareable generator that also accepts a dict-config ``source_model``.

    Stock ``PTFedOptModelShareableGenerator`` resolves ``source_model`` either as a component id
    (str) or a live ``torch.nn.Module`` — neither of which a recipe can provide for the user's
    ``models.get_model`` factory, which only exists inside the deployed job's ``custom/``. This
    subclass additionally accepts the same ``{"path": "models.get_model"}`` dict config that
    ``PTFileModelPersistor`` takes for its ``model`` arg. Two resolution paths cover the two ways
    the component comes to life, and the redundancy is deliberate:

    * **Deployed JSON job**: NVFLARE's ``ComponentBuilder`` resolves the nested dict at config-load
      time (it is a valid class config — deliberately *without* ``config_type: "dict"``, unlike the
      optimizer args), so ``handle_event`` already sees a live module.
    * **Direct instantiation** (SimEnv, a live recipe object, unit tests): the dict survives
      construction and is resolved here at ``START_RUN`` — where stock consumes ``source_model`` —
      panicking loudly on a bad path or an import error inside the user's ``models.py`` (raising
      instead would be swallowed by the event dispatcher and surface a full round later as an
      opaque ``NoneType`` failure).

    Because the resolved instance is built by this component, it is a *second* model object,
    independent of the one inside the persistor whose ``state_dict()`` seeds the round-0 global
    (including any ``SERVER_CHECKPOINT`` backbone). Stock never syncs its copy from the global —
    it promotes ``self.model.state_dict()`` as the new global after every optimizer step — so
    :meth:`shareable_to_learnable` first loads the live global into this copy in place. Without
    that sync, round 1 would silently replace the global (checkpointed backbone included) with
    this instance's fresh random init plus one step.

    No ``__init__`` override on purpose: recipe/FedJob serialisation captures the constructor
    signature of the most-derived ``__init__``, so inheriting stock's keeps every stock arg
    (``optimizer_args``, ``lr_scheduler_args``, ``device``, …) serialisable
    (see the ``*args``/``**kwargs`` note on ``flip.nvflare.controllers.ScatterAndGather``).
    """

    # Stock assigns this in __init__ without an annotation; annotate the widened contract here
    # (dict config | component id | live module) so type checkers can follow the resolution.
    source_model: dict | str | torch.nn.Module

    def handle_event(self, event_type: str, fl_ctx: FLContext) -> None:
        """Resolve a dict-config ``source_model`` into a live model at ``START_RUN``, then defer.

        Args:
            event_type (str): The fired NVFLARE event type.
            fl_ctx (FLContext): Context provided by the workflow.
        """
        if event_type == EventType.START_RUN and isinstance(self.source_model, dict):
            class_path = self.source_model.get("path")
            if not class_path:
                self.system_panic(reason="Dict source_model config must have a 'path' key", fl_ctx=fl_ctx)
                return
            try:
                self.source_model = instantiate_class(class_path, self.source_model.get("args", {}))
            except Exception as e:
                self.system_panic(
                    reason=f"Failed to resolve source_model {class_path!r}: {secure_format_exception(e)}",
                    fl_ctx=fl_ctx,
                )
                return
        super().handle_event(event_type, fl_ctx)

    def shareable_to_learnable(self, shareable: Shareable, fl_ctx: FLContext) -> ModelLearnable:
        """Sync our model copy to the live global, then run stock's server-optimizer step.

        The values are copied *into* the existing parameter tensors (``load_state_dict``), so the
        optimizer built at ``START_RUN`` keeps its parameter references and momentum state. After
        round 1 the copy is value-identical (the global *is* this model's previous output), so the
        per-round sync is a cheap no-op that keeps the pair exact by construction.

        Args:
            shareable (Shareable): The aggregated ``WEIGHT_DIFF`` shareable.
            fl_ctx (FLContext): Context provided by the workflow.

        Returns:
            ModelLearnable: The updated global model from stock's FedOpt step.
        """
        base_model = fl_ctx.get_prop(AppConstants.GLOBAL_MODEL)
        if isinstance(self.model, torch.nn.Module) and base_model:
            state = {key: torch.as_tensor(value) for key, value in base_model[ModelLearnableKey.WEIGHTS].items()}
            # strict=False: a key mismatch must not hard-crash the sync — stock's own buffer
            # patching and panics cover architectural mismatches with clearer messages.
            self.model.load_state_dict(state, strict=False)
        return super().shareable_to_learnable(shareable, fl_ctx)
