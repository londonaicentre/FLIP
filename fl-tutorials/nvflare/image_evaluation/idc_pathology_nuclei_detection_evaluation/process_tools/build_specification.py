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
"""Build the detector-specification checkpoint the FL server broadcasts.

``EvaluationModelLocator`` loads ``config.json['models'][*]['checkpoint']`` server-side and broadcasts
its weights over the ``validate`` task. For this tutorial the "weights" are the detector's physical
parameters, so the checkpoint is generated from ``config.json`` rather than downloaded -- edit the
parameters there and re-run this to keep the two in step.

It is generated rather than committed for the same reason a lockfile is not hand-written: it is
derived, and a stale copy that disagreed with ``config.json`` would silently evaluate parameters
nobody chose.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch

_APP_FILES_DIR = Path(__file__).resolve().parents[1] / "app_files"
sys.path.insert(0, str(_APP_FILES_DIR))

from models import get_model  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=_APP_FILES_DIR / "detector_specification.pt")
    args = parser.parse_args()

    model = get_model()
    state_dict = model.state_dict()
    torch.save(state_dict, args.output)
    print(f"Wrote {args.output} with parameters:")
    for name, value in sorted(state_dict.items()):
        print(f"  {name:24s} {float(value):g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
