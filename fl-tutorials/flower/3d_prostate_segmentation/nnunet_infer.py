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
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/inference/nnunet_infer.py

import argparse
import os
from pathlib import Path

import monai.transforms as mt
import pandas as pd
import torch
from batchgenerators.utilities.file_and_folder_operations import load_json
from monai.data import PatchIterd, list_data_collate
from monai.losses import DiceCELoss
from monai.transforms import Compose
from monai.utils import set_determinism
from nnunetv2.utilities.plans_handling.plans_handler import PlansManager
from torch.utils.data import ConcatDataset, DataLoader

from dataset import AXCODES, IMAGE_KEY, PicaiDataset
from network import build_network_architecture
from preprocess import build_case_transform
from train_helpers import (
    generate_predictions,
    inference_func,
    init_logger,
    possible_patch_size,
    seed_torch,
)


def infer_loop():
    """Run inference for the trained PICAI prostate segmentation model over a held-out cohort.

    Parses CLI args, builds the test set from one or more site folders, builds the network from the
    nnU-Net plans file and loads the checkpoint(s) nnunet_train.py saved, reports Dice/HD95/region
    test metrics, and saves each case's prediction as a NIfTI in its own native space under
    `predictions/{exp_name}/`.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-s",
        "--site-dir",
        type=Path,
        nargs="+",
        required=True,
        help="One or more data/prostate/sites/<CENTER> folders to treat as the held-out test set. "
        "Point this at sites the model was not trained on.",
    )
    parser.add_argument(
        "--modality", type=str, default="t2w", choices=["t2w", "adc", "hbv"]
    )
    parser.add_argument("-conf", "--config", type=str, required=True)
    parser.add_argument("-nw", "--num_workers", type=int, required=True)
    parser.add_argument("--exp_name", type=str, required=True)
    parser.add_argument(
        "--debug",
        type=bool,
        default=False,
        help="Limit number of images for processing (for testing)",
    )
    args = parser.parse_args()

    exp_name = args.exp_name

    # Run outputs land beside this script, not two levels up in fl-tutorials/.
    base_dir = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(base_dir, "logs")
    model_weights_dir = os.path.join(base_dir, "model_weights")
    predictions_dir = os.path.join(base_dir, "predictions", exp_name)

    os.makedirs(logs_dir, exist_ok=True)
    os.makedirs(predictions_dir, exist_ok=True)

    LOG_FILE = os.path.join(logs_dir, f"{exp_name}_infer.log")
    LOGGER = init_logger(LOG_FILE)
    LOGGER.info(f"Experiment name: {exp_name}_inference")

    config = load_json(args.config)
    config["deep_supervision"] = (
        False  # inference always runs without deep supervision heads
    )
    config["batch_size"] = (
        1  # one volume per batch; its patch grid is the effective batch
    )

    # Written by calculate_dataset_fingerprint_segmentation.py --output-dir configs.
    nnunet_plan_path = os.path.join(
        base_dir, "configs", "nnUNetPlans_segmentation.json"
    )
    if os.path.exists(nnunet_plan_path):
        plans_manager = PlansManager(nnunet_plan_path)
        configuration_manager = plans_manager.get_configuration("3d_fullres")
    else:
        raise FileNotFoundError(f"nnUNet plans file not found at {nnunet_plan_path}")

    img_mean = plans_manager.foreground_intensity_properties_per_channel["0"]["mean"]
    img_std = plans_manager.foreground_intensity_properties_per_channel["0"]["std"]
    median_size = plans_manager.original_median_shape_after_transp
    crop_sz, patch_sz = possible_patch_size(
        median_size, configuration_manager.patch_size
    )
    spacing = plans_manager.original_median_spacing_after_transp[::-1]

    seed_torch(seed=config.get("seed", 42))
    set_determinism(seed=config.get("seed", 42))

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    LOGGER.info(f"Using device: {device}")

    model = build_network_architecture(
        configuration_manager.network_arch_class_name,
        configuration_manager.network_arch_init_kwargs,
        configuration_manager.network_arch_init_kwargs_req_import,
        num_input_channels=config.get("in_channels", 1),
        num_output_channels=config.get("out_channels", 3),
        enable_deep_supervision=config.get("deep_supervision", True),
    )
    model.to(device)

    if config.get("custom_patch", False):
        patch_size = patch_sz[-1]
    else:
        patch_size = tuple(configuration_manager.patch_size[::-1])
    LOGGER.info(f"Using patch size {patch_size}")

    # The same preprocessing and the same deterministic patch grid training used — no augmentation.
    test_transform = build_case_transform(
        crop_size=(crop_sz[0], crop_sz[1]), image_mean=img_mean, image_std=img_std
    )
    patch_iter = PatchIterd(
        keys=["image", "mask"], patch_size=patch_size, start_pos=(0, 0), mode="wrap"
    )

    # generate_predictions reads only IMAGE_KEY and "accession_id" from the datalist; the loader
    # below is what feeds the metrics pass.
    test_datalist: list[dict] = []
    test_sets = []
    for site_dir in args.site_dir:
        site_dir = Path(site_dir)
        manifest = pd.read_csv(site_dir / "manifest.csv", dtype=str)
        if args.debug:
            manifest = manifest.iloc[:20]

        dataset = PicaiDataset(
            site_dir,
            modality=args.modality,
            transform=test_transform,
            target_spacing=(spacing[0], spacing[1], spacing[2]),
            patch_iter=patch_iter,
        )
        dataset.df = manifest.reset_index(drop=True)
        test_sets.append(dataset)

        for _, row in manifest.iterrows():
            accession_id = f"{row['patient_id']}_{row['study_id']}"
            test_datalist.append(
                {
                    IMAGE_KEY: site_dir
                    / "nifti"
                    / f"{accession_id}_{args.modality}.nii.gz",
                    "accession_id": accession_id,
                }
            )

    test_dataset = ConcatDataset(test_sets)
    LOGGER.info(f"Running inference on {len(test_datalist)} case(s)")

    test_loader = DataLoader(
        test_dataset,
        batch_size=config.get("batch_size", 1),
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=True,
        drop_last=False,
        collate_fn=list_data_collate,
    )

    criterion = DiceCELoss(include_background=True, sigmoid=True, to_onehot_y=False)

    LOGGER.info("Running inference (metrics)...")
    test_metrics = inference_func(
        config,
        exp_name,
        model_weights_dir,
        model,
        test_loader,
        device,
        criterion,
        spacing=(spacing[0], spacing[1], spacing[2]),
    )
    LOGGER.info(
        f"  Mean Dice: {test_metrics['test_dice_mean']:.4f} | Mean WP: {test_metrics['test_wp_mean']:.4f} | "
        f"Mean PZ: {test_metrics['test_pz_mean']:.4f} | Mean TZ: {test_metrics['test_tz_mean']:.4f}"
    )
    LOGGER.info(
        f"  Std Dice: {test_metrics['test_std_dice']:.4f} | 95% CI Dice: "
        f"[{test_metrics['test_95ci_lower_dice']:.4f}, {test_metrics['test_95ci_upper_dice']:.4f}]"
    )

    # Same load/orient/resample/pad/crop/normalize chain as build_loader + build_case_transform, but
    # over "image" only and MetaTensor-preserving throughout (dataset.py's load_case strips to a
    # plain tensor, which would drop the transform trace Invertd needs) — see generate_predictions.
    # Patching is deliberately absent: Invertd replays exactly what was applied before the model.
    preprocessing = Compose(
        [
            mt.LoadImaged(keys=["image"], ensure_channel_first=True, image_only=True),
            mt.Orientationd(keys=["image"], axcodes=AXCODES, labels=None),
            mt.Spacingd(
                keys=["image"],
                pixdim=(spacing[0], spacing[1], spacing[2]),
                mode="nearest",
            ),
            mt.SpatialPadd(keys=["image"], spatial_size=[crop_sz[0], crop_sz[1], -1]),
            mt.CenterSpatialCropd(
                keys=["image"], roi_size=[crop_sz[0], crop_sz[1], -1]
            ),
            mt.NormalizeIntensityd(keys="image", subtrahend=img_mean, divisor=img_std),
        ]
    )

    # Image-only twin of the training grid: generate_predictions patches a preprocessed volume that
    # carries no mask, so a keys=["image", "mask"] iterator would raise on the missing key.
    predict_patch_iter = PatchIterd(
        keys=["image"], patch_size=patch_size, start_pos=(0, 0), mode="wrap"
    )

    LOGGER.info(f"Saving predictions to {predictions_dir}...")
    generate_predictions(
        predictions_dir,
        config,
        exp_name,
        model_weights_dir,
        model,
        test_datalist,
        preprocessing,
        predict_patch_iter,
        device,
    )
    LOGGER.info("Inference complete")


if __name__ == "__main__":
    infer_loop()
    print("Inference complete")
