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

"""Driver script: regenerate the committed diffusion_model app/config JSONs and meta.json.

Run from the flip-utils venv (needs the ``full`` extra — flip + nvflare + torch):

    cd flip-utils && uv run --no-sync python \\
        ../fl-apps/nvflare/diffusion_model/recipe.py \\
        --output ../fl-apps/nvflare/diffusion_model

Do NOT hand-edit the generated JSON files — regenerate them via this script after any
recipe change and commit the result.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import types
from pathlib import Path

# Pin the hash seed before importing NVFLARE (via flip): export_job serialises some task lists via
# sets, whose iteration order depends on PYTHONHASHSEED, so an unpinned seed yields spurious
# task-order diffs on every regeneration. The seed must be set before interpreter start, so re-exec
# once when it isn't already fixed.
if os.environ.get("PYTHONHASHSEED") != "0":
    os.environ["PYTHONHASHSEED"] = "0"
    os.execv(sys.executable, [sys.executable, *sys.argv])

import torch  # noqa: E402  (imported after the hash-seed pin)

# The base template ships no models.py (each app supplies its own), but constructing the recipe
# imports ``models`` via the persistor/locator ``model={"path": "models.get_model"}`` wiring — stub
# it so this script runs standalone. Only the import path lands in the generated JSON, never the
# stub itself (mirrors the recipe unit tests).
_stub_models = types.ModuleType("models")
_stub_models.get_model = lambda: torch.nn.Linear(1, 1)
sys.modules.setdefault("models", _stub_models)

from flip.nvflare.recipes import FlipDiffusionRecipe  # noqa: E402  (imported after the hash-seed pin)


def _copy_json(src: Path, dest: Path) -> None:
    """Copy a generated JSON file, guaranteeing exactly one trailing newline.

    NVFLARE's ``export_job`` emits ``config_fed_server.json`` / ``meta.json`` without a trailing
    newline. Normalising on copy keeps the committed templates stable across regenerations and
    satisfies the ``end-of-file-fixer`` pre-commit hook.
    """
    text = src.read_text()
    if not text.endswith("\n"):
        text += "\n"
    dest.write_text(text)


def main() -> None:
    """Export FlipDiffusionRecipe configs into the diffusion_model template directory."""
    parser = argparse.ArgumentParser(
        description="Regenerate the diffusion_model app/config JSONs and meta.json from FlipDiffusionRecipe."
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
        recipe = FlipDiffusionRecipe()
        recipe.export(tmp)
        exported_root = Path(tmp) / recipe.job.name
        exported_config = exported_root / "app" / "config"
        for name in ("config_fed_server.json", "config_fed_client.json"):
            _copy_json(exported_config / name, dest_config / name)
        _copy_json(exported_root / "meta.json", args.output / "meta.json")

    print(f"Wrote {dest_config}/config_fed_server.json")
    print(f"Wrote {dest_config}/config_fed_client.json")
    print(f"Wrote {args.output}/meta.json")


if __name__ == "__main__":
    main()
