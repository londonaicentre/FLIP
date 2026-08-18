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

"""Driver script: regenerate the committed evaluation app/config JSONs and meta.json.

Run from the flip-utils venv (needs flip + nvflare + torch); PTFileModelPersistor's
``model={"path": "models.get_model"}`` triggers a ``models`` import, so stub it first:

    cd flip-utils && uv run --no-sync python - <<'PY'
    import sys, types, torch, runpy
    m = types.ModuleType("models"); m.get_model = lambda: torch.nn.Linear(1, 1); sys.modules["models"] = m
    sys.argv = ["recipe.py", "--output", "../fl-apps/nvflare/evaluation"]
    runpy.run_path("../fl-apps/nvflare/evaluation/recipe.py", run_name="__main__")
    PY

Do NOT hand-edit the generated JSON files — regenerate them via this script after any recipe change
and commit the result.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from flip.nvflare.recipes import FlipEvalRecipe


def _copy_json_2space(src: Path, dest: Path) -> None:
    """Copy a JSON file, re-emitting it with 2-space indentation to match the fl-apps convention."""
    dest.write_text(json.dumps(json.loads(src.read_text()), indent=2) + "\n")


def main() -> None:
    """Export FlipEvalRecipe configs into the evaluation template directory."""
    parser = argparse.ArgumentParser(
        description="Regenerate the evaluation app/config JSONs and meta.json from FlipEvalRecipe."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent,
        help="Template dir (writes app/config/ and meta.json).",
    )
    args = parser.parse_args()

    dest_config = args.output / "app" / "config"
    dest_config.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        recipe = FlipEvalRecipe()
        recipe.export(tmp)
        exported_root = Path(tmp) / recipe.job.name
        exported_config = exported_root / "app" / "config"
        for name in ("config_fed_server.json", "config_fed_client.json"):
            _copy_json_2space(exported_config / name, dest_config / name)
        _copy_json_2space(exported_root / "meta.json", args.output / "meta.json")

    print(f"Wrote {dest_config}/config_fed_server.json")
    print(f"Wrote {dest_config}/config_fed_client.json")
    print(f"Wrote {args.output}/meta.json")


if __name__ == "__main__":
    main()
