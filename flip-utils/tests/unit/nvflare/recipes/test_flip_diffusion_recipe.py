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

from flip.nvflare.recipes import FlipDiffusionRecipe  # noqa: E402
from flip.nvflare.recipes.flip_diffusion_recipe import _DEV_MODEL_ID  # noqa: E402
from flip.nvflare.recipes.flip_fedavg_recipe import PercentilePrivacy  # noqa: E402
from flip.nvflare.runtime import FLIP_CUSTOM_PROPS_KEY, FLIP_MODEL_ID_KEY  # noqa: E402


class TestFlipDiffusionRecipe:
    def test_builds_fed_job_with_flip_components(self):
        """A default recipe should construct a FedJob with the FLIP latent-diffusion wiring."""
        recipe = FlipDiffusionRecipe()
        assert recipe.job is not None
        assert recipe.job.name == "flip_diffusion"
        assert recipe.job.job.min_clients == 1

    def test_meta_props_carry_model_id_into_custom_props(self):
        """The FedJob's meta carries the FLIP model_id into custom_props so components resolve it lazily."""
        recipe = FlipDiffusionRecipe()
        meta = recipe.job.job.meta_props
        assert FLIP_CUSTOM_PROPS_KEY in meta
        assert meta[FLIP_CUSTOM_PROPS_KEY][FLIP_MODEL_ID_KEY] == _DEV_MODEL_ID

    def test_custom_model_id_propagates_to_meta_props(self):
        custom_id = "abcdef01-2345-6789-abcd-ef0123456789"
        recipe = FlipDiffusionRecipe(model_id=custom_id)
        assert recipe.job.job.meta_props[FLIP_CUSTOM_PROPS_KEY][FLIP_MODEL_ID_KEY] == custom_id

    def test_train_script_normalisation(self):
        """Bare ``trainer.py`` is rewritten to ``custom/trainer.py``; explicit prefix kept."""
        assert FlipDiffusionRecipe(train_script="trainer.py").train_script == "custom/trainer.py"
        assert FlipDiffusionRecipe(train_script="custom/my.py").train_script == "custom/my.py"

    def test_overrides_are_applied(self):
        recipe = FlipDiffusionRecipe(
            num_rounds_ae=4,
            num_rounds_dm=6,
            min_clients=3,
            train_script="custom/my_trainer.py",
            validation_timeout=42,
            percentile_privacy=PercentilePrivacy(gamma=0.5, percentile=25, off=True),
        )
        assert recipe.num_rounds_ae == 4
        assert recipe.num_rounds_dm == 6
        assert recipe.min_clients == 3
        assert recipe.train_script == "custom/my_trainer.py"
        assert recipe.validation_timeout == 42
        assert recipe.percentile_privacy.gamma == 0.5
        assert recipe.percentile_privacy.percentile == 25
        assert recipe.percentile_privacy.off

    def test_default_train_args_have_no_whitespace_values(self):
        """NVFlare's TaskScriptRunner whitespace-splits task_script_args, so the default keeps only
        ``--project_id`` (a UUID); the SQL query is plumbed via the client config's top-level ``query``."""
        recipe = FlipDiffusionRecipe()
        assert "--query" not in recipe.train_args
        assert recipe.train_args == "--project_id {project_id}"

    def test_export_writes_fed_job_layout(self, tmp_path: Path):
        """``recipe.export`` produces the standard NVFLARE FedJob layout with the two-phase wiring."""
        import torch

        sys.modules["models"].get_model = lambda: torch.nn.Linear(1, 1)
        try:
            recipe = FlipDiffusionRecipe()
            recipe.export(tmp_path)

            job_dir = tmp_path / recipe.job.name
            assert (job_dir / "meta.json").exists()
            server_cfg_path = job_dir / "app" / "config" / "config_fed_server.json"
            client_cfg_path = job_dir / "app" / "config" / "config_fed_client.json"
            assert server_cfg_path.exists()
            assert client_cfg_path.exists()

            meta = json.loads((job_dir / "meta.json").read_text())
            assert meta[FLIP_CUSTOM_PROPS_KEY][FLIP_MODEL_ID_KEY] == _DEV_MODEL_ID

            server_cfg = json.loads(server_cfg_path.read_text())
            server_blob = json.dumps(server_cfg)
            assert "InitTraining" in server_blob
            assert "ClientExceptionReporter" in server_blob
            assert "PersistToS3AndCleanup" in server_blob
            assert "ValidationJsonGenerator" in server_blob
            assert "InitialPTModelLocator" in server_blob

            # Two-phase chain, in order: SAG-LDM(train_ae) → eval(validate_ae) → cleanup →
            # SAG-LDM(train_dm) → eval(validate_dm) → cleanup.
            workflow_paths = [w["path"] for w in server_cfg["workflows"]]
            sag_indices = [i for i, p in enumerate(workflow_paths) if p.endswith("ScatterAndGatherLDM")]
            eval_indices = [i for i, p in enumerate(workflow_paths) if p.endswith("GlobalModelEval")]
            broadcast_indices = [i for i, p in enumerate(workflow_paths) if p.endswith("BroadcastTask")]
            assert len(sag_indices) == 2
            assert len(eval_indices) == 2
            assert len(broadcast_indices) == 2
            assert sag_indices[0] < eval_indices[0] < broadcast_indices[0]
            assert broadcast_indices[0] < sag_indices[1] < eval_indices[1] < broadcast_indices[1]

            sag_ae, sag_dm = (server_cfg["workflows"][i] for i in sag_indices)
            assert sag_ae["args"]["train_task_name"] == "train_ae"
            assert sag_dm["args"]["train_task_name"] == "train_dm"
            # min_clients MUST be emitted even at its default: the fl-server's deploy-time
            # configure_server only overrides workflow args that exist in the exported config —
            # without the key a deployed job silently runs min_clients=1 and closes every round on
            # the fastest trust's update.
            assert sag_ae["args"]["min_clients"] == 1
            assert sag_dm["args"]["min_clients"] == 1
            assert sag_ae["args"]["num_rounds_ae"] == 1
            assert sag_ae["args"]["num_rounds_dm"] == 1
            # Phase-1 → phase-2 handoff: the DM phase seeds itself via the run-dir locator.
            assert sag_ae["args"]["model_locator_id"] == "model_locator_initial"
            assert sag_dm["args"]["model_locator_id"] == "model_locator"
            # The aggregator/persistor/shareable-generator ids are omitted from the SAG args when
            # they equal the controller defaults — those defaults must resolve against components
            # the recipe actually wired.
            component_ids = {c.get("id") for c in server_cfg["components"]}
            for required_id in ("persistor", "aggregator", "shareable_generator", "model_locator"):
                assert required_id in component_ids

            eval_ae, eval_dm = (server_cfg["workflows"][i] for i in eval_indices)
            assert eval_ae["args"]["validation_task_name"] == "validate_ae"
            assert eval_dm["args"]["validation_task_name"] == "validate_dm"

            client_cfg = json.loads(client_cfg_path.read_text())
            client_blob = json.dumps(client_cfg)
            # Client uses ONE stock Client-API executor for all four ML tasks; no RUN_TRAINER /
            # RUN_VALIDATOR executor pairs.
            assert "InProcessClientAPIExecutor" in client_blob
            assert "RUN_TRAINER" not in client_blob
            assert "RUN_VALIDATOR" not in client_blob
            executor_tasks = [t for e in client_cfg["executors"] for t in e["tasks"]]
            for task in ("train_ae", "train_dm", "validate_ae", "validate_dm"):
                assert task in executor_tasks
            assert "init_training" in executor_tasks  # CleanupImages init
            assert "post_validation" in executor_tasks  # CleanupImages post

            # The stage-aware DP filter guards both train tasks' results.
            privacy_blocks = [
                block
                for block in client_cfg.get("task_result_filters", [])
                if any("StagePercentilePrivacy" in f.get("path", "") for f in block.get("filters", []))
            ]
            assert len(privacy_blocks) == 1
            assert sorted(privacy_blocks[0]["tasks"]) == ["train_ae", "train_dm"]

            # Client event plumbing: FLIP event handler + SummaryWriter analytics relay.
            assert "ClientEventHandler" in client_blob
            assert "FlipAnalyticsBridge" in client_blob

            # project_id / query are emitted as top-level client-config placeholders.
            assert client_cfg["project_id"] == ""
            assert client_cfg["query"] == "SELECT * FROM Table;"
        finally:
            sys.modules["models"].get_model = lambda: object()

    def test_write_client_config_params_is_noop_when_config_absent(self, tmp_path: Path):
        """If export produced no client config (unexpected layout), the param-write is a safe no-op."""
        recipe = FlipDiffusionRecipe()
        recipe._write_client_config_params(tmp_path)
