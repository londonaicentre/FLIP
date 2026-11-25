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

"""Turn the fingerprint job's downloaded model into the planner's ``dataset_fingerprint.json``.

Host-side, after the job: ``python export_fingerprint.py <model.pt> --output <dataset_fingerprint.json>``.
The segmentation tutorial's ``make plan FINGERPRINT=<model.pt>`` runs this and then the planner.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from app.fingerprint import STATE_KEYS, pooled_case_count, pooled_fingerprint
from app.models import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model", type=Path, help="The fingerprint job's final model (a torch state dict).")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()

    state_dict = torch.load(args.model, map_location="cpu", weights_only=True)
    state = {key: state_dict[key].numpy() for key in STATE_KEYS}
    fingerprint = pooled_fingerprint(state, load_config())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fingerprint))
    intensity = fingerprint["foreground_intensity_properties_per_channel"]["0"]
    print(f"cases: {pooled_case_count(state['site_cases'])}")
    print(f"median spacing (d, h, w): {np.median(fingerprint['spacings'], axis=0).tolist()}")
    print(f"median shape after crop (d, h, w): {np.median(fingerprint['shapes_after_crop'], axis=0).tolist()}")
    print(f"foreground intensity mean / std: {intensity['mean']:.1f} / {intensity['std']:.1f}")
    print(f"written to {args.output}")


if __name__ == "__main__":
    main()
