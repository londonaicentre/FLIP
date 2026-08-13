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

"""Client-side training script for 3D Spleen Segmentation with FLIP.

This script is executed by FedAvgRecipe's ScriptRunner on each participating
client. It uses the NVFlare Client API (flare.init / receive / send) instead
of the legacy Executor pattern.

Usage (SimEnv):
    python client.py --project_id <id> --query "<sql>" --model_id <uuid>

Usage (Docker):
    Same CLI args; FLIP_PROJECT_ID / FLIP_QUERY / FLIP_MODEL_ID env vars
    serve as fallbacks.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import nibabel as nib
import numpy as np
import nvflare.client as flare
import pandas as pd
import torch
from model import FLUNet
from monai.data import DataLoader, Dataset, decollate_batch
from monai.losses import DiceCELoss
from monai.metrics import DiceMetric
from monai.transforms import AsDiscrete
from nvflare.app_opt.monai import decomposers
from nvflare.client.tracking import SummaryWriter
from transforms import get_sliding_window_inferer, get_train_transforms, get_val_transforms

from flip import FLIP
from flip.constants import ResourceType


def _compute_weight_diff(
    original_weights: dict[str, torch.Tensor],
    new_weights: dict[str, torch.Tensor],
) -> dict[str, np.ndarray]:
    """Return the weight delta (new - original) as numpy arrays.

    Only the difference is sent over the wire, reducing bandwidth.
    """
    diff: dict[str, np.ndarray] = {}
    for key in new_weights:
        diff[key] = new_weights[key].cpu().numpy() - original_weights[key].cpu().numpy()
    return diff


def _build_image_label_pairs(
    flip: FLIP,
    project_id: str,
    dataframe: pd.DataFrame,
) -> list[dict[str, str]]:
    """Walk all accession_ids in the dataframe and return matched
    image/segmentation pairs that pass QC."""
    pairs: list[dict[str, str]] = []
    images_found = 0
    for accession_id in dataframe["accession_id"]:
        try:
            folder = flip.get_by_accession_number(
                project_id,
                accession_id,
                resource_type=[ResourceType.NIFTI],
            )
        except Exception:
            print(f"[FLIP] Could not fetch data for {accession_id}")
            continue

        all_images = list(Path(folder).rglob("input_*.nii.gz"))
        images_found += len(all_images)
        for img_path in all_images:
            seg_path = Path(str(img_path).replace("/input_", "/label_"))
            if not seg_path.exists():
                continue

            try:
                img_hdr = nib.load(str(img_path))
                seg_hdr = nib.load(str(seg_path))
            except Exception:
                continue

            if len(img_hdr.shape) != 3:
                continue
            if any(idim != sdim for idim, sdim in zip(img_hdr.shape, seg_hdr.shape)):
                continue

            pairs.append({"image": str(img_path), "label": str(seg_path)})

    print(f"[FLIP] Found {len(pairs)} valid image/segmentation pairs.")

    # Fail here rather than letting an empty dataset surface downstream as torch's opaque
    # "num_samples should be a positive integer value, but got num_samples=0".
    if not pairs:
        raise RuntimeError(
            f"No image/label pairs found: {images_found} input_*.nii.gz image(s) across "
            f"{len(dataframe['accession_id'])} accession(s), none with a matching label_*.nii.gz. "
            "Supervised training needs a label beside each image in XNAT — was the data-enrichment step "
            "run, and did it run after DICOM-to-NIfTI conversion? See the Data Enrichment user guide."
        )

    return pairs


def _build_loaders(
    pairs: list[dict[str, str]],
    val_split: float,
    batch_size: int = 3,
    val_batch_size: int = 1,
    num_workers: int = 1,
) -> tuple[DataLoader, DataLoader]:
    """Randomly split *pairs* and wrap each half in a DataLoader."""
    indices = np.random.permutation(len(pairs))
    split = int((1.0 - val_split) * len(pairs))
    train_idx = indices[:split]
    val_idx = indices[split:]

    train_ds = Dataset([pairs[i] for i in train_idx], transform=get_train_transforms())
    val_ds = Dataset([pairs[i] for i in val_idx], transform=get_val_transforms())

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=num_workers)
    val_loader = DataLoader(val_ds, batch_size=val_batch_size, shuffle=False, num_workers=num_workers)
    return train_loader, val_loader


def _train_one_epoch(
    model: torch.nn.Module,
    loader: DataLoader,
    loss_fn: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
) -> float:
    """Run one epoch of training.  Returns the average loss."""
    model.train()
    running_loss = 0.0
    num_images = 0
    for batch in loader:
        images = batch["image"].to(device)
        labels = batch["label"].to(device)

        optimizer.zero_grad()
        predictions = model(images)
        cost = loss_fn(predictions, labels)
        cost.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()

        batch_size = images.shape[0]
        running_loss += cost.cpu().detach().numpy() * batch_size
        num_images += batch_size
    return running_loss / num_images if num_images > 0 else 0.0


@torch.inference_mode()
def _validate(
    model: torch.nn.Module,
    loader: DataLoader,
    loss_fn: torch.nn.Module,
    inferer: object,
    device: torch.device,
    post_pred: torch.nn.Module,
    post_pred_gt: torch.nn.Module,
    dice_acc: DiceMetric,
) -> tuple[float, float]:
    """Run validation.  Returns (average_loss, mean_dice)."""
    model.eval()
    dice_acc.reset()
    running_loss = 0.0
    num_batches = 0

    for batch in loader:
        images = batch["image"].to(device)
        labels = batch["label"].to(device)

        predictions = inferer(images, model)
        cost = loss_fn(predictions, labels)
        running_loss += cost.cpu().item()

        preds_d = decollate_batch(predictions)
        preds_d = torch.stack([post_pred(i) for i in preds_d], 0)
        labels_d = torch.stack([post_pred_gt(i) for i in labels], 0)
        dice_acc(y_pred=preds_d, y=labels_d)
        num_batches += 1

    # Aggregate once over all batches; aggregate() returns the running mean over
    # every sample fed since reset(), so calling it per-batch and averaging would
    # double-count earlier batches.
    avg_loss = running_loss / max(num_batches, 1)
    avg_dice = float(dice_acc.aggregate().item()) if num_batches > 0 else 0.0
    return avg_loss, avg_dice


def main() -> None:
    parser = argparse.ArgumentParser(description="FLIP Spleen Segmentation Client")
    parser.add_argument("--local_epochs", type=int, default=4, help="Number of local epochs per FL round")
    parser.add_argument("--lr", type=float, default=5e-5, help="Learning rate")
    parser.add_argument("--val_split", type=float, default=0.1, help="Fraction of data for validation")
    parser.add_argument("--batch_size", type=int, default=3, help="Training batch size")
    parser.add_argument("--project_id", default=os.environ.get("FLIP_PROJECT_ID", ""), help="FLIP project ID")
    parser.add_argument("--query", default=os.environ.get("FLIP_QUERY", ""), help="FLIP SQL query for cohort")
    parser.add_argument("--model_id", default=os.environ.get("FLIP_MODEL_ID", ""), help="Central Hub model UUID")
    args = parser.parse_args()

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    print(f"[FLIP] Device: {device}")

    # ------------------------------------------------------------------
    # 1. FLIP data access — runs once before the FL loop
    # ------------------------------------------------------------------
    flip = FLIP()
    if not args.project_id:
        raise ValueError("project_id is required (pass --project_id or set FLIP_PROJECT_ID env var)")
    if not args.query:
        raise ValueError("query is required (pass --query or set FLIP_QUERY env var)")
    dataframe = flip.get_dataframe(args.project_id, args.query)
    pairs = _build_image_label_pairs(flip, args.project_id, dataframe)
    train_loader, val_loader = _build_loaders(
        pairs, val_split=args.val_split, batch_size=args.batch_size
    )
    print(f"[FLIP] Train batches: {len(train_loader)}, Val batches: {len(val_loader)}")

    # ------------------------------------------------------------------
    # 2. Model, loss, optimizer
    # ------------------------------------------------------------------
    model = FLUNet(
        spatial_dims=3,
        in_channels=1,
        out_channels=2,
        channels=[16, 32, 64, 128, 256],
        strides=[2, 2, 2, 2],
        num_res_units=2,
        norm="batch",
    ).to(device)

    loss_fn = DiceCELoss(
        to_onehot_y=True, softmax=True, squared_pred=False, batch=True, lambda_ce=0.2, lambda_dice=0.8
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    # Post-processing and metrics
    post_pred = AsDiscrete(argmax=True, to_onehot=2)
    post_pred_gt = AsDiscrete(to_onehot=2)
    dice_acc = DiceMetric(include_background=False, reduction="mean")
    inferer = get_sliding_window_inferer(device)

    # ------------------------------------------------------------------
    # 3. NVFlare Client API initialisation
    # ------------------------------------------------------------------
    decomposers.register_monai_types()
    flare.init()
    writer = SummaryWriter()

    # ------------------------------------------------------------------
    # 4. Federated learning loop
    # ------------------------------------------------------------------
    while flare.is_running():
        input_model = flare.receive()
        global_round = input_model.current_round
        print(f"[FLIP] Round {global_round} — receiving global model")

        # Load global weights into local model
        model.load_state_dict(
            {k: torch.as_tensor(v) for k, v in input_model.params.items()}
        )

        # Snapshot original weights for diff computation
        original_weights = {k: v.clone() for k, v in model.state_dict().items()}

        # ------------------------------------------------------------------
        # 4a. Evaluate global model before training
        # ------------------------------------------------------------------
        val_loss, val_dice = _validate(
            model, val_loader, loss_fn, inferer, device,
            post_pred, post_pred_gt, dice_acc,
        )
        writer.add_scalar("validation/loss", val_loss, global_step=global_round)
        writer.add_scalar("validation/dice", val_dice, global_step=global_round)
        print(f"[FLIP] Pre-train validation — loss: {val_loss:.4f}, dice: {val_dice:.4f}")

        # ------------------------------------------------------------------
        # 4b. Local training
        # ------------------------------------------------------------------
        train_loss = 0.0
        for epoch in range(args.local_epochs):
            train_loss = _train_one_epoch(model, train_loader, loss_fn, optimizer, device)
            # Per-epoch point: the "@epoch" tag suffix names the x-axis, global_step is its coordinate
            # (the FLIP analytics bridge parses "<label>[@<x_label>]" — see FLIP#148).
            writer.add_scalar("training/loss@epoch", train_loss, global_step=global_round * args.local_epochs + epoch)
            print(f"[FLIP] Epoch {epoch + 1}/{args.local_epochs} — train loss: {train_loss:.4f}")

        # ------------------------------------------------------------------
        # 4c. Evaluate post-training
        # ------------------------------------------------------------------
        val_loss, val_dice = _validate(
            model, val_loader, loss_fn, inferer, device,
            post_pred, post_pred_gt, dice_acc,
        )
        writer.add_scalar("validation/post_train_loss", val_loss, global_step=global_round)
        writer.add_scalar("validation/post_train_dice", val_dice, global_step=global_round)
        print(f"[FLIP] Post-train validation — loss: {val_loss:.4f}, dice: {val_dice:.4f}")

        # ------------------------------------------------------------------
        # 4d. Compute weight diff and send back
        # ------------------------------------------------------------------
        new_weights = model.state_dict()
        weight_diff = _compute_weight_diff(original_weights, new_weights)

        output_model = flare.FLModel(
            params=weight_diff,
            params_type=flare.ParamsType.DIFF,
            metrics={"accuracy": float(val_dice)},
        )
        flare.send(output_model)
        print(f"[FLIP] Round {global_round} complete — sent weight diff")

        # ------------------------------------------------------------------
        # 4e. Send metrics to Central Hub (FLIP platform integration)
        # ------------------------------------------------------------------
        flip.send_metrics(
            client_name=flare.get_site_name(),
            model_id=args.model_id,
            label="VAL_DICE",
            value=float(val_dice),
            global_round=global_round,
        )
        flip.send_metrics(
            client_name=flare.get_site_name(),
            model_id=args.model_id,
            label="TRAIN_LOSS",
            value=float(train_loss),
            global_round=global_round,
        )

    print("[FLIP] FL loop ended. Exiting.")


if __name__ == "__main__":
    main()
