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

import torch

from flip.nvflare.components.fedopt_shareable_generator import FlipFedOptShareableGenerator

# The dict-config resolution imports the user's ``models`` module by path; in a real job it
# lives in custom/, so stub it here like the recipe tests do.
_stub_models = types.ModuleType("models")
_stub_models.get_model = lambda: torch.nn.Linear(1, 1)
sys.modules.setdefault("models", _stub_models)


class TestFlipFedOptShareableGenerator:
    def _generator(self, source_model) -> FlipFedOptShareableGenerator:
        return FlipFedOptShareableGenerator(source_model=source_model)

    def test_resolves_dict_source_model_before_stock_handling(self):
        """A ``{"path": ...}`` config is instantiated into a live model on the first event, so
        stock's own resolution (component id or nn.Module only) then sees a torch module."""
        generator = self._generator({"path": "models.get_model"})
        with patch.object(FlipFedOptShareableGenerator.__mro__[1], "handle_event") as stock_handle:
            generator.handle_event("any_event", MagicMock())
        assert isinstance(generator.source_model, torch.nn.Module)
        stock_handle.assert_called_once()

    def test_dict_without_path_panics_and_skips_stock(self):
        generator = self._generator({"args": {}})
        generator.system_panic = MagicMock()
        with patch.object(FlipFedOptShareableGenerator.__mro__[1], "handle_event") as stock_handle:
            generator.handle_event("any_event", MagicMock())
        generator.system_panic.assert_called_once()
        stock_handle.assert_not_called()
        # Left unresolved: the panic aborts the run rather than half-configuring the generator.
        assert generator.source_model == {"args": {}}

    def test_non_dict_source_model_passes_straight_to_stock(self):
        """A component-id string keeps stock semantics untouched (resolved by stock at START_RUN)."""
        generator = self._generator("model")
        with patch.object(FlipFedOptShareableGenerator.__mro__[1], "handle_event") as stock_handle:
            generator.handle_event("any_event", MagicMock())
        assert generator.source_model == "model"
        stock_handle.assert_called_once()

    def test_resolution_happens_once(self):
        """The resolved module must not be re-instantiated on every event (optimizer state lives
        on the resolved model's parameters)."""
        generator = self._generator({"path": "models.get_model"})
        with patch.object(FlipFedOptShareableGenerator.__mro__[1], "handle_event"):
            generator.handle_event("first", MagicMock())
            first = generator.source_model
            generator.handle_event("second", MagicMock())
        assert generator.source_model is first
