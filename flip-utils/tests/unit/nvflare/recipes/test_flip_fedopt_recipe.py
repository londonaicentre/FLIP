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
from pathlib import Path

import pytest

from flip.nvflare.recipes import FlipFedAvgRecipe, FlipFedOptRecipe
from flip.nvflare.recipes.flip_fedopt_recipe import _DEFAULT_OPTIMIZER_ARGS


class TestFlipFedOptRecipe:
    def test_defaults_match_stock_nvflare_fedopt(self):
        """The server-optimizer defaults must track stock NVFLARE FedOpt: SGD at lr=1.0 with
        momentum=0.6 and no LR schedule. (Deliberately NOT the retired template's server Adam,
        which was never live — its aggregation was broken by the ScatterAndGather guard bug —
        and destroys the global model in one step.) ``device="cpu"`` is FLIP's own default:
        the hub's fl-server has no GPU."""
        recipe = FlipFedOptRecipe()
        assert recipe.optimizer_args["path"] == "torch.optim.SGD"
        assert recipe.optimizer_args["args"] == {"lr": 1.0, "momentum": 0.6}
        assert recipe.lr_scheduler_args is None
        assert recipe.device == "cpu"

    def test_default_optimizer_args_are_isolated_per_instance(self):
        """The module-level default must never be aliased into an instance: stock's generator
        mutates ``optimizer_args["args"]`` in place at START_RUN (inserting the live params
        generator), which would otherwise poison the process-wide default for every later
        default-constructed recipe."""
        recipe = FlipFedOptRecipe()
        assert recipe.optimizer_args is not _DEFAULT_OPTIMIZER_ARGS
        assert recipe.optimizer_args["args"] is not _DEFAULT_OPTIMIZER_ARGS["args"]
        recipe.optimizer_args["args"]["params"] = object()
        assert "params" not in _DEFAULT_OPTIMIZER_ARGS["args"]

    def test_user_optimizer_args_are_normalised_like_stock(self):
        """A user dict without ``config_type: "dict"`` must be normalised (stock FedOptRecipe's
        ensure_config_type_dict step): without it the server ComponentBuilder instantiates the
        optimizer class at config-load time — sans params — and the job dies opaquely. The
        caller's dict must also not be mutated."""
        supplied = {"path": "torch.optim.Adam", "args": {"lr": 0.001}}
        recipe = FlipFedOptRecipe(optimizer_args=supplied)
        assert recipe.optimizer_args["config_type"] == "dict"
        assert recipe.optimizer_args is not supplied
        assert "config_type" not in supplied

    def test_pathless_optimizer_args_rejected_at_construction(self):
        """A component config with no path/class_path/name would otherwise surface mid-job as a
        server panic — fail at recipe construction instead."""
        with pytest.raises(ValueError, match="optimizer_args"):
            FlipFedOptRecipe(optimizer_args={"args": {"lr": 0.1}})

    def test_export_wires_fedopt_server_components(self, tmp_path: Path):
        """The exported server config carries the FedOpt shareable generator (sourcing the model
        from the user's ``models.get_model``) and an aggregator left at the stock default, whose
        ``expected_data_kind`` is WEIGHT_DIFF — assert the override is genuinely absent, not
        defaulted around. Everything else (workflows, FLIP components) must match the standard
        FedAvg layout, since the client contract is identical."""
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

        # Stock default = WEIGHT_DIFF; an explicit override sneaking back in is the dice-0.006
        # class of regression (every client contribution rejected), so assert absence outright.
        assert "expected_data_kind" not in components["aggregator"].get("args", {})

        # The FLIP server components and workflow order are inherited from the FedAvg build.
        assert "persistor" in components
        assert "flip_server_event_handler" in components
        assert server_cfg["workflows"][-1]["path"].endswith("broadcast_task.BroadcastTask")

    def test_client_contract_matches_standard(self, tmp_path: Path):
        """FedOpt must not change the client side: the exported client config is byte-identical to
        the FedAvg recipe's, so every ``standard`` app runs under ``fed_opt`` unchanged."""
        FlipFedOptRecipe().export(tmp_path / "fedopt")
        FlipFedAvgRecipe().export(tmp_path / "fedavg")
        client_cfg = Path("app") / "config" / "config_fed_client.json"
        fedopt_client = (tmp_path / "fedopt" / "flip_fedopt" / client_cfg).read_text()
        fedavg_client = (tmp_path / "fedavg" / "flip_fedavg" / client_cfg).read_text()
        assert fedopt_client == fedavg_client
