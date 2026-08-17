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

from nvflare.apis.fl_context import FLContext
from nvflare.app_opt.pt.fedopt import PTFedOptModelShareableGenerator
from nvflare.fuel.utils.class_utils import instantiate_class


class FlipFedOptShareableGenerator(PTFedOptModelShareableGenerator):
    """Stock FedOpt shareable generator that also accepts a dict-config ``source_model``.

    Stock ``PTFedOptModelShareableGenerator`` resolves ``source_model`` either as a component id
    (str) or a live ``torch.nn.Module`` — neither of which a recipe can provide for the user's
    ``models.get_model`` factory, which only exists inside the deployed job's ``custom/``. This
    subclass additionally accepts the same ``{"path": "models.get_model"}`` dict config that
    ``PTFileModelPersistor`` takes for its ``model`` arg, resolving it lazily with
    ``instantiate_class`` on the first event (i.e. inside the job, where ``custom/`` is on the
    python path).

    No ``__init__`` override on purpose: recipe/FedJob serialisation captures the constructor
    signature of the most-derived ``__init__``, so inheriting stock's keeps every stock arg
    (``optimizer_args``, ``lr_scheduler_args``, ``device``, …) serialisable
    (see the ``*args``/``**kwargs`` note on ``flip.nvflare.controllers.ScatterAndGather``).
    """

    def handle_event(self, event_type: str, fl_ctx: FLContext) -> None:
        """Resolve a dict-config ``source_model`` into a live model, then defer to stock.

        Args:
            event_type (str): The fired NVFLARE event type.
            fl_ctx (FLContext): Context provided by the workflow.
        """
        if isinstance(self.source_model, dict):
            class_path = self.source_model.get("path")
            if not class_path:
                self.system_panic(reason="Dict source_model config must have a 'path' key", fl_ctx=fl_ctx)
                return
            self.source_model = instantiate_class(class_path, self.source_model.get("args", {}))
        super().handle_event(event_type, fl_ctx)
