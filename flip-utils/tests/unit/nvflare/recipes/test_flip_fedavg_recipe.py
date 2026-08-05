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

import pytest

# PTFileModelPersistor's model={"path": "models.get_model"} triggers an import of the user's
# ``models`` module at construction. In a real job that module lives in custom/; under unit
# tests we inject a stub before importing the recipe.
_stub_models = types.ModuleType("models")
_stub_models.get_model = lambda: object()
sys.modules.setdefault("models", _stub_models)

from flip.nvflare.recipes import FlipFedAvgRecipe  # noqa: E402
from flip.nvflare.recipes.flip_fedavg_recipe import _DEV_MODEL_ID, PercentilePrivacy  # noqa: E402
from flip.nvflare.runtime import FLIP_CUSTOM_PROPS_KEY, FLIP_MODEL_ID_KEY  # noqa: E402


class TestFlipFedAvgRecipe:
    def test_builds_fed_job_with_flip_components(self):
        """A default recipe should construct a FedJob with the FLIP server + client wiring."""
        recipe = FlipFedAvgRecipe()
        assert recipe.job is not None
        assert recipe.job.name == "flip_fedavg"
        # min_clients propagates through to the underlying FedJobConfig.
        assert recipe.job.job.min_clients == 1

    def test_meta_props_carry_model_id_into_custom_props(self):
        """The FedJob's meta carries the FLIP model_id into custom_props so server/client
        components can resolve it lazily at first task."""
        recipe = FlipFedAvgRecipe()
        meta = recipe.job.job.meta_props
        assert FLIP_CUSTOM_PROPS_KEY in meta
        assert meta[FLIP_CUSTOM_PROPS_KEY][FLIP_MODEL_ID_KEY] == _DEV_MODEL_ID

    def test_custom_model_id_propagates_to_meta_props(self):
        custom_id = "abcdef01-2345-6789-abcd-ef0123456789"
        recipe = FlipFedAvgRecipe(model_id=custom_id)
        assert recipe.job.job.meta_props[FLIP_CUSTOM_PROPS_KEY][FLIP_MODEL_ID_KEY] == custom_id

    def test_train_script_normalisation(self):
        """Bare ``trainer.py`` is rewritten to ``custom/trainer.py``; explicit prefix kept."""
        assert FlipFedAvgRecipe(train_script="trainer.py").train_script == "custom/trainer.py"
        assert FlipFedAvgRecipe(train_script="custom/my.py").train_script == "custom/my.py"

    def test_overrides_are_applied(self):
        recipe = FlipFedAvgRecipe(
            num_rounds=10,
            min_clients=3,
            train_script="custom/my_trainer.py",
            train_args="--lr=0.001",
            percentile_privacy=PercentilePrivacy(gamma=1.0, percentile=80, off=True),
        )
        assert recipe.num_rounds == 10
        assert recipe.min_clients == 3
        assert recipe.train_script == "custom/my_trainer.py"
        assert recipe.train_args == "--lr=0.001"
        assert recipe.percentile_privacy == PercentilePrivacy(gamma=1.0, percentile=80, off=True)

    def test_percentile_privacy_defaults_are_stock(self):
        """Guard: DP defaults must stay stock-like (share the top 90%, clip per-step at 0.01).

        The old defaults (percentile=95, gamma=2.0) discarded 95% of every update under stock
        "largest percentile to share" semantics — with KeepOnlyVars active this reset the global
        head every round."""
        cfg = PercentilePrivacy()
        assert cfg.percentile == 10
        assert cfg.gamma == 0.01
        assert cfg.off is False

    def test_default_train_args_have_no_whitespace_values(self):
        """NVFlare's TaskScriptRunner whitespace-splits task_script_args, so any FLIP-API
        placeholder we pass via that channel must substitute into a single token. The default
        keeps only ``--project_id`` (a UUID) for this reason; the SQL query is plumbed via
        the client config's top-level ``query`` key instead."""
        recipe = FlipFedAvgRecipe()
        assert "--query" not in recipe.train_args
        assert recipe.train_args == "--project_id {project_id}"

    def test_export_writes_fed_job_layout(self, tmp_path: Path):
        """``recipe.export`` produces the standard NVFLARE FedJob layout under ``<job_dir>``.

        Uses a real ``nn.Linear`` stub for ``models.get_model`` because PTFileModelPersistor
        round-trips its ``model`` arg via FedJob's JSON serialiser at export time.
        """
        import torch

        sys.modules["models"].get_model = lambda: torch.nn.Linear(1, 1)
        try:
            recipe = FlipFedAvgRecipe()
            recipe.export(tmp_path)

            job_dir = tmp_path / recipe.job.name
            assert (job_dir / "meta.json").exists()
            # Server + client share the same config under a single ``app/`` since the recipe
            # treats all clients uniformly. FedJob picks this layout automatically.
            assert (job_dir / "app" / "config" / "config_fed_server.json").exists()
            assert (job_dir / "app" / "config" / "config_fed_client.json").exists()

            meta = json.loads((job_dir / "meta.json").read_text())
            assert meta[FLIP_CUSTOM_PROPS_KEY][FLIP_MODEL_ID_KEY] == _DEV_MODEL_ID

            # project_id / query / local_rounds are emitted as top-level client-config keys
            # (placeholders) so the template is self-documenting and {project_id} resolves;
            # fl-server's configure_client overwrites project_id/query at job-assembly.
            client_cfg = json.loads((job_dir / "app" / "config" / "config_fed_client.json").read_text())
            assert client_cfg["project_id"] == ""
            assert client_cfg["query"] == "SELECT * FROM Table;"
            assert client_cfg["local_rounds"] == 1

            server_cfg = json.loads((job_dir / "app" / "config" / "config_fed_server.json").read_text())
            evaluation_workflow = next(
                workflow for workflow in server_cfg["workflows"] if workflow["path"].endswith("GlobalModelEval")
            )
            assert evaluation_workflow["path"].startswith("nvflare.app_common.workflows")
            assert evaluation_workflow["path"].endswith("global_model_eval.GlobalModelEval")
            assert server_cfg["workflows"][-1]["path"].endswith("broadcast_task.BroadcastTask")
            assert set(server_cfg["task_result_filters"][0]["tasks"]) == {"validate", "post_validation"}
            executor_tasks = client_cfg["executors"][-1]["tasks"]
            assert executor_tasks == ["train", "validate"]
            assert "submit_model" not in executor_tasks
        finally:
            sys.modules["models"].get_model = lambda: object()

    def test_submit_model_opt_in_exports_cross_site_evaluation(self, tmp_path: Path):
        """Callers can still explicitly request the full client-model evaluation matrix."""
        import torch

        sys.modules["models"].get_model = lambda: torch.nn.Linear(1, 1)
        try:
            recipe = FlipFedAvgRecipe(submit_model_task_name="custom_submit")
            recipe.export(tmp_path)

            config_dir = tmp_path / recipe.job.name / "app" / "config"
            server_cfg = json.loads((config_dir / "config_fed_server.json").read_text())
            client_cfg = json.loads((config_dir / "config_fed_client.json").read_text())

            evaluation_workflow = next(
                workflow for workflow in server_cfg["workflows"] if workflow["path"].endswith("CrossSiteModelEval")
            )
            assert evaluation_workflow["path"].startswith("nvflare.app_common.workflows")
            assert evaluation_workflow["path"].endswith("cross_site_model_eval.CrossSiteModelEval")
            assert evaluation_workflow["args"]["submit_model_task_name"] == "custom_submit"
            assert client_cfg["executors"][-1]["tasks"] == ["train", "custom_submit", "validate"]
            assert set(server_cfg["task_result_filters"][0]["tasks"]) == {
                "custom_submit",
                "validate",
                "post_validation",
            }
        finally:
            sys.modules["models"].get_model = lambda: object()

    def test_persistor_is_initial_checkpoint(self, tmp_path: Path):
        """The FedAvg persistor is InitialCheckpointPTModelPersistor so a SERVER_CHECKPOINT-declaring
        job (e.g. Ark+ finetuning) seeds the round-0 global model server-side. It is a safe drop-in:
        with no SERVER_CHECKPOINT it behaves like the stock PTFileModelPersistor. model_id is resolved
        lazily from meta.json custom_props (like the other FLIP components), not passed as a component arg."""
        import torch

        sys.modules["models"].get_model = lambda: torch.nn.Linear(1, 1)
        try:
            recipe = FlipFedAvgRecipe()
            recipe.export(tmp_path)
            server_cfg = json.loads(
                (tmp_path / recipe.job.name / "app" / "config" / "config_fed_server.json").read_text()
            )
            persistor = next(c for c in server_cfg["components"] if c["id"] == "persistor")
            assert persistor["path"].endswith("InitialCheckpointPTModelPersistor")
        finally:
            sys.modules["models"].get_model = lambda: object()

    def test_write_client_config_params_is_noop_when_config_absent(self, tmp_path: Path):
        """If export produced no client config (unexpected layout), the param-write is a safe no-op."""
        recipe = FlipFedAvgRecipe()
        # tmp_path has no <job_name>/app/config/config_fed_client.json — must not raise.
        recipe._write_client_config_params(tmp_path)


def _train_filter_paths(cfg: dict, block_key: str) -> list[str]:
    """Flatten the filter component paths for the 'train' task from a task_*_filters block."""
    out: list[str] = []
    for block in cfg.get(block_key, []):
        if "train" in block.get("tasks", []):
            out.extend(f["path"] for f in block.get("filters", []))
    return out


class TestFlipFedAvgRecipeAggregateOnly:
    """Optional head-only (frozen-backbone finetuning) filter wiring."""

    def _export(self, tmp_path: Path, **kwargs):
        import torch

        sys.modules["models"].get_model = lambda: torch.nn.Linear(1, 1)
        try:
            recipe = FlipFedAvgRecipe(**kwargs)
            recipe.export(tmp_path)
            base = tmp_path / recipe.job.name / "app" / "config"
            return (
                json.loads((base / "config_fed_server.json").read_text()),
                json.loads((base / "config_fed_client.json").read_text()),
            )
        finally:
            sys.modules["models"].get_model = lambda: object()

    def test_no_regex_leaves_filters_empty(self, tmp_path: Path):
        """Default recipe (no regex) wires none of the head-only filters."""
        server_cfg, client_cfg = self._export(tmp_path)
        assert not any("TrimBroadcastVars" in p for p in _train_filter_paths(server_cfg, "task_data_filters"))
        assert not any("KeepOnlyVars" in p for p in _train_filter_paths(client_cfg, "task_result_filters"))
        assert not any("ReconstructFullModel" in p for p in _train_filter_paths(client_cfg, "task_data_filters"))

    def test_regex_wires_head_only_filters(self, tmp_path: Path):
        """aggregate_only_regex wires TrimBroadcastVars (server), ReconstructFullModel (client data)
        and KeepOnlyVars (client result, BEFORE PercentilePrivacy)."""
        server_cfg, client_cfg = self._export(tmp_path, aggregate_only_regex="omni_heads")
        assert any("TrimBroadcastVars" in p for p in _train_filter_paths(server_cfg, "task_data_filters"))
        assert any("ReconstructFullModel" in p for p in _train_filter_paths(client_cfg, "task_data_filters"))
        result_paths = _train_filter_paths(client_cfg, "task_result_filters")
        keep_i = next(i for i, p in enumerate(result_paths) if "KeepOnlyVars" in p)
        perc_i = next(i for i, p in enumerate(result_paths) if "PercentilePrivacy" in p)
        assert keep_i < perc_i, "KeepOnlyVars must run before PercentilePrivacy"

        # include_vars must round-trip through FedJob export. Regression guard: KeepOnlyVars/TrimBroadcastVars
        # store the regex only as self.pattern/self.skip, so FedJob's attribute-introspection export dropped
        # include_vars and the filters silently became no-ops (they now also store self.include_vars).
        def _filter_args(cfg, block_key, name):
            for block in cfg.get(block_key, []):
                if "train" in block.get("tasks", []):
                    for f in block.get("filters", []):
                        if name in f["path"]:
                            return f.get("args", {})
            return {}

        assert _filter_args(client_cfg, "task_result_filters", "KeepOnlyVars").get("include_vars") == "omni_heads"
        assert _filter_args(server_cfg, "task_data_filters", "TrimBroadcastVars").get("include_vars") == "omni_heads"


class TestFlipFedAvgRecipeBestModel:
    """Optional best-global-model selection via stock IntimeModelSelector (FLIP#673)."""

    def _export_server_cfg(self, tmp_path: Path, **kwargs) -> dict:
        import torch

        sys.modules["models"].get_model = lambda: torch.nn.Linear(1, 1)
        try:
            recipe = FlipFedAvgRecipe(**kwargs)
            recipe.export(tmp_path)
            base = tmp_path / recipe.job.name / "app" / "config"
            return json.loads((base / "config_fed_server.json").read_text())
        finally:
            sys.modules["models"].get_model = lambda: object()

    def test_default_wires_no_model_selector(self, tmp_path: Path):
        """Without best_model_metric no selector is wired, so no best checkpoint is ever saved."""
        server_cfg = self._export_server_cfg(tmp_path)
        assert not any("IntimeModelSelector" in c.get("path", "") for c in server_cfg["components"])

    def test_best_model_metric_wires_stock_intime_model_selector(self, tmp_path: Path):
        """best_model_metric wires stock IntimeModelSelector keyed on that metric.

        The selector averages the client-reported metric (evaluated on the received global model,
        carried in DXO meta INITIAL_METRICS from FLModel.metrics) across clients each round and
        fires GLOBAL_BEST_MODEL_AVAILABLE on improvement; the persistor answers by saving
        best_FL_global_model.pt alongside the final model, in the same persistence format.
        """
        server_cfg = self._export_server_cfg(tmp_path, best_model_metric="VAL_DICE")
        selector = next(c for c in server_cfg["components"] if "IntimeModelSelector" in c.get("path", ""))
        assert selector["path"] == "nvflare.app_common.widgets.intime_model_selector.IntimeModelSelector"
        assert selector["args"]["key_metric"] == "VAL_DICE"
        # FedJob export omits default-valued args; absent means stock's default (False).
        assert selector["args"].get("negate_key_metric", False) is False

    def test_best_model_metric_minimize_negates_key_metric(self, tmp_path: Path):
        """Loss-like metrics (lower is better) export with negate_key_metric=True."""
        server_cfg = self._export_server_cfg(tmp_path, best_model_metric="VAL_LOSS", best_model_metric_minimize=True)
        selector = next(c for c in server_cfg["components"] if "IntimeModelSelector" in c.get("path", ""))
        assert selector["args"]["negate_key_metric"] is True

    def test_best_model_metric_with_single_round_raises(self):
        """best_model_metric on a 1-round job fails fast instead of silently never saving a best model.

        IntimeModelSelector always skips round 0, so a single-round job can never fire it —
        mirrors the fl-api validate_config guard on the platform path.
        """
        with pytest.raises(ValueError, match="num_rounds >= 2"):
            FlipFedAvgRecipe(num_rounds=1, best_model_metric="VAL_DICE")

    def test_best_model_metric_with_two_rounds_constructs(self, tmp_path: Path):
        """num_rounds=2 is the minimum where selection can fire; the selector is wired normally."""
        server_cfg = self._export_server_cfg(tmp_path, num_rounds=2, best_model_metric="VAL_DICE")
        assert any("IntimeModelSelector" in c.get("path", "") for c in server_cfg["components"])
