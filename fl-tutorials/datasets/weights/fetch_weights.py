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

FL apps must not download anything at run time (FLIP#1206): the hub's FL server on a
platform-managed estate and a trust host behind an NHS firewall have no internet route, and a
run-time fetch sidesteps the scanned upload path — the reviewer approved the code, not
whatever a URL serves later. So the download happens HERE, on a developer machine, once, and
the result is a file the tutorial ships beside its code through the ordinary upload.

Today that is one network: torchvision's SqueezeNet, the perceptual-loss backbone of the
latent-diffusion tutorial. lpips resolves it through torch.hub, so it stays the ``.pth``
torchvision itself reads and the app drops it into a hub ``checkpoints/`` dir at run time
instead of downloading it. Add an entry to ``CHECKPOINTS`` for the next network.

Every checkpoint URL carries the first eight sha256 hex chars in its filename (torchvision's
convention) and is fetched with ``check_hash=True``, so a tampered or truncated download is
refused rather than staged.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from torch.hub import load_state_dict_from_url


@dataclass(frozen=True)
class Checkpoint:
    url: str
    sha256_prefix: str

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]


CHECKPOINTS: dict[str, Checkpoint] = {
    "squeezenet1_1": Checkpoint("https://download.pytorch.org/models/squeezenet1_1-b8a52dc0.pth", "b8a52dc0"),
}


def download(spec: Checkpoint, cache_dir: Path) -> Path:
    """Fetch (or reuse) the torchvision checkpoint, verifying the hash embedded in its name."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    # load_state_dict_from_url caches under model_dir and, with check_hash, refuses a file whose
    # sha256 does not start with the prefix in the filename. We want the FILE, so re-derive it.
    load_state_dict_from_url(spec.url, model_dir=str(cache_dir), check_hash=True, progress=False, map_location="cpu")
    path = cache_dir / spec.filename
    if not path.is_file():
        raise FileNotFoundError(f"expected torch.hub to leave {path} behind")
    return path


def stage(arch: str, cache_dir: Path, out_dir: Path) -> Path:
    """Stage ``arch``'s checkpoint under ``out_dir`` with its original name (torch.hub needs it)."""
    spec = CHECKPOINTS[arch]
    path = download(spec, cache_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / spec.filename
    shutil.copyfile(path, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("arch", choices=sorted(CHECKPOINTS), help="which checkpoint to stage")
    parser.add_argument("--cache-dir", type=Path, required=True, help="where raw torchvision downloads are kept")
    parser.add_argument("--out", type=Path, required=True, help="directory to drop the checkpoint into")
    args = parser.parse_args(argv)
    written = stage(args.arch, args.cache_dir, args.out)
    print(f"✅ {args.arch} → {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
