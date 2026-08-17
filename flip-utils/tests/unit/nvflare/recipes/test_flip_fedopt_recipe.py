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

import json
import sys
import types
from pathlib import Path

# PTFileModelPersistor's model={"path": "models.get_model"} triggers an import of the user's
# ``models`` module at construction. In a real job that module lives in custom/; under unit
# tests we inject a stub before importing the recipe.
_stub_models = types.ModuleType("models")
_stub_models.get_model = lambda: object()
sys.modules.setdefault("models", _stub_models)

from nvflare.apis.dxo import DataKind  # noqa: E402

from flip.nvflare.recipes import FlipFedAvgRecipe, FlipFedOptRecipe  # noqa: E402


class TestFlipFedOptRecipe:
    def test_defaults_match_stock_nvflare_fedopt(self):
        """The server-optimizer defaults must track stock NVFLARE FedOpt: SGD at lr=1.0 with
        momentum=0.6 and no LR schedule, on a CPU-held model copy. (Deliberately NOT the retired
        template's Adam-at-0.5, which was never live — its aggregation was broken by the
        ScatterAndGather guard bug — and destroys the global model in one step.)"""
        recipe = FlipFedOptRecipe()
        assert recipe.optimizer_args["path"] == "torch.optim.SGD"
        assert recipe.optimizer_args["args"] == {"lr": 1.0, "momentum": 0.6}
        assert recipe.lr_scheduler_args is None
        assert recipe.device == "cpu"
        assert recipe._aggregator_data_kind() == DataKind.WEIGHT_DIFF

    def test_export_wires_fedopt_server_components(self, tmp_path: Path):
        """The exported server config carries the FedOpt shareable generator (sourcing the model
        from the user's ``models.get_model``) and an aggregator whose ``expected_data_kind``
        resolves to WEIGHT_DIFF — the stock default, so it may legitimately be omitted from the
        serialised args. Everything else (workflows, FLIP components) must match the standard
        FedAvg layout, since the client contract is identical."""
        import torch

        sys.modules["models"].get_model = lambda: torch.nn.Linear(1, 1)
        try:
            recipe = FlipFedOptRecipe()
            recipe.export(tmp_path)
            job_dir = tmp_path / recipe.job.name
            assert recipe.job.name == "flip_fedopt"

            server_cfg = json.loads((job_dir / "app" / "config" / "config_fed_server.json").read_text())
            components = {c["id"]: c for c in server_cfg["components"]}

            generator = components["shareable_generator"]
            assert generator["path"].endswith("fedopt_shareable_generator.FlipFedOptShareableGenerator")
            assert generator["args"]["source_model"] == {"path": "models.get_model"}
            assert generator["args"]["optimizer_args"]["path"] == "torch.optim.SGD"

            aggregator = components["aggregator"]
            assert aggregator["args"].get("expected_data_kind", "WEIGHT_DIFF") == "WEIGHT_DIFF"

            # The FLIP server components and workflow order are inherited from the FedAvg build.
            assert "persistor" in components
            assert "flip_server_event_handler" in components
            assert server_cfg["workflows"][-1]["path"].endswith("broadcast_task.BroadcastTask")
        finally:
            sys.modules["models"].get_model = lambda: object()

    def test_client_contract_matches_standard(self, tmp_path: Path):
        """FedOpt must not change the client side: the exported client config is byte-identical to
        the FedAvg recipe's, so every ``standard`` app runs under ``fed_opt`` unchanged."""
        import torch

        sys.modules["models"].get_model = lambda: torch.nn.Linear(1, 1)
        try:
            FlipFedOptRecipe().export(tmp_path / "fedopt")
            FlipFedAvgRecipe().export(tmp_path / "fedavg")
            client_cfg = Path("app") / "config" / "config_fed_client.json"
            fedopt_client = (tmp_path / "fedopt" / "flip_fedopt" / client_cfg).read_text()
            fedavg_client = (tmp_path / "fedavg" / "flip_fedavg" / client_cfg).read_text()
            assert fedopt_client == fedavg_client
        finally:
            sys.modules["models"].get_model = lambda: object()
