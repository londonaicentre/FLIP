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

"""Extract an autoencoder-only checkpoint from an `autoencoder`-tutorial run.

The latent diffusion job needs the autoencoder as a clean ``.pt`` state dict to declare as
``SERVER_CHECKPOINT``. A finished run of the `autoencoder` tutorial does not hand you one directly:

1. NVFLARE's ``PTFileModelPersistor`` saves ``persistence_manager.to_persistence_dict()``, so the
   state dict sits under a ``"model"`` key alongside meta/train_conf rather than at the top level.
2. That state dict covers the whole autoencoder *training* network — both ``autoencoder.*`` and the
   ``discriminator.*`` that adversarially trained it. The latent diffusion network has no
   discriminator.

This script does both steps: unwrap the envelope, keep only ``autoencoder.*``, and save the result.

Dropping ``discriminator.*`` is about file size and a clean load log, not correctness — the
persistor loads with ``strict=False``, so leaving them in would merely report them as unexpected
keys. What *is* load-bearing is the ``autoencoder.`` prefix itself: it must match the submodule name
in this tutorial's ``models.py``, or the load silently matches nothing.

Usage:
    python extract_autoencoder.py --src <autoencoder run's .pt> --dst ../app_files/pretrained_autoencoder.pt
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch

# The submodule prefix shared by the `autoencoder` tutorial's network and this one's. Both name the
# submodule `autoencoder`, which is what lets the checkpoint cross between them.
_KEEP_PREFIX = "autoencoder."


def extract(src: Path, dst: Path, keep_prefix: str = _KEEP_PREFIX) -> int:
    """Write an autoencoder-only state dict from an FL run's checkpoint.

    Args:
        src: The checkpoint produced by an `autoencoder` tutorial run (an NVFLARE persistor file, or
            a plain state dict).
        dst: Where to write the extracted autoencoder-only state dict.
        keep_prefix: State-dict key prefix to retain.

    Returns:
        int: Number of tensors written.

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

    dropped = len(state_dict) - len(cleaned)
    dst.parent.mkdir(parents=True, exist_ok=True)
    torch.save(cleaned, str(dst))
    print(f"  Saved {len(cleaned)} tensors to {dst} (dropped {dropped} non-autoencoder tensor(s)).")
    return len(cleaned)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--src",
        required=True,
        type=Path,
        help="Checkpoint from an `autoencoder` tutorial run (e.g. the downloaded FL global model).",
    )
    parser.add_argument(
        "--dst",
        required=True,
        type=Path,
        help="Output path for the autoencoder-only checkpoint (app_files/pretrained_autoencoder.pt).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    extract(args.src, args.dst)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
