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

"""Preprocess Ark+ checkpoints for the evaluation pipeline.

Produces clean state dict files that match the raw ArkSwinTransformer
architecture exactly, so the evaluator's ``strict`` ``load_state_dict`` on the
client side accepts the broadcast weights.

Usage::

    # Example
    python preprocess_checkpoints.py \\
        --entry /raw/Ark6_swinLarge768_ep50.pth.tar /clean/pretrained.pt arkplus_pretrained
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch

_APP_FILES = Path(__file__).resolve().parents[1] / "app_files"
sys.path.insert(0, str(_APP_FILES))

from checkpoint_utils import load_arkplus_state_dict  # noqa: E402
from models import _build_arkplus_raw  # noqa: E402

# ---------------------------------------------------------------------------
# Processor registry
# ---------------------------------------------------------------------------
PROCESSORS: dict[str, dict] = {
    "arkplus_pretrained": {
        "description": "Foundation model (6 heads: 14,14,14,3,6,1)",
        "model_cfg": {
            "MODEL_NAME": "swin_large_384",
            "INPUT_SIZE": 768,
            "PROJECTOR_FEATURES": 1376,
            "USE_MLP": False,
            "NUM_CLASSES_LIST": [14, 14, 14, 3, 6, 1],
        },
        "extract": lambda ckpt: ckpt["teacher"],
    },
    # # Fine-tuned model processing is no longer needed — the training app
    # # produces clean checkpoints directly.
    # "arkplus_finetuned": {
    #     "description": "Fine-tuned model (1 head: 5 classes)",
    #     "model_cfg": {
    #         "MODEL_NAME": "swin_large_384",
    #         "INPUT_SIZE": 768,
    #         "PROJECTOR_FEATURES": 1376,
    #         "USE_MLP": False,
    #         "NUM_CLASSES_LIST": [5],
    #     },
    #     "extract": lambda ckpt: ckpt.get("model", ckpt),
    # },
}


def preprocess(src: Path, dst: Path, config_key: str) -> None:
    """Preprocess a checkpoint and save a clean state dict."""
    if config_key not in PROCESSORS:
        raise KeyError(f"Unknown config key {config_key!r}. Available: {list(PROCESSORS.keys())}")

    if not src.is_file():
        raise FileNotFoundError(f"Raw checkpoint not found: {src}")

    proc = PROCESSORS[config_key]
    print(f"  [{config_key}] {proc['description']}")

    model = _build_arkplus_raw(proc["model_cfg"])

    print(f"  Loading {src} ...")
    ckpt = torch.load(str(src), map_location="cpu", weights_only=False)
    state_dict = proc["extract"](ckpt)

    print("  Processing weights ...")
    load_arkplus_state_dict(model, state_dict, {"load_backbone_only": False})

    torch.save(model.state_dict(), str(dst))
    n = len(torch.load(str(dst), map_location="cpu", weights_only=True))
    print(f"  Saved {n} tensors to {dst}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preprocess Ark+ checkpoints for evaluation.")
    parser.add_argument(
        "--entry",
        action="append",
        nargs=3,
        required=True,
        metavar=("SRC", "DST", "CONFIG_KEY"),
        help="Triplet: source checkpoint, output path, processor key. Repeatable.  Available keys: {}".format(
            ", ".join(PROCESSORS.keys())
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    for src, dst, config_key in args.entry:
        preprocess(Path(src), Path(dst), config_key)

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
