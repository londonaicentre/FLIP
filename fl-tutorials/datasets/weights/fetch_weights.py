# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Fetch a pretrained network a tutorial needs and stage it as a plain file.

FL apps never download at run time (FLIP#1206 — see the user guide's *Model Files* section for
why). So the download happens HERE, on a developer machine, once, and the result is a file the
tutorial ships beside its code through the ordinary scanned upload.

Today that is one network: torchvision's SqueezeNet, the perceptual-loss backbone of the
latent-diffusion tutorial. lpips resolves it through torch.hub, so it stays the ``.pth``
torchvision itself reads and the app drops it into a hub ``checkpoints/`` dir at run time
instead of downloading it. Add an entry to ``CHECKPOINTS`` for the next network.

Every checkpoint URL carries the first eight sha256 hex chars in its filename (torch.hub's
convention, ``HASH_REGEX``), and the file is fetched with that prefix as ``hash_prefix`` — the
same check torch.hub applies to its own downloads — so a tampered or truncated download is
refused rather than staged. A file already in ``--out`` is re-hashed before it is reused, which
is more than torch.hub does for its cache.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from torch.hub import HASH_REGEX, download_url_to_file


@dataclass(frozen=True)
class Checkpoint:
    url: str

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]

    @property
    def hash_prefix(self) -> str:
        """The sha256 prefix torch.hub embeds in the filename (``name-<hex>.pth``)."""
        match = HASH_REGEX.search(self.filename)
        if match is None:
            raise ValueError(f"{self.filename} carries no hash prefix; only hash-named checkpoints are stageable")
        return match.group(1)


CHECKPOINTS: dict[str, Checkpoint] = {
    "squeezenet1_1": Checkpoint("https://download.pytorch.org/models/squeezenet1_1-b8a52dc0.pth"),
}


def verify(path: Path, hash_prefix: str) -> None:
    """Raise unless ``path``'s sha256 starts with ``hash_prefix``."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not digest.startswith(hash_prefix):
        raise RuntimeError(
            f"{path} has sha256 {digest[: len(hash_prefix)]}…, expected {hash_prefix}…; delete it and re-run"
        )


def stage(arch: str, out_dir: Path) -> Path:
    """Put ``arch``'s checkpoint at ``out_dir/<its torchvision filename>``, downloading if needed."""
    spec = CHECKPOINTS[arch]
    target = out_dir / spec.filename
    if target.is_file():
        verify(target, spec.hash_prefix)
        return target
    out_dir.mkdir(parents=True, exist_ok=True)
    # Download to a sibling and rename into place, so an interrupted fetch never leaves a
    # partial file under the final name for the next run (or `make`) to trust.
    partial = target.with_name(target.name + ".part")
    download_url_to_file(spec.url, str(partial), hash_prefix=spec.hash_prefix, progress=False)
    os.replace(partial, target)
    return target


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("arch", choices=sorted(CHECKPOINTS), help="which checkpoint to stage")
    parser.add_argument("--out", type=Path, required=True, help="directory to drop the checkpoint into")
    args = parser.parse_args(argv)
    print(f"✅ {args.arch} → {stage(args.arch, args.out)}")


if __name__ == "__main__":
    main()
