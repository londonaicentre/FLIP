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

"""Recipe for the FLIP FedOpt job type — server-side optimizer aggregation over client diffs.

FedOpt (Reddi et al., "Adaptive Federated Optimization") replaces FedAvg's plain
apply-the-average with a **server-side optimizer step**: the aggregator averages the clients'
``WEIGHT_DIFF`` updates directly (as in every FLIP job type — stock NVFLARE semantics), and the
shareable generator treats the averaged diff as a pseudo-gradient, applying it to the global
model through a configurable ``torch.optim`` optimizer (+ optional LR scheduler).

The client contract is identical to the ``standard`` job type: a Client API ``trainer.py``
that sends its update as ``FLModel(params=diff, params_type="DIFF")`` — every ``standard``
tutorial app is FedOpt-compatible as-is.

Optimizer defaults follow stock NVFLARE FedOpt (``fedopt_ctl`` / the official ``FedOptRecipe``):
server SGD at ``lr=1.0`` with ``momentum=0.6`` and no LR scheduler. The ``device="cpu"`` default
is FLIP's own (stock defaults to cuda-if-available; the hub's fl-server has no GPU). The retired
hand-written template wired server Adam — its README cited ``lr=0.5``, though the config's ``lr``
was an unsubstituted ``{lr}`` placeholder — but its aggregation never actually ran due to the
ScatterAndGather guard bug fixed alongside this recipe, and once live an Adam step at that scale
destroys the global model in one round, so it was not carried forward.
"""

from __future__ import annotations

import copy

from nvflare.recipe.utils import ensure_config_type_dict

from flip.nvflare.components import FlipFedOptShareableGenerator
from flip.nvflare.recipes.flip_fedavg_recipe import FlipFedAvgRecipe

_DEFAULT_OPTIMIZER_ARGS = {
    "path": "torch.optim.SGD",
    "args": {"lr": 1.0, "momentum": 0.6},
    "config_type": "dict",
}


class FlipFedOptRecipe(FlipFedAvgRecipe):
    """FLIP FedOpt recipe — :class:`FlipFedAvgRecipe` with a server-optimizer aggregation scheme.

    Args:
        optimizer_args: ``torch.optim`` component config for the server optimizer
            (``{"path": ..., "args": {...}}``). Defaults to SGD at ``lr=1.0`` with
            ``momentum=0.6`` (stock NVFLARE FedOpt).
        lr_scheduler_args: Optional ``torch.optim.lr_scheduler`` component config for the server
            optimizer's schedule. Defaults to ``None`` (no schedule).
        device: Device the server-side model copy lives on for the optimizer step.
        **kwargs: Everything :class:`FlipFedAvgRecipe` takes (rounds, timeouts, best-model
            selection, DP filter, …) applies unchanged.
    """

    job_name = "flip_fedopt"

    def __init__(
        self,
        *,
        optimizer_args: dict | None = None,
        lr_scheduler_args: dict | None = None,
        device: str = "cpu",
        **kwargs,
    ):
        if optimizer_args is not None and not any(k in optimizer_args for k in ("path", "class_path", "name")):
            raise ValueError(
                "optimizer_args must be a component config with a 'path', e.g. "
                "{'path': 'torch.optim.SGD', 'args': {'lr': 1.0}}"
            )
        # Set before super().__init__: the base constructor builds the FedJob, which calls the
        # _make_shareable_generator hook below. deepcopy isolates instances from the shared module
        # default AND from the caller's dict — stock's generator mutates optimizer_args["args"] in
        # place at START_RUN (it inserts the live params generator). ensure_config_type_dict is the
        # stock FedOptRecipe normalisation this recipe must mirror: without config_type="dict" the
        # server ComponentBuilder instantiates torch.optim.* at config-load time (sans params) and
        # the job dies with an opaque failed-to-instantiate error.
        self.optimizer_args = ensure_config_type_dict(copy.deepcopy(optimizer_args or _DEFAULT_OPTIMIZER_ARGS))
        self.lr_scheduler_args = ensure_config_type_dict(copy.deepcopy(lr_scheduler_args))
        self.device = device
        super().__init__(**kwargs)

    def _make_shareable_generator(self) -> FlipFedOptShareableGenerator:
        """Build the FedOpt generator, sourcing the model from the user's ``models.get_model``.

        Returns:
            FlipFedOptShareableGenerator: Applies the aggregated ``WEIGHT_DIFF`` to the global
            model through the configured server optimizer.
        """
        return FlipFedOptShareableGenerator(
            device=self.device,
            source_model={"path": "models.get_model"},
            optimizer_args=self.optimizer_args,
            lr_scheduler_args=self.lr_scheduler_args,
        )
