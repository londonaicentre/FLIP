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
#
# Adapted from
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/preprocess/calculate_dataset_fingerprint_segmentation.py

import argparse
import os
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import monai
from torch.utils.data import ConcatDataset

from custom_collate import patch_collate_fingerprint
from dataset import PicaiDataset
from dataset_fingerprint import DatasetFingerprintExtractor
from experiment_planner import ExperimentPlanner


def run_fingerprint_extractor() -> None:
    """Run the fingerprint extractor and the experiment planner over one or more sites."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-s",
        "--site-dir",
        type=Path,
        nargs="+",
        required=True,
        help="One or more data/prostate/sites/<CENTER> folders (each holding manifest.csv, nifti/, "
        "labels/, zonal_labels/). Passing several pools their studies into a single fingerprint, "
        "so every client can be given the same plan.",
    )
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument(
        "-m",
        "--modality",
        type=str,
        default="t2w",
        help="Which PI-CAI scan modality to fingerprint (t2w, adc, or hbv).",
    )
    parser.add_argument("-np", "--num-processes", type=int, default=8)
    parser.add_argument("-mem", "--gpu-memory-GB", type=int, default=8)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of images for processing, per site (for testing)",
    )
    args = parser.parse_args()

    site_datasets = []
    for site_dir in args.site_dir:
        site_dataset = PicaiDataset(
            site_dir,
            modality=args.modality,
            fingerprint=True,
        )
        if args.limit is not None:
            site_dataset.df = site_dataset.df.iloc[: args.limit]
        print(f"{site_dir.name}: {len(site_dataset)} studies")
        site_datasets.append(site_dataset)

    picai_dataset = ConcatDataset(site_datasets)
    print(f"Fingerprinting {len(picai_dataset)} studies across {len(site_datasets)} site(s)")

    dataloader = monai.data.DataLoader(
        picai_dataset,
        batch_size=1,
        collate_fn=patch_collate_fingerprint,
        num_workers=args.num_processes,
        pin_memory=True,
        shuffle=False,
    )

    fingerprint_extractor = DatasetFingerprintExtractor(
        output_folder=str(args.output_dir),
        dataloader=dataloader,
        channels=1,
        num_processes=args.num_processes,
        verbose=args.verbose,
    )

    fingerprint_extractor.run(overwrite_existing=True)

    print("Fingerprint extraction complete")

    print("Planning experiment")
    planner = ExperimentPlanner(
        fingerprint_dir=str(args.output_dir),
        output_folder=str(args.output_dir),
        dataloader=dataloader,
        num_channels=1,
        gpu_memory_target_in_gb=args.gpu_memory_GB,
    )
    ret = planner.plan_experiment()
    print(ret)

    # rename the dataset fingerprint file
    os.rename(
        os.path.join(args.output_dir, "dataset_fingerprint.json"),
        os.path.join(args.output_dir, "dataset_fingerprint_segmentation.json"),
    )

    # rename the experiment plan file
    os.rename(
        os.path.join(args.output_dir, "nnUNetPlans.json"),
        os.path.join(args.output_dir, "nnUNetPlans_segmentation.json"),
    )


if __name__ == "__main__":
    run_fingerprint_extractor()
