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
        finally:
            sys.modules["models"].get_model = lambda: object()

    def test_write_client_config_params_is_noop_when_config_absent(self, tmp_path: Path):
        """If export produced no client config (unexpected layout), the param-write is a safe no-op."""
        recipe = FlipFedAvgRecipe()
        # tmp_path has no <job_name>/app/config/config_fed_client.json — must not raise.
        recipe._write_client_config_params(tmp_path)
