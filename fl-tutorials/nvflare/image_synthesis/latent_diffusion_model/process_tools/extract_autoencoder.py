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

"""Extract an autoencoder-only checkpoint, plus the config describing it, from an `autoencoder` run.

The latent diffusion job needs the autoencoder as a clean ``.pt`` state dict to declare as
``SERVER_CHECKPOINT``. A finished run of the `autoencoder` tutorial does not hand you one directly:

1. NVFLARE's ``PTFileModelPersistor`` saves ``persistence_manager.to_persistence_dict()``, so the
   state dict sits under a ``"model"`` key alongside meta/train_conf rather than at the top level.
2. That state dict covers the whole autoencoder *training* network — both ``autoencoder.*`` and the
   ``discriminator.*`` that adversarially trained it. The latent diffusion network has no
   discriminator.

So this script unwraps the envelope and keeps only ``autoencoder.*``. Dropping ``discriminator.*``
is about file size and a clean load log, not correctness — the persistor loads with
``strict=False``, so leaving them in would merely report them as unexpected keys.

**Weights alone are not a usable artifact**, which is the other half of this script. A state dict
does not say what network it belongs to, and ``strict=False`` means a wrong guess is tolerated
rather than raised: the diffusion model would train against a randomly-initialised encoder while
reporting entirely plausible losses. Worse, some of the architecture is invisible to any check you
could make after the fact — ``norm_num_groups`` alters how GroupNorm computes without altering one
parameter shape, so a network built with the wrong value loads the checkpoint *cleanly* under
``strict=True`` and merely produces wrong latents.

The fix is to stop guessing. Alongside the ``.pt`` this writes an ``autoencoder_config.yaml``
recording the architecture, taken from the `autoencoder` tutorial's own
``models.autoencoder_kwargs`` rather than transcribed from its ``config.json`` — what the network
was *built* with, not what a config file claims. The latent tutorial's ``models.py`` reads that YAML
to define its frozen autoencoder, so the architecture has exactly one definition and travels with
the weights it describes.

Before writing anything, the recorded architecture is instantiated and compared against the
extracted tensors, key by key and shape by shape. That is what turns "wrong checkpoint" from a
silent success into a refusal here. Its one blind spot is the class of arguments that do not touch
parameter shapes (``norm_num_groups``, ``norm_eps``): those are trustworthy because they are read
from the network definition, not because this check confirms them.

Usage:
    python extract_autoencoder.py --src <autoencoder run's .pt> --dst ../app_files/pretrained_autoencoder.pt

Writes ``autoencoder_config.yaml`` next to ``--dst`` unless ``--config-out`` says otherwise, so the
checkpoint and its config stay together wherever you stage them.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import torch
import yaml
from monai.networks.nets import AutoencoderKL

# The submodule prefix shared by the `autoencoder` tutorial's network and this one's. Both name the
# submodule `autoencoder`, which is what lets the checkpoint cross between them.
_KEEP_PREFIX = "autoencoder."

# The sibling tutorial that produces these checkpoints. Its models.py owns the architecture.
_AUTOENCODER_TUTORIAL = Path(__file__).resolve().parents[2] / "autoencoder"

# Bumped if the YAML layout changes in a way a reader must notice. models.py checks it.
_SCHEMA_VERSION = 1

_CONFIG_NAME = "autoencoder_config.yaml"


def _load_autoencoder_models(tutorial: Path) -> Any:
    """Import the `autoencoder` tutorial's ``models`` module.

    Imported rather than duplicated so the architecture keeps one definition. Note this instantiates
    that module's network singleton as a side effect (a few seconds on CPU) — harmless here, and not
    worth restructuring the tutorial to avoid.
    """
    models_path = tutorial / "app_files" / "models.py"
    if not models_path.is_file():
        raise FileNotFoundError(
            f"Cannot find the autoencoder tutorial's models.py at {models_path}. It defines the "
            "architecture recorded in the config; pass --autoencoder-tutorial if it lives elsewhere."
        )
    spec = importlib.util.spec_from_file_location("_flip_autoencoder_models", models_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"Could not load a module spec from {models_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def describe_autoencoder(tutorial: Path = _AUTOENCODER_TUTORIAL) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(architecture, training_context)`` for the `autoencoder` tutorial's network.

    The architecture is the literal kwargs its ``models.py`` passes to ``AutoencoderKL``. The
    training context is the data geometry that shaped the weights — not needed to build the network,
    recorded because the latent geometry is only reproducible with it.
    """
    models = _load_autoencoder_models(tutorial)
    with open(tutorial / "app_files" / "config.json") as handle:
        config = json.load(handle)
    architecture = models.autoencoder_kwargs(config["net_config"])
    training = {
        "spatial_shape": config["spatial_shape"],
        "modalities": config.get("MODALITIES"),
    }
    return architecture, training


def verify_against_checkpoint(architecture: dict[str, Any], tensors: dict[str, torch.Tensor]) -> None:
    """Refuse an architecture that does not describe these tensors.

    Builds the network and compares state-dict keys and shapes. Catches the whole family of "this
    is not the checkpoint you think it is" errors — a stale file, a different channel ladder, a
    checkpoint from another tutorial.

    Raises:
        ValueError: on any missing key, unexpected key, or shape disagreement.
    """
    reference = AutoencoderKL(**architecture).state_dict()
    stripped = {key[len(_KEEP_PREFIX) :]: value for key, value in tensors.items()}

    missing = sorted(set(reference) - set(stripped))
    unexpected = sorted(set(stripped) - set(reference))
    mismatched = {
        key: (tuple(reference[key].shape), tuple(stripped[key].shape))
        for key in sorted(set(reference) & set(stripped))
        if reference[key].shape != stripped[key].shape
    }
    if not (missing or unexpected or mismatched):
        print(f"  Verified: the recorded architecture builds all {len(reference)} tensors, shapes included.")
        return

    report = [
        "The recorded architecture does not describe this checkpoint, so the config would be a lie "
        "and the frozen autoencoder would load as noise. Nothing was written.",
        f"  missing from the checkpoint: {len(missing)}",
        f"  unexpected in the checkpoint: {len(unexpected)}",
        f"  shape disagreements: {len(mismatched)}",
    ]
    for key, (want, got) in list(mismatched.items())[:5]:
        report.append(f"    {key}: architecture says {want}, checkpoint has {got}")
    if len(mismatched) > 5:
        report.append(f"    ... and {len(mismatched) - 5} more")
    report.append(
        "Most likely the checkpoint came from a different autoencoder run than the tutorial's "
        "current config.json describes."
    )
    raise ValueError("\n".join(report))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_config(
    path: Path,
    architecture: dict[str, Any],
    training: dict[str, Any],
    source: Path,
    tensor_count: int,
) -> None:
    """Write the YAML describing an extracted checkpoint.

    Deliberately free of timestamps: regenerating from the same checkpoint reproduces the file
    byte-for-byte, so a diff on this file always means the architecture or the source weights
    actually changed.
    """
    document = {
        "schema_version": _SCHEMA_VERSION,
        "autoencoder": architecture,
        "training": training,
        "source": {
            "checkpoint": source.name,
            "sha256": _sha256(source),
            "autoencoder_tensors": tensor_count,
        },
    }
    header = (
        "# The frozen autoencoder that accompanies pretrained_autoencoder.pt.\n"
        "#\n"
        "# Generated by latent_diffusion_model/process_tools/extract_autoencoder.py, which takes the\n"
        "# `autoencoder` block from that tutorial's models.autoencoder_kwargs and checks it against\n"
        "# the checkpoint's own tensors before writing. Do not hand-edit: the point of this file is\n"
        "# that the architecture has one definition and travels with the weights it describes.\n"
        "#\n"
        "# `training` does not build the network. It records the data geometry the weights were fit\n"
        "# to, without which the latent shape is not reproducible.\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as handle:
        handle.write(header)
        yaml.safe_dump(document, handle, sort_keys=False, default_flow_style=False)
    print(f"  Wrote {path}")


def extract(src: Path, dst: Path, keep_prefix: str = _KEEP_PREFIX) -> dict[str, torch.Tensor]:
    """Return the autoencoder-only state dict inside an FL run's checkpoint.

    Args:
        src: The checkpoint produced by an `autoencoder` tutorial run (an NVFLARE persistor file, or
            a plain state dict).
        dst: Where the caller intends to write it. Used only for messages; nothing is written here.
        keep_prefix: State-dict key prefix to retain.

    Returns:
        The retained tensors, still carrying ``keep_prefix``.

    Raises:
        KeyError: if no key carries ``keep_prefix`` — the checkpoint is not from the expected network,
            and saving an empty file would fail silently later (the persistor's load is
            ``strict=False``).
    """
    print(f"  Loading {src} ...")
    # weights_only=False: an NVFLARE persistor checkpoint carries pickled meta/train_conf alongside
    # the tensors. Only ever point this at a checkpoint you produced yourself.
    blob = torch.load(str(src), map_location="cpu", weights_only=False)

    if isinstance(blob, dict) and "model" in blob and isinstance(blob["model"], dict):
        # NVFLARE persistor envelope: {"model": state_dict, "meta": ..., "train_conf": ...}
        state_dict = blob["model"]
        print("  Unwrapped NVFLARE persistor envelope (state dict was under the 'model' key).")
    else:
        state_dict = blob
        print("  Treating the file as a plain state dict (no 'model' envelope found).")

    cleaned = {k: v for k, v in state_dict.items() if k.startswith(keep_prefix)}
    if not cleaned:
        raise KeyError(
            f"No keys starting with {keep_prefix!r} in {src}. Found prefixes: "
            f"{sorted({k.split('.')[0] for k in state_dict})}. This checkpoint does not look like an "
            "`autoencoder` tutorial run — saving it would produce a checkpoint that loads nothing."
        )
    print(f"  Kept {len(cleaned)} autoencoder tensor(s), dropped {len(state_dict) - len(cleaned)}.")
    return cleaned


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--src",
        type=Path,
        default=None,
        help="Checkpoint from an `autoencoder` tutorial run (e.g. the downloaded FL global model).",
    )
    parser.add_argument(
        "--dst",
        required=True,
        type=Path,
        help="Output path for the autoencoder-only checkpoint (app_files/pretrained_autoencoder.pt).",
    )
    parser.add_argument(
        "--config-out",
        type=Path,
        default=None,
        help=f"Where to write the architecture YAML. Defaults to {_CONFIG_NAME} beside --dst.",
    )
    parser.add_argument(
        "--autoencoder-tutorial",
        type=Path,
        default=_AUTOENCODER_TUTORIAL,
        help="The `autoencoder` tutorial directory whose models.py defines the architecture.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help=(
            "Check that an already-prepared --dst and its YAML agree, and exit. Writes nothing and "
            "does not need --src. Used by `make prepare-checkpoint` so a pre-existing checkpoint is "
            "trusted because it was checked, not because the file happened to be there."
        ),
    )
    args = parser.parse_args(argv)
    if args.src is None and not args.verify_only:
        parser.error("--src is required unless --verify-only is given")
    return args


def verify_prepared(dst: Path, config_out: Path) -> int:
    """Check a prepared checkpoint against the architecture recorded beside it.

    The pair is what the job consumes, and it can go stale asymmetrically: replacing one file and not
    the other leaves a checkpoint whose weights the persistor silently declines to load. Nothing at
    run time notices, so it is noticed here.
    """
    for path, what in ((dst, "checkpoint"), (config_out, "architecture config")):
        if not path.is_file():
            print(f"❌ Missing {what}: {path}")
            return 1
    with open(config_out) as handle:
        document = yaml.safe_load(handle)
    architecture = document["autoencoder"]
    tensors = torch.load(str(dst), map_location="cpu", weights_only=True)
    if not any(key.startswith(_KEEP_PREFIX) for key in tensors):
        # A prepared checkpoint is already autoencoder-only and still carries the prefix; without it
        # the persistor's strict=False load would match nothing.
        print(f"❌ {dst} has no {_KEEP_PREFIX!r} keys, so the persistor would load none of it.")
        return 1
    verify_against_checkpoint(architecture, tensors)
    print(f"✅ {dst.name} matches the architecture in {config_out.name}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_out = args.config_out or args.dst.parent / _CONFIG_NAME

    if args.verify_only:
        return verify_prepared(args.dst, config_out)

    tensors = extract(args.src, args.dst)
    architecture, training = describe_autoencoder(args.autoencoder_tutorial)
    # Before writing either file, so a mismatch leaves no half-built artifact behind.
    verify_against_checkpoint(architecture, tensors)

    args.dst.parent.mkdir(parents=True, exist_ok=True)
    torch.save(tensors, str(args.dst))
    print(f"  Wrote {args.dst}")
    write_config(config_out, architecture, training, args.src, len(tensors))
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
