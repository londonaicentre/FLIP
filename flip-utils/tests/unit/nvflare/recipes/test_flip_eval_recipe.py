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

from flip.nvflare.recipes import FlipEvalRecipe  # noqa: E402
from flip.nvflare.recipes.flip_eval_recipe import _DEV_MODEL_ID  # noqa: E402
from flip.nvflare.runtime import FLIP_CUSTOM_PROPS_KEY, FLIP_MODEL_ID_KEY  # noqa: E402


class TestFlipEvalRecipe:
    def test_builds_fed_job_with_flip_components(self):
        """A default recipe should construct a FedJob with the FLIP evaluation wiring."""
        recipe = FlipEvalRecipe()
        assert recipe.job is not None
        assert recipe.job.name == "flip_evaluation"
        assert recipe.job.job.min_clients == 1

    def test_meta_props_carry_model_id_into_custom_props(self):
        """The FedJob's meta carries the FLIP model_id into custom_props so components resolve it lazily."""
        recipe = FlipEvalRecipe()
        meta = recipe.job.job.meta_props
        assert FLIP_CUSTOM_PROPS_KEY in meta
        assert meta[FLIP_CUSTOM_PROPS_KEY][FLIP_MODEL_ID_KEY] == _DEV_MODEL_ID

    def test_custom_model_id_propagates_to_meta_props(self):
        custom_id = "abcdef01-2345-6789-abcd-ef0123456789"
        recipe = FlipEvalRecipe(model_id=custom_id)
        assert recipe.job.job.meta_props[FLIP_CUSTOM_PROPS_KEY][FLIP_MODEL_ID_KEY] == custom_id

    def test_eval_script_normalisation(self):
        """Bare ``evaluator.py`` is rewritten to ``custom/evaluator.py``; explicit prefix kept."""
        assert FlipEvalRecipe(eval_script="evaluator.py").eval_script == "custom/evaluator.py"
        assert FlipEvalRecipe(eval_script="custom/my.py").eval_script == "custom/my.py"

    def test_overrides_are_applied(self):
        recipe = FlipEvalRecipe(
            min_clients=3,
            eval_script="custom/my_evaluator.py",
            eval_args="--project_id {project_id}",
            validation_timeout=42,
            evaluate_task_name="validate",
        )
        assert recipe.min_clients == 3
        assert recipe.eval_script == "custom/my_evaluator.py"
        assert recipe.validation_timeout == 42
        assert recipe.evaluate_task_name == "validate"

    def test_default_eval_args_have_no_whitespace_values(self):
        """NVFlare's TaskScriptRunner whitespace-splits task_script_args, so the default keeps only
        ``--project_id`` (a UUID); the SQL query is plumbed via the client config's top-level ``query``."""
        recipe = FlipEvalRecipe()
        assert "--query" not in recipe.eval_args
        assert recipe.eval_args == "--project_id {project_id}"

    def test_export_writes_fed_job_layout(self, tmp_path: Path):
        """``recipe.export`` produces the standard NVFLARE FedJob layout with the FLIP evaluation wiring."""
        import torch

        sys.modules["models"].get_model = lambda: torch.nn.Linear(1, 1)
        try:
            recipe = FlipEvalRecipe()
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
            # The bespoke ModelEval/EvaluationPTModelLocator are NOT used — the recipe reuses the
            # shared cross-site validate path with the single-model locator + evaluation JSON generator.
            assert "GlobalModelEval" in server_blob
            assert "nvflare.app_common.workflows.global_model_eval.GlobalModelEval" in server_blob
            assert "ClientExceptionReporter" in server_blob
            assert "BroadcastTask" in server_blob
            assert "EvaluationModelLocator" in server_blob
            assert "EvaluationJsonGenerator" in server_blob
            assert "InitEvaluation" in server_blob
            # The bespoke ModelEval controller (fed_evaluation.ModelEval) and its multi-model
            # COLLECTION locator are not wired — note GlobalModelEval merely *contains* the
            # substring "ModelEval", so match the bespoke module path precisely.
            assert "fed_evaluation.ModelEval" not in server_blob
            assert "EvaluationPTModelLocator" not in server_blob

            client_cfg = json.loads(client_cfg_path.read_text())
            client_blob = json.dumps(client_cfg)
            # Client uses the stock Client-API executor for the validate task; no RUN_EVALUATOR.
            assert "InProcessClientAPIExecutor" in client_blob
            assert "RUN_EVALUATOR" not in client_blob
            # validate task is registered on the executor.
            executor_tasks = [t for e in client_cfg["executors"] for t in e["tasks"]]
            assert "validate" in executor_tasks
            assert "init_task" in executor_tasks  # CleanupImages init
            assert "post_validation" in executor_tasks  # CleanupImages post

            # project_id / query are emitted as top-level client-config placeholders.
            assert client_cfg["project_id"] == ""
            assert client_cfg["query"] == "SELECT * FROM Table;"
        finally:
            sys.modules["models"].get_model = lambda: object()

    def test_write_client_config_params_is_noop_when_config_absent(self, tmp_path: Path):
        """If export produced no client config (unexpected layout), the param-write is a safe no-op."""
        recipe = FlipEvalRecipe()
        recipe._write_client_config_params(tmp_path)
