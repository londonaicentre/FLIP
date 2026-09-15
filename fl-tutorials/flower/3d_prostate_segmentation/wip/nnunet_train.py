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
# Copied verbatim from
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/training/nnunet_train.py,
# flattened into this tutorial's directory.

import argparse
import gc
import os
import time

import numpy as np
import torch
import wandb
from batchgenerators.utilities.file_and_folder_operations import load_json
from carbontracker import parser as tracker_parser
from carbontracker.tracker import CarbonTracker
from dotenv import load_dotenv
from monai.data import PatchIterd
from monai.transforms import (
    Compose,
    Rand3DElasticd,
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
from nnunetv2.training.lr_scheduler.polylr import PolyLRScheduler
from nnunetv2.utilities.plans_handling.plans_handler import PlansManager
from sklearn.model_selection import StratifiedGroupKFold
from torch.amp import GradScaler
from torch.optim.lr_scheduler import (
    CosineAnnealingLR,
    CosineAnnealingWarmRestarts,
    ReduceLROnPlateau,
    StepLR,
)
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter

import nifti_utilities as nutil
from custom_collate import patch_collate_seg
from csv_load import active_surveillance_data_load, picai_data_load
from mambax_picai_dataset import PicSegDataset
from compound_loss import FocalTverskyDiceCE
from metrics_utils import EarlyStopping
from nnunet_arch import (
    build_network_architecture,
    set_deep_supervision_enabled,
)
from warmup import GradualWarmupSchedulerV2
from crop import InPlaneCrop
from normalize import NormalizeData
from random_noise import (
    MaskDropout,
    RandomMaskNoise,
    mark_mask_noise_by_patient,
)
from resample import MONAIResample
from train_helpers import (
    get_scheduler,
    init_logger,
    seed_torch,
    train_seg,
)


def train_loop(tb_writer):
    """Run the full nnU-Net-style training pipeline for PICAI/AS prostate segmentation.

    Parses CLI args, loads environment/config, sets up logging, wandb tracking and
    carbon tracking, builds the network from the nnU-Net plans file, prepares the
    train/validation datasets and dataloaders (with optional mask-noise augmentation
    and k-fold splitting for PICAI), then trains for the configured number of epochs
    with early stopping, saving the best model weights and a per-epoch checkpoint.
    Logs metrics and carbon emissions to wandb.

    Args:
        tb_writer (SummaryWriter): TensorBoard writer passed through to `train_seg`
            for logging training/validation curves.

    Returns:
        SummaryWriter: The same `tb_writer` instance passed in.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("-df", "--df_path", type=str, required=False)
    parser.add_argument("-p", "--prostateX_mapping", type=str, required=False)
    parser.add_argument("-t2", "--T2_path", type=str, required=True)
    parser.add_argument("-wp", "--WP_label_path", type=str, required=True)
    parser.add_argument("-pz", "--PZ_TZ_label_path", type=str, required=True)
    parser.add_argument("-val_t2", "--val_T2_path", type=str, required=False)
    parser.add_argument("-val_wp", "--val_WP_label_path", type=str, required=False)
    parser.add_argument("-val_pz", "--val_PZ_TZ_label_path", type=str, required=False)
    parser.add_argument("-test_wp", "--test_WP_label_path", type=str, required=False)
    parser.add_argument("-test_pz", "--test_PZ_TZ_label_path", type=str, required=False)
    parser.add_argument(
        "-name",
        "--dataset_name",
        type=str,
        default="picai",
        choices=["picai", "AS"],
        help="Dataset: 'picai' for ProstateX/PICAI (k-fold), 'AS' for Active Surveillance (fixed split, uses AS_seg_cache)",
    )
    parser.add_argument("--sample_sz", type=int, default=300)
    parser.add_argument(
        "--pretrained_weights",
        type=str,
        default=None,
        help="Path to pretrained model weights (.pt state dict) to fine-tune from. Only used when -name AS.",
    )
    parser.add_argument("-conf", "--config", type=str, required=True)
    parser.add_argument("--fold", type=int, required=False, default=0)
    parser.add_argument("-nw", "--num_workers", type=int, required=True)
    parser.add_argument("--exp_name", type=str, required=True)
    parser.add_argument("--noise", type=float, default=0.0)
    parser.add_argument("--both_mask_noise", type=float, default=0.0)
    parser.add_argument(
        "--debug",
        type=bool,
        default=False,
        help="Limit number of images for processing (for testing)",
    )
    args = parser.parse_args()

    load_dotenv()

    os.getenv("WANDB_API_KEY")
    if not os.getenv("WANDB_API_KEY"):
        raise EnvironmentError(
            "The environment variable 'WANDB_API_KEY' is not set. Please set it before running the script."
        )

    ENTITY = os.getenv("ENTITY")
    if ENTITY is None:
        raise EnvironmentError(
            "The environment variable 'ENTITY' is not set. Please set it before running the script."
        )

    PROJECT = os.getenv("PROJECT")
    if PROJECT is None:
        raise EnvironmentError(
            "The environment variable 'PROJECT' is not set. Please set it before running the script."
        )

    os.environ["WANDB_NOTEBOOK_NAME"] = "nnunet_train.py"

    exp_name = args.exp_name

    dataset_name = args.dataset_name
    T2_path = args.T2_path
    WP_label_path = args.WP_label_path
    PZ_TZ_label_path = args.PZ_TZ_label_path

    # Create base directory path and ensure consistency - pointing to project root
    current_file_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.join(
        current_file_dir, "..", ".."
    )  # Go up two levels to reach project root
    base_dir = os.path.normpath(base_dir)
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

    carbon_log_path = os.path.join(logs_dir, f"carbon_{exp_name}_lesion_train.log")

    config = load_json(args.config)

    tracker = CarbonTracker(
        epochs=config["epochs"],
        log_dir=carbon_log_path,
        api_keys={
            "electricitymaps": os.getenv("ElectricityMaps"),
        },
    )

    nnunet_plan_path = os.path.join(
        base_dir,
        "mambax_net",
        "configs",
        "nnUNetPlans_segmentation.json",
    )
    nnunet_plan_path = os.path.normpath(nnunet_plan_path)
    if os.path.exists(nnunet_plan_path):
        plans_manager = PlansManager(nnunet_plan_path)
        configuration_manager = plans_manager.get_configuration("3d_fullres")
    else:
        raise FileNotFoundError(f"nnUNet plans file not found at {nnunet_plan_path}")

    img_mean = plans_manager.foreground_intensity_properties_per_channel["0"]["mean"]
    img_std = plans_manager.foreground_intensity_properties_per_channel["0"]["std"]
    suggested_patch_size = configuration_manager.patch_size

    median_size = plans_manager.original_median_shape_after_transp  # [::-1]
    crop_sz, patch_sz = nutil.possible_patch_size(median_size, suggested_patch_size)
    spacing = plans_manager.original_median_spacing_after_transp[::-1]

    seed_torch(seed=config["seed"])
    set_determinism(seed=config["seed"])
    print(f"Seed set to: {os.getenv('PYTHONHASHSEED')}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    LOGGER.info(f"Using device: {device}")

    wandb.init(project=PROJECT, entity=ENTITY, config=config, name=exp_name)
    wandb.define_metric("custom_train_step")
    wandb.define_metric("custom_val_step")
    wandb.define_metric("custom_lr_step")

    # Associating the step metric with the train/val loss
    wandb.define_metric("train/*", step_metric="custom_train_step")
    wandb.define_metric("val/*", step_metric="custom_val_step")

    if dataset_name == "AS":
        rem_ids_config_path = os.path.join(
            base_dir,
            "mambax_net",
            "configs",
            "ignore_ids.yaml",
        )
        rem_ids_config_path = os.path.normpath(rem_ids_config_path)
        train_df, val_df, test_df = active_surveillance_data_load(
            args,
            config,
            T2_path,
            args.val_WP_label_path,
            args.test_WP_label_path,
            best_preds_path=None,
            coarse_preds_path=None,
            rem_ids_config_path=rem_ids_config_path,
            include_timepoints=["_1", "_2"],
        )
    else:
        if args.df_path is None or args.prostateX_mapping is None:
            raise ValueError("-df and -p are required for picai dataset")
        train_df, test_df = picai_data_load(args.df_path, args.prostateX_mapping)

    if args.debug:
        train_df = train_df.sample(min(20, len(train_df))).reset_index(drop=True)
        if dataset_name == "AS":
            val_df = val_df.sample(min(20, len(val_df))).reset_index(drop=True)
        else:
            test_df = test_df.sample(min(20, len(test_df))).reset_index(drop=True)
        config["epochs"] = 1

    if args.noise > 0:
        LOGGER.info(
            f"Applying mask noise with probability {args.noise}% during training"
        )
    if args.both_mask_noise > 0 and dataset_name == "AS":
        train_df, noisy_mask_patients = mark_mask_noise_by_patient(
            train_df, args.both_mask_noise, config["seed"]
        )
        n_train_patients = (
            train_df["study_id"].astype(str).str.rsplit("_", n=1).str[0].nunique()
        )
        n_noisy_rows = int(train_df["mask_noise"].sum())
        LOGGER.info(
            f"Perturbing flattened current and prior labels for {len(noisy_mask_patients)}/{n_train_patients} "
            f"training patients ({n_noisy_rows} rows; {args.both_mask_noise}%)"
        )
        wandb.config.update(
            {
                "both_mask_noise": args.both_mask_noise,
                "both_mask_noise_patients": len(noisy_mask_patients),
            },
            allow_val_change=True,
        )

    if dataset_name == "picai":
        skf = StratifiedGroupKFold(
            n_splits=config["n_fold"], random_state=config["seed"], shuffle=True
        )
        train_df["fold"] = -1
        for i, (train_idx, valid_idx) in enumerate(
            skf.split(train_df, train_df["case_ISUP"], groups=train_df["patient_id"])
        ):
            train_df.loc[valid_idx, "fold"] = i
        fold = args.fold
        LOGGER.info(f"Training fold {fold}")

    model = build_network_architecture(
        configuration_manager.network_arch_class_name,
        configuration_manager.network_arch_init_kwargs,
        configuration_manager.network_arch_init_kwargs_req_import,
        num_input_channels=config["in_channels"],
        num_output_channels=config["out_channels"],
        enable_deep_supervision=config["deep_supervision"],
    )

    net_num_pool_op_kernel_sizes = plans_manager.get_configuration(
        "3d_fullres"
    ).pool_op_kernel_sizes

    deep_supervision_scales = list(
        list(i) for i in 1 / np.cumprod(np.vstack(net_num_pool_op_kernel_sizes), axis=0)
    )[:-1]

    weights = np.array([1 / (2**i) for i in range(len(deep_supervision_scales))])
    ds_loss_weights = weights / weights.sum()

    LOGGER.info("Loading model...")
    if config["deep_supervision"]:
        LOGGER.info("Using deep supervision...")
        model = set_deep_supervision_enabled(True, False, model)
    model.to(device)

    if dataset_name == "AS" and args.pretrained_weights is not None:
        pretrained_path = os.path.normpath(args.pretrained_weights)
        LOGGER.info(f"Fine-tuning from pretrained weights: {pretrained_path}")
        state_dict = torch.load(pretrained_path, map_location=device)
        missing, unexpected = model.load_state_dict(state_dict, strict=False)
        if missing:
            LOGGER.warning(f"Missing keys when loading pretrained weights: {missing}")
        if unexpected:
            LOGGER.warning(
                f"Unexpected keys when loading pretrained weights: {unexpected}"
            )

    if dataset_name == "AS":
        train_data = train_df
        valid_data = val_df
    else:
        train_idx = np.where((train_df["fold"] != fold))[0]
        valid_idx = np.where((train_df["fold"] == fold))[0]
        train_data = train_df.loc[train_idx]
        valid_data = train_df.loc[valid_idx]

    LOGGER.info("Loading dataset...")

    preprocess = Compose(
        [
            MONAIResample((spacing[0], spacing[1], spacing[2])),
            InPlaneCrop(crop_sz[0], crop_sz[1]),
            NormalizeData(mean=img_mean, std=img_std),
        ]
    )

    transforms = Compose(
        [
            RandAxisFlipd(prob=0.3, keys=["image", "mask"]),
            RandRotate90d(prob=0.4, keys=["image", "mask"]),
            RandGaussianNoised(keys=["image"], prob=0.35),
            RandShiftIntensityd(keys=["image"], offsets=(10, 20), prob=0.35),
            RandZoomd(
                prob=0.20,
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
            Rand3DElasticd(
                keys=["image", "mask"],
                sigma_range=(3, 5),
                magnitude_range=(50, 150),
                prob=0.20,
                padding_mode="reflection",
                mode=["bilinear", "nearest"],
            ),
        ]
        + (
            [RandomMaskNoise(keys=["image", "mask"], prob=float(args.noise / 100.0))]
            if args.noise > 0
            else []
        )
        + (
            [MaskDropout(keys=["image", "mask"], drop_prob=1.0)]
            if args.both_mask_noise > 0
            else []
        )
    )
    if config["custom_patch"]:
        LOGGER.info(f"Using custom patch size {patch_sz[-1]}")
        patch_iter = PatchIterd(
            keys=["image", "mask"],
            patch_size=(patch_sz[-1]),
            start_pos=(0, 0),
            mode="wrap",
        )
    else:
        LOGGER.info(
            f"Using suggested patch size {configuration_manager.patch_size[::-1]}"
        )
        patch_iter = PatchIterd(
            keys=["image", "mask"],
            patch_size=(configuration_manager.patch_size[::-1]),
            start_pos=(0, 0),
            mode="wrap",
        )

    cache_dir = os.path.join(
        base_dir,
        "mambax_net",
        "data",
        "AS_seg_cache" if dataset_name == "AS" else "seg_cache",
    )
    cache_dir = os.path.normpath(cache_dir)

    train_dataset = PicSegDataset(
        train_data,
        patch_iter,
        T2_path,
        WP_label_path,
        PZ_TZ_label_path,
        preprocess=preprocess,
        transform=transforms,
        patch_transform=None,
        with_coordinates=False,
        cache=dataset_name == "AS",
        cache_dir=cache_dir,
        mode="train",
        dataset_name=dataset_name,
    )

    valid_t2 = args.val_T2_path if dataset_name == "AS" else T2_path
    valid_wp = args.val_WP_label_path if dataset_name == "AS" else WP_label_path
    valid_pz = args.val_PZ_TZ_label_path if dataset_name == "AS" else PZ_TZ_label_path

    valid_dataset = PicSegDataset(
        valid_data,
        patch_iter,
        valid_t2,
        valid_wp,
        valid_pz,
        preprocess=preprocess,
        transform=None,
        patch_transform=None,
        with_coordinates=False,
        cache=dataset_name == "AS",
        cache_dir=cache_dir,
        mode="valid",
        dataset_name=dataset_name,
    )

    train_loader = DataLoader(
        train_dataset,
        batch_size=config["batch_size"],
        shuffle=True,
        num_workers=args.num_workers,
        pin_memory=True,
        drop_last=False,
        collate_fn=patch_collate_seg,
    )

    valid_loader = DataLoader(
        valid_dataset,
        batch_size=config["batch_size"],
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=True,
        drop_last=False,
        collate_fn=patch_collate_seg,
    )

    LOGGER.info("Loading dataset complete")

    optimizer = torch.optim.SGD(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=config["lr"],
        weight_decay=3e-5,
        momentum=0.99,
    )

    scheduler = get_scheduler(
        config, optimizer, config["scheduler"], train_loader, config["step_size"]
    )

    early_stopping = EarlyStopping(patience=config["patience"], verbose=False)

    criterion = FocalTverskyDiceCE(
        alpha=0.8,
        beta=0.8,
        gamma=0.8,
        focal_gamma=3.0,
        background=True,
        sigmoid=True,
        softmax=False,
        to_onehot_y=False,
        mode="binary",
    )

    if config["deep_supervision"]:
        criterion = DeepSupervisionWrapper(criterion, ds_loss_weights)

    if config["bf16"]:
        scaler = GradScaler(enabled=False)
        LOGGER.info("Using mixed precision training...")
    else:
        scaler = None

    best_dice = 0
    best_wp = 0
    best_pz = 0
    best_tz = 0

    LOGGER.info("Starting training...")

    wandb.watch(model, log="all")

    for epoch in range(1, config["epochs"] + 1):
        tracker.epoch_start()
        start_time = time.time()
        torch.cuda.empty_cache()
        gc.collect()

        LOGGER.info(f"starting epoch {epoch}...")

        avg_train_loss, avg_val_loss, metrics = train_seg(
            epoch,
            config,
            model,
            optimizer,
            scheduler,
            train_loader,
            valid_loader,
            tb_writer,
            scaler,
            criterion,
            device,
        )

        if isinstance(scheduler, ReduceLROnPlateau):
            scheduler.step(metrics["val/mean_dice"])
        if isinstance(scheduler, CosineAnnealingLR):
            scheduler.step()
        if isinstance(scheduler, CosineAnnealingWarmRestarts):
            scheduler.step()
        if isinstance(scheduler, GradualWarmupSchedulerV2):
            scheduler.step()
        if isinstance(scheduler, StepLR):
            scheduler.step()
        if isinstance(scheduler, PolyLRScheduler):
            scheduler.step()

        elapsed = time.time() - start_time
        LOGGER.info(
            f"  Epoch {epoch} - train/avg_loss: {avg_train_loss:.4f} | val/avg_loss: {avg_val_loss:.4f} | time: {elapsed:.0f}s"
        )
        LOGGER.info(
            f"  Epoch {epoch} - train/mean_dice_avg: {metrics['train/mean_dice_avg']} | val/mean_dice_avg: {metrics['val/mean_dice_avg']}"
        )

        if metrics["val/mean_dice_avg"] > best_dice:
            model_path = os.path.join(model_weights_dir, f"{exp_name}_model.pt")
            torch.save(model.state_dict(), model_path)
            best_dice = metrics["val/mean_dice_avg"]
            best_wp = metrics["val/wp_dice_avg"]
            best_pz = metrics["val/pz_dice_avg"]
            best_tz = metrics["val/tz_dice_avg"]

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

        tracker.epoch_end()

    wandb.unwatch(model)

    LOGGER.info(f"  Best Dice score {best_dice}")
    wandb.run.summary["Best Dice score"] = best_dice
    wandb.run.summary["Best WP Dice"] = best_wp
    wandb.run.summary["Best PZ Dice"] = best_pz
    wandb.run.summary["Best TZ Dice"] = best_tz

    model_path = os.path.join(model_weights_dir, f"{exp_name}_model.pt")
    wandb.run.log_model(
        path=model_path,
        name=f"{exp_name}_model",
    )

    logs = tracker_parser.parse_all_logs(
        log_dir=f"logs/carbon_{exp_name}_lesion_train.log"
    )
    last_log = logs[-1]

    wandb.run.summary["Actual consumption"] = last_log["actual"]
    wandb.run.summary["Predicted emissions"] = last_log["pred"]

    return tb_writer


if __name__ == "__main__":
    current_file_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.join(
        current_file_dir, "..", ".."
    )  # Go up two levels to reach project root
    project_root = os.path.normpath(project_root)
    runs_dir = os.path.join(project_root, "runs")
    tb_writer = SummaryWriter(runs_dir)

    tb_writer = train_loop(tb_writer)
    print("Training complete")

    tb_writer.close()
    wandb.finish()
