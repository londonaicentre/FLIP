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

import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np
import torch
from nvflare.apis.event_type import EventType
from nvflare.app_common.abstract.model import make_model_learnable
from nvflare.app_common.app_constant import AppConstants

from flip.nvflare.components.fedopt_shareable_generator import FlipFedOptShareableGenerator

# The dict-config resolution imports the user's ``models`` module by path; in a real job it
# lives in custom/, so stub it here like the recipe tests do.
_stub_models = types.ModuleType("models")
_stub_models.get_model = lambda: torch.nn.Linear(1, 1)
sys.modules.setdefault("models", _stub_models)

_STOCK = FlipFedOptShareableGenerator.__mro__[1]


class TestFlipFedOptShareableGenerator:
    def _generator(self, source_model) -> FlipFedOptShareableGenerator:
        return FlipFedOptShareableGenerator(source_model=source_model)

    def test_resolves_dict_source_model_at_start_run(self):
        """A ``{"path": ...}`` config is instantiated into a live model at START_RUN — where stock
        consumes ``source_model`` — so stock's own resolution then sees a torch module."""
        generator = self._generator({"path": "models.get_model"})
        with patch.object(_STOCK, "handle_event") as stock_handle:
            generator.handle_event(EventType.START_RUN, MagicMock())
        assert isinstance(generator.source_model, torch.nn.Module)
        stock_handle.assert_called_once()

    def test_other_events_leave_dict_unresolved(self):
        """Resolution is gated on START_RUN; unrelated events pass straight through to stock."""
        generator = self._generator({"path": "models.get_model"})
        with patch.object(_STOCK, "handle_event") as stock_handle:
            generator.handle_event("some_other_event", MagicMock())
        assert generator.source_model == {"path": "models.get_model"}
        stock_handle.assert_called_once()

    def test_dict_without_path_panics_and_skips_stock(self):
        generator = self._generator({"args": {}})
        generator.system_panic = MagicMock()
        with patch.object(_STOCK, "handle_event") as stock_handle:
            generator.handle_event(EventType.START_RUN, MagicMock())
        generator.system_panic.assert_called_once()
        stock_handle.assert_not_called()
        # Left unresolved: the panic aborts the run rather than half-configuring the generator.
        assert generator.source_model == {"args": {}}

    def test_unresolvable_path_panics_and_skips_stock(self):
        """A typo'd path (or an import error inside the user's models.py) must panic, not raise:
        the event dispatcher swallows handle_event exceptions, which would otherwise surface a
        full round later as an opaque NoneType failure."""
        generator = self._generator({"path": "models.no_such_factory"})
        generator.system_panic = MagicMock()
        with patch.object(_STOCK, "handle_event") as stock_handle:
            generator.handle_event(EventType.START_RUN, MagicMock())
        generator.system_panic.assert_called_once()
        assert "no_such_factory" in generator.system_panic.call_args.kwargs.get(
            "reason", generator.system_panic.call_args.args[0] if generator.system_panic.call_args.args else ""
        )
        stock_handle.assert_not_called()
        assert generator.source_model == {"path": "models.no_such_factory"}

    def test_non_dict_source_model_passes_straight_to_stock(self):
        """A component-id string keeps stock semantics untouched (resolved by stock at START_RUN)."""
        generator = self._generator("model")
        with patch.object(_STOCK, "handle_event") as stock_handle:
            generator.handle_event(EventType.START_RUN, MagicMock())
        assert generator.source_model == "model"
        stock_handle.assert_called_once()

    def test_resolution_happens_once(self):
        """The resolved module must not be re-instantiated on a later START_RUN (optimizer state
        lives on the resolved model's parameters)."""
        generator = self._generator({"path": "models.get_model"})
        with patch.object(_STOCK, "handle_event"):
            generator.handle_event(EventType.START_RUN, MagicMock())
            first = generator.source_model
            generator.handle_event(EventType.START_RUN, MagicMock())
        assert generator.source_model is first

    def test_shareable_to_learnable_syncs_model_from_global(self):
        """The generator's own model copy is loaded from the live global before stock's optimizer
        step. This is the guard against the second-instance trap: the copy is built independently
        of the persistor's model (whose state seeds the round-0 global, SERVER_CHECKPOINT
        included), and stock promotes the copy's state_dict as the new global — unsynced, round 1
        would replace the checkpointed global with this copy's fresh random init."""
        generator = self._generator({"path": "models.get_model"})
        generator.model = torch.nn.Linear(1, 1)
        optimizer = torch.optim.SGD(generator.model.parameters(), lr=1.0)
        global_weights = {
            "weight": np.array([[42.0]], dtype=np.float32),
            "bias": np.array([7.0], dtype=np.float32),
        }
        fl_ctx = MagicMock()
        fl_ctx.get_prop = MagicMock(
            side_effect=lambda key, *a, **k: make_model_learnable(global_weights, {})
            if key == AppConstants.GLOBAL_MODEL
            else None
        )
        with patch.object(_STOCK, "shareable_to_learnable") as stock_convert:
            generator.shareable_to_learnable(MagicMock(), fl_ctx)
        stock_convert.assert_called_once()
        assert generator.model.weight.item() == 42.0
        assert generator.model.bias.item() == 7.0
        # In-place copy: the optimizer's parameter references (and thus its state) stay valid.
        assert optimizer.param_groups[0]["params"][0] is generator.model.weight

    def test_shareable_to_learnable_without_global_defers_to_stock(self):
        """No global in the context → skip the sync; stock's own 'No global base model!' panic
        owns that failure with the clearer message."""
        generator = self._generator({"path": "models.get_model"})
        generator.model = torch.nn.Linear(1, 1)
        fl_ctx = MagicMock()
        fl_ctx.get_prop = MagicMock(return_value=None)
        before = generator.model.weight.detach().clone()
        with patch.object(_STOCK, "shareable_to_learnable") as stock_convert:
            generator.shareable_to_learnable(MagicMock(), fl_ctx)
        stock_convert.assert_called_once()
        assert torch.equal(generator.model.weight.detach(), before)
