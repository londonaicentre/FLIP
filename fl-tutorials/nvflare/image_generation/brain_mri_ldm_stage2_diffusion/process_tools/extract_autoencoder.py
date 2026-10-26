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

"""Extract the autoencoder from a `brain_mri_ldm_stage1_autoencoder` tutorial run, for this tutorial to train against.

A run's ``FL_global_model.pt`` holds the whole training network (autoencoder and discriminator)
inside FLARE's persistor envelope; the published autoencoder is a ``.safetensors`` of the same
tensors. This keeps the ``autoencoder.*`` tensors, checks that they load into the architecture the
run was trained with, and writes the two files the job uploads together:

* ``pretrained_autoencoder.pt``: the weights, loaded once on the FL server (``SERVER_CHECKPOINT``);
* ``autoencoder_config.yaml``: the architecture every client builds, and a fingerprint of the
  weights that the trainer checks against what it receives.

Usage:
    python extract_autoencoder.py --src <the run's .pt> --dst ../app_files/pretrained_autoencoder.pt \\
        [--autoencoder-config <the config.json that run was trained with>]
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import torch
import yaml
from monai.networks.nets import AutoencoderKL
from safetensors.torch import load_file

TUTORIALS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TUTORIALS / "brain_mri_ldm_stage2_diffusion" / "app_files"))
from latent_utils import autoencoder_fingerprint  # noqa: E402

PREFIX = "autoencoder."
CONFIG_NAME = "autoencoder_config.yaml"
HEADER = (
    "# The frozen autoencoder in pretrained_autoencoder.pt: the architecture every client builds, and the\n"
    "# fingerprint of its weights. Written by process_tools/extract_autoencoder.py; regenerate, don't edit.\n"
)


def autoencoder_kwargs(net_config: dict) -> dict:
    """The `brain_mri_ldm_stage1_autoencoder` tutorial's own mapping from its config to ``AutoencoderKL`` arguments."""
    # Loaded under its own name: this tutorial has a models.py too.
    models_py = TUTORIALS / "brain_mri_ldm_stage1_autoencoder" / "app_files" / "models.py"
    spec = importlib.util.spec_from_file_location("autoencoder_models", models_py)
    models = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(models)
    return models.autoencoder_kwargs(net_config)


def extract(src: Path) -> dict[str, torch.Tensor]:
    """The ``autoencoder.*`` tensors of a run's checkpoint, out of FLARE's persistor envelope."""
    if src.suffix == ".safetensors":
        state_dict = load_file(src)
    else:
        # weights_only=False: the envelope pickles FLARE metadata beside the tensors. Only point this
        # at a checkpoint you produced.
        blob = torch.load(src, map_location="cpu", weights_only=False)
        state_dict = blob["model"] if isinstance(blob.get("model"), dict) else blob
    tensors = {key: value for key, value in state_dict.items() if key.startswith(PREFIX)}
    if not tensors:
        raise KeyError(
            f"No {PREFIX!r} keys in {src}: not a checkpoint of the `brain_mri_ldm_stage1_autoencoder` tutorial."
        )
    print(f"Kept {len(tensors)} autoencoder tensors, dropped {len(state_dict) - len(tensors)}.")
    return tensors


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--src", type=Path, required=True, help="The `brain_mri_ldm_stage1_autoencoder` run's checkpoint."
    )
    parser.add_argument("--dst", type=Path, required=True, help="Where to write pretrained_autoencoder.pt.")
    parser.add_argument(
        "--autoencoder-config",
        type=Path,
        default=TUTORIALS / "brain_mri_ldm_stage1_autoencoder" / "app_files" / "config.json",
        help="The config.json the run was trained with (default: the autoencoder tutorial's own).",
    )
    args = parser.parse_args(argv)

    tensors = extract(args.src)
    architecture = autoencoder_kwargs(json.loads(args.autoencoder_config.read_text())["net_config"])
    # A strict load: weights from another run or another config fail here, not as noise in training.
    AutoencoderKL(**architecture).load_state_dict({key.removeprefix(PREFIX): value for key, value in tensors.items()})

    args.dst.parent.mkdir(parents=True, exist_ok=True)
    torch.save(tensors, args.dst)
    body = yaml.safe_dump({"autoencoder": architecture}, sort_keys=False)
    fingerprint = f"autoencoder_sha256: {autoencoder_fingerprint(tensors)}  # pragma: allowlist secret\n"
    (args.dst.parent / CONFIG_NAME).write_text(HEADER + body + fingerprint)
    print(f"Wrote {args.dst} and {CONFIG_NAME} beside it.")


if __name__ == "__main__":
    main()
