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

"""Convert the published MedicalNet checkpoint into this job's ``SERVER_CHECKPOINT``.

The FL server loads ``SERVER_CHECKPOINT`` into ``get_model()`` with ``strict=False``, so its keys
must be the model's own: ``perceptual.<backbone key>``. Run by ``make weights``.
"""

import argparse
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app_files"))
from medicalnet_perceptual import (  # noqa: E402
    MEDICALNET_FINGERPRINT,
    PERCEPTUAL_PREFIX,
    read_published_checkpoint,
    weights_fingerprint,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, required=True, help="The published MedicalNet .pth")
    parser.add_argument("--dst", type=Path, required=True, help="Where to write the SERVER_CHECKPOINT")
    args = parser.parse_args()

    state = read_published_checkpoint(args.src)
    fingerprint = weights_fingerprint(state)
    if fingerprint != MEDICALNET_FINGERPRINT:
        raise SystemExit(f"{args.src} converts to fingerprint {fingerprint}, expected {MEDICALNET_FINGERPRINT}.")
    torch.save({PERCEPTUAL_PREFIX + key: value for key, value in state.items()}, args.dst)
    print(f"Wrote {args.dst}: {len(state)} MedicalNet tensors, fingerprint {fingerprint[:12]}….")


if __name__ == "__main__":
    main()
