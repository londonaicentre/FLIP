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
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/training/nnunet_train.py

import argparse
import gc
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from batchgenerators.utilities.file_and_folder_operations import load_json
from monai.data import PatchIterd, list_data_collate
from monai.losses import DiceCELoss
from monai.transforms import (
    Compose,
    RandAxisFlipd,
    RandCoarseDropoutd,
    RandGaussianNoised,
    RandGaussianSmoothd,
    RandRotate90d,
    RandShiftIntensityd,
    RandZoomd,
)
from monai.utils import set_determinism
from nnunetv2.training.loss.deep_supervision import DeepSupervisionWrapper
from nnunetv2.utilities.plans_handling.plans_handler import PlansManager
from torch.optim.lr_scheduler import PolynomialLR
from torch.utils.data import ConcatDataset, DataLoader

from dataset import PicaiDataset
from network import (
    build_network_architecture,
    set_deep_supervision_enabled,
)
from preprocess import build_case_transform
from train_helpers import (
    init_logger,
    possible_patch_size,
    seed_torch,
    train_seg,
)

SEED = 42
seed_torch(seed=SEED)


class EarlyStopping:
    """Early stops training if a monitored validation loss doesn't improve after a given patience.

    Moved here from MambaX-Net's metrics/utils.py (flattened as metrics_utils.py), this script being
    its only consumer. Upstream's save_checkpoint() came with it but was never called by anything —
    it wrote a hardcoded "checkpoint.pt" into the CWD — so it is dropped here; train_loop does its
    own checkpointing. `verbose` and `val_loss_min` are kept: verbose is still passed by the
    constructor call below, and both stay part of the object's attribute surface.

    Attributes:
        patience (float): Number of consecutive non-improving calls to wait before stopping.
        verbose (bool): Retained for constructor compatibility; with save_checkpoint gone nothing
            reads it.
        counter (int): Number of consecutive calls since the last improvement.
        best_score (Optional[float]): Best (lowest) validation loss seen so far.
        early_stop (bool): Set to True once patience is exceeded.
        val_loss_min (float): Initialised to inf and never updated, save_checkpoint having been
            its only writer.
    """

    def __init__(self, patience: float = 7, verbose: bool = False) -> None:
        """
        Early stops the training if validation loss doesn't improve after a given patience.

        Args:
            patience (float): How long to wait after last time validation loss improved.
                            Default: 7
            verbose (bool): If True, prints a message for each validation loss improvement.
                            Default: False
        """
        self.patience = patience
        self.verbose = verbose
        self.counter = 0
        self.best_score = None
        self.early_stop = False
        self.val_loss_min = np.inf

    def __call__(self, val_loss: float, model: torch.nn.Module) -> None:
        """
        Args:
            val_loss (float): Validation loss value
            model (torch.nn.Module): Model to save. Currently not used
        """
        score = val_loss

        if self.best_score is None:
            self.best_score = score
        elif score > self.best_score:
            self.counter += 1
            print(f"EarlyStopping counter: {self.counter} out of {self.patience}")
            if self.counter >= self.patience:
                self.early_stop = True
        else:
            self.best_score = score
            self.counter = 0


def build_site_datasets(
    site_dirs: list[Path],
    modality: str,
    val_split: float,
    train_transform: Compose,
    valid_transform: Compose,
    target_spacing: tuple[float, float, float],
    patch_iter: PatchIterd,
    limit: int | None = None,
) -> tuple[ConcatDataset, ConcatDataset]:
    """Split each site's manifest into train/val, then concatenate the sites into one dataset pair.

    The split is per site rather than over the pooled studies so that every center is represented
    on both sides of it — a global split over an unbalanced roster (RUMC contributes 800 studies
    against ZGT's and PCNN's 350) can leave a small site almost entirely in one half.

    Args:
        site_dirs: `data/prostate/sites/<CENTER>` folders, each holding `manifest.csv`, `nifti/`,
            `labels/` and `zonal_labels/`.
        modality: Which PI-CAI scan to load as the image (`t2w`, `adc` or `hbv`).
        val_split: Validation fraction (0-1), applied within each site.
        train_transform: Applied to the training half (patching + augmentation).
        valid_transform: Applied to the validation half (no patching, no augmentation).
        target_spacing: (x, y, z) mm spacing every volume is resampled to — see
            `dataset.build_loader`.
        patch_iter: `monai.data.PatchIterd` over the "image" and "mask" keys, passed through to
            every `PicaiDataset` so `__getitem__` yields one dict per patch instead of per volume.
        limit: Keep at most this many studies per site, for a quick smoke run.

    Returns:
        (train_dataset, valid_dataset): both `ConcatDataset`s over per-site `PicaiDataset`s.
    """
    train_sets, valid_sets = [], []
    for site_dir in site_dirs:
        manifest = pd.read_csv(Path(site_dir) / "manifest.csv", dtype=str)
        manifest = manifest.sample(frac=1, random_state=SEED).reset_index(drop=True)
        if limit is not None:
            manifest = manifest.iloc[:limit]
        n_val = int(val_split * len(manifest))

        for transform, rows, sets in (
            (train_transform, manifest.iloc[n_val:], train_sets),
            (valid_transform, manifest.iloc[:n_val], valid_sets),
        ):
            dataset = PicaiDataset(
                site_dir,
                modality=modality,
                transform=transform,
                target_spacing=target_spacing,
                patch_iter=patch_iter,
            )
            dataset.df = rows.reset_index(drop=True)
            sets.append(dataset)

    return ConcatDataset(train_sets), ConcatDataset(valid_sets)


def build_augmentations() -> Compose:
    """The training augmentations, with MambaX-Net's exact set and probabilities.

    Applied to the WHOLE volume before patching (see train_loop), which is the order upstream's
    PicSegDataset used. `Rand3DElasticd` is deliberately absent: this port had added it, it never
    ran upstream, and it would change what the model sees.

    Returns:
        Compose: Operates on a `{"image", "mask"}` dict.
    """
    return Compose([
        RandAxisFlipd(prob=0.1, keys=["image", "mask"]),
        RandRotate90d(prob=0.2, keys=["image", "mask"]),
        RandGaussianNoised(keys=["image"], prob=0.45),
        RandShiftIntensityd(keys=["image"], offsets=(10, 20), prob=0.15),
        RandZoomd(
            prob=0.25,
            min_zoom=0.8,
            max_zoom=1.2,
            keep_size=True,
            keys=["image", "mask"],
        ),
        RandGaussianSmoothd(
            keys=["image"],
            sigma_x=(0.25, 1.5),
            sigma_y=(0.25, 1.5),
            sigma_z=(0.25, 1.5),
            approx="erf",
            prob=0.15,
        ),
        RandCoarseDropoutd(
            keys=["image"],
            holes=8,
            max_holes=15,
            spatial_size=(30, 30, 5),
            prob=0.15,
        ),
    ])


def train_loop():
    """Run the full nnU-Net-style training pipeline for PICAI/AS prostate segmentation.

    Parses CLI args, loads environment/config, sets up logging, builds the network
    from the nnU-Net plans file, prepares the PICAI train/validation datasets and
    dataloaders, then trains for the configured number of epochs
    with early stopping, saving the best model weights and a per-epoch checkpoint.
    Args:

    Returns:
        None
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-s",
        "--site-dir",
        type=Path,
        nargs="+",
        required=True,
        help="One or more data/prostate/sites/<CENTER> folders (each holding manifest.csv, nifti/, "
        "labels/, zonal_labels/). Passing several pools their studies into a single local run.",
    )
    parser.add_argument("--modality", type=str, default="t2w", choices=["t2w", "adc", "hbv"])
    parser.add_argument("--val-split", type=float, default=0.2)
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
    checkpoints_dir = os.path.join(base_dir, "checkpoints")
    runs_dir = os.path.join(base_dir, "runs")

    # Create all necessary directories
    os.makedirs(logs_dir, exist_ok=True)
    os.makedirs(model_weights_dir, exist_ok=True)
    os.makedirs(checkpoints_dir, exist_ok=True)
    os.makedirs(runs_dir, exist_ok=True)

    LOG_FILE = os.path.join(logs_dir, f"{exp_name}_train.log")
    LOGGER = init_logger(LOG_FILE)
    LOGGER.info(f"Experiment name: {exp_name}")

    config = load_json(args.config)

    # Written by calculate_dataset_fingerprint_segmentation.py --output-dir configs.
    nnunet_plan_path = os.path.join(base_dir, "configs", "nnUNetPlans_segmentation.json")
    if os.path.exists(nnunet_plan_path):
        plans_manager = PlansManager(nnunet_plan_path)
        configuration_manager = plans_manager.get_configuration("3d_fullres")
    else:
        raise FileNotFoundError(f"nnUNet plans file not found at {nnunet_plan_path}")

    img_mean = plans_manager.foreground_intensity_properties_per_channel["0"]["mean"]
    img_std = plans_manager.foreground_intensity_properties_per_channel["0"]["std"]
    suggested_patch_size = configuration_manager.patch_size

    median_size = plans_manager.original_median_shape_after_transp  # [::-1]
    crop_sz, patch_sz = possible_patch_size(median_size, suggested_patch_size)
    spacing = plans_manager.original_median_spacing_after_transp[::-1]

    seed_torch(seed=config.get("seed", 42))
    set_determinism(seed=config.get("seed", 42))
    print(f"Seed set to: {os.getenv('PYTHONHASHSEED')}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    LOGGER.info(f"Using device: {device}")

    if args.debug:
        config["epochs"] = 1

    model = build_network_architecture(
        configuration_manager.network_arch_class_name,
        configuration_manager.network_arch_init_kwargs,
        configuration_manager.network_arch_init_kwargs_req_import,
        num_input_channels=config.get("in_channels", 1),
        num_output_channels=config.get("out_channels", 3),
        enable_deep_supervision=config.get("deep_supervision", True),
    )

    net_num_pool_op_kernel_sizes = plans_manager.get_configuration("3d_fullres").pool_op_kernel_sizes

    deep_supervision_scales = list(list(i) for i in 1 / np.cumprod(np.vstack(net_num_pool_op_kernel_sizes), axis=0))[
        :-1
    ]

    weights = np.array([1 / (2**i) for i in range(len(deep_supervision_scales))])
    ds_loss_weights = (weights / weights.sum()).tolist()

    LOGGER.info("Loading model...")
    if config.get("deep_supervision", True):
        LOGGER.info("Using deep supervision...")
        model = set_deep_supervision_enabled(True, False, model)
    model.to(device)

    LOGGER.info("Loading dataset...")

    transforms = build_augmentations()
    if config.get("custom_patch", False):
        patch_size = patch_sz[-1]
        LOGGER.info(f"Using custom patch size {patch_size}")
    else:
        patch_size = tuple(configuration_manager.patch_size[::-1])
        LOGGER.info(f"Using suggested patch size {patch_size}")

    preprocess_transform = build_case_transform(
        crop_size=(crop_sz[0], crop_sz[1]), image_mean=img_mean, image_std=img_std
    )
    # Augment the WHOLE volume, then tile it
    train_transform = Compose([preprocess_transform, transforms])
    valid_transform = preprocess_transform

    patch_iter = PatchIterd(keys=["image", "mask"], patch_size=patch_size, start_pos=(0, 0), mode="wrap")

    train_dataset, valid_dataset = build_site_datasets(
        args.site_dir,
        modality=args.modality,
        val_split=args.val_split,
        train_transform=train_transform,
        valid_transform=valid_transform,
        target_spacing=(spacing[0], spacing[1], spacing[2]),
        patch_iter=patch_iter,
        limit=20 if args.debug else None,
    )
    LOGGER.info(f"Training on {len(train_dataset)} case(s), validating on {len(valid_dataset)}")

    train_loader = DataLoader(
        train_dataset,
        batch_size=config.get("batch_size", 1),
        shuffle=True,
        num_workers=args.num_workers,
        pin_memory=True,
        drop_last=False,
        collate_fn=list_data_collate,
    )

    valid_loader = DataLoader(
        valid_dataset,
        batch_size=config.get("batch_size", 1),
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=True,
        drop_last=False,
        collate_fn=list_data_collate,
    )

    LOGGER.info("Loading dataset complete")

    optimizer = torch.optim.SGD(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=config.get("lr", 0.01),
        weight_decay=3e-5,
        momentum=0.99,
    )

    scheduler = PolynomialLR(optimizer, total_iters=config.get("epochs", 1000), power=0.9)

    early_stopping = EarlyStopping(patience=config.get("patience", 50), verbose=False)

    criterion = DiceCELoss(
        include_background=True,
        sigmoid=True,
        to_onehot_y=False,
    )

    if config.get("deep_supervision", True):
        criterion = DeepSupervisionWrapper(criterion, ds_loss_weights)

    if config.get("bf16", True):
        LOGGER.info("Using mixed precision training...")

    best_dice = 0

    LOGGER.info("Starting training...")

    for epoch in range(1, config.get("epochs", 1000) + 1):
        start_time = time.time()
        torch.cuda.empty_cache()
        gc.collect()

        LOGGER.info(f"starting epoch {epoch}...")

        avg_train_loss, avg_val_loss, metrics = train_seg(
            config,
            model,
            optimizer,
            scheduler,
            train_loader,
            valid_loader,
            criterion,
            device,
        )

        scheduler.step()

        elapsed = time.time() - start_time
        LOGGER.info(
            f"  Epoch {epoch} - train/avg_loss: {avg_train_loss:.4f} | "
            f"val/avg_loss: {avg_val_loss:.4f} | time: {elapsed:.0f}s"
        )
        LOGGER.info(
            f"  Epoch {epoch} - train/mean_dice_avg: {metrics['train/mean_dice_avg']} | "
            f"val/mean_dice_avg: {metrics['val/mean_dice_avg']}"
        )

        if metrics["val/mean_dice_avg"] > best_dice:
            model_path = os.path.join(model_weights_dir, f"{exp_name}_model.pt")
            torch.save(model.state_dict(), model_path)
            best_dice = metrics["val/mean_dice_avg"]

        checkpoint = {
            "epoch": epoch,
            "lr": optimizer.param_groups[0]["lr"],
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "scheduler_state_dict": scheduler.state_dict(),
        }

        checkpoint_path = os.path.join(checkpoints_dir, f"{exp_name}_checkpoint.pt")
        torch.save(checkpoint, checkpoint_path)

        early_stopping(avg_val_loss, model)
        if early_stopping.early_stop:
            print("Early stopping")
            break

    LOGGER.info(f"  Best Dice score {best_dice}")


if __name__ == "__main__":
    train_loop()
    print("Training complete")
