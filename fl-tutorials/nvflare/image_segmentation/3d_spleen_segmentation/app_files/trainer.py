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

import argparse
import json
import logging
from pathlib import Path

import nibabel as nib
import numpy as np
import nvflare.client as flare
import pandas as pd
import torch
from flip import FLIP
from flip.constants import ResourceType
from models import get_model
from monai.data import DataLoader, Dataset, decollate_batch
from monai.losses import DiceCELoss
from monai.metrics import DiceMetric
from monai.transforms import AsDiscrete
from nvflare.client.tracking import SummaryWriter
from tqdm import tqdm
from transforms import get_sliding_window_inferer, get_train_transforms, get_val_transforms

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project_id", type=str, default="")
    return parser.parse_args()


def load_query() -> str:
    """Read the cohort query from the client app config.

    NVFlare's TaskScriptRunner does a naive whitespace split on task_script_args,
    so the SQL query (which can contain spaces) is plumbed via the top-level
    ``query`` key in ``config/config_fed_client.json`` rather than as a CLI flag.
    In dev/simulator mode this is ignored by ``flip.get_dataframe``.
    """
    client_cfg = Path(__file__).parent.parent / "config" / "config_fed_client.json"
    if client_cfg.exists():
        try:
            return json.loads(client_cfg.read_text()).get("query", "")
        except Exception:
            return ""
    return ""


def load_config() -> dict:
    """Load the user-supplied config.json that sits next to this script."""
    config_path = Path(__file__).parent.resolve() / "config.json"
    with open(config_path) as f:
        return json.load(f)


def build_datalist(flip: FLIP, dataframe: pd.DataFrame, project_id: str) -> list[dict[str, str]]:
    """Walk every accession in the cohort and return the QC-passing image/label pairs.

    Each converted ``input_*.nii.gz`` must have a sibling ``label_*.nii.gz`` (added
    during the data-enrichment stage on the platform, or shipped with the dev dataset).

    Args:
        flip (FLIP): The FLIP client instance.
        dataframe (pd.DataFrame): The cohort dataframe containing accession IDs.
        project_id (str): The FLIP project ID.

    Returns:
        list[dict[str, str]]: MONAI-compatible items with ``image`` and ``label`` paths.
    """
    datalist: list[dict[str, str]] = []
    n_images = 0

    for accession_id in tqdm(dataframe["accession_id"], desc="Preparing dataset", unit="accession"):
        try:
            accession_folder_path = flip.get_by_accession_number(
                project_id, accession_id, resource_type=[ResourceType.NIFTI]
            )
        except Exception as err:
            logger.info("⚠️ Could not fetch images for accession_id=%s: %s", accession_id, err)
            continue

        all_images = list(accession_folder_path.rglob("input_*.nii.gz"))
        n_images += len(all_images)

        for img in all_images:
            # For each image, find the corresponding segmentation mask.
            seg = str(img).replace("/input_", "/label_")
            if not Path(seg).exists():
                logger.info("⚠️ No matching segmentation for %s", img.name)
                continue

            try:
                img_header = nib.load(str(img))
                seg_header = nib.load(seg)
            except nib.filebasedimages.ImageFileError as err:
                logger.info("⚠️ Invalid image pair for %s: %s", img.name, err)
                continue

            # QC: 3D image whose segmentation has the same shape.
            if len(img_header.shape) != 3:
                logger.info("⚠️ Skipping non-3D image %s", img.name)
                continue
            if img_header.shape != seg_header.shape:
                logger.info(
                    "⚠️ Shape mismatch for %s: image=%s label=%s", img.name, img_header.shape, seg_header.shape
                )
                continue

            datalist.append({"image": str(img), "label": seg})

    if not datalist:
        # Fail loudly: torch's downstream num_samples=0 error reads like an app bug, when the usual
        # cause is a missing data-enrichment step (no label_*.nii.gz uploaded next to the images).
        raise ValueError(
            f"No image/label pairs found: {n_images} image(s) across {len(dataframe)} accession(s), "
            "none with a matching label_*.nii.gz. Was the data-enrichment (label upload) step run?"
        )

    logger.info("Dataset ready: %d image/label pairs", len(datalist))
    return datalist


def split_datalist(datalist: list, val_split: float) -> tuple:
    """Deterministic train/validation split — no shuffling, matching the legacy trainer."""
    train_items, val_items = np.split(datalist, [int((1 - val_split) * len(datalist))])
    logger.info("Split → train=%d, val=%d", len(train_items), len(val_items))
    return train_items, val_items


def train_one_epoch(
    model: torch.nn.Module,
    loader: DataLoader,
    loss_fn: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
) -> tuple[float, int]:
    """Run one local training epoch. Returns (per-image average loss, batch count)."""
    model.train()
    running_loss = 0.0
    num_images = 0
    for i, batch in enumerate(loader):
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
        logger.info("Iteration: %d, Loss: %s", i + 1, cost.cpu().item())

    return (running_loss / num_images if num_images > 0 else 0.0, len(loader))


@torch.inference_mode()
def validate(
    model: torch.nn.Module,
    loader: DataLoader,
    loss_fn: torch.nn.Module,
    inferer,
    device: torch.device,
    post_pred: AsDiscrete,
    post_pred_gt: AsDiscrete,
    dice_acc: DiceMetric,
) -> tuple[float, float]:
    """Run a validation pass with sliding-window inference. Returns (average loss, mean Dice).

    The metric is reset once and aggregated once over the whole pass: ``DiceMetric.aggregate``
    returns the running mean over every sample fed since ``reset()``, so calling it per batch
    and averaging the per-batch values would double-count earlier batches.
    """
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

    avg_loss = running_loss / max(num_batches, 1)
    avg_dice = float(dice_acc.aggregate().item()) if num_batches > 0 else 0.0
    return avg_loss, avg_dice


def main() -> None:
    args = parse_args()
    config = load_config()
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    logger.info("Device: %s", device)

    model = get_model().to(device)
    loss_fn = DiceCELoss(
        to_onehot_y=True, softmax=True, squared_pred=False, batch=True, lambda_ce=0.2, lambda_dice=0.8
    )
    # The optimizer is created once and persists across the whole flare.is_running() loop (never
    # reset per global round), matching the legacy executor's optimizer lifecycle.
    optimizer = torch.optim.Adam(model.parameters(), lr=config["LEARNING_RATE"])

    post_pred = AsDiscrete(argmax=True, to_onehot=2)
    post_pred_gt = AsDiscrete(to_onehot=2)
    dice_acc = DiceMetric(include_background=False, reduction="mean")
    inferer = get_sliding_window_inferer(device)

    flip = FLIP()
    query = load_query()
    dataframe = flip.get_dataframe(args.project_id, query)
    if "accession_id" not in dataframe.columns:
        raise ValueError("The dataframe must contain 'accession_id' column.")

    datalist = build_datalist(flip, dataframe, args.project_id)
    train_items, val_items = split_datalist(datalist, config["VAL_SPLIT"])

    train_loader = DataLoader(
        Dataset(train_items, transform=get_train_transforms()),
        batch_size=config["BATCH_SIZE"],
        shuffle=True,
        num_workers=1,
    )
    val_loader = DataLoader(
        Dataset(val_items, transform=get_val_transforms()), batch_size=1, shuffle=False, num_workers=1
    )

    flare.init()
    writer = SummaryWriter()

    while flare.is_running():
        input_model = flare.receive()
        if input_model is None:
            break

        if flare.is_train():
            global_round = input_model.current_round or 0
            torch_weights = {k: torch.as_tensor(v) for k, v in input_model.params.items()}
            model.load_state_dict(torch_weights)

            # Best-model selection (FLIP#673): evaluate the received global model before local
            # training mutates it. The metrics ride back on the returned FLModel (DXO meta
            # INITIAL_METRICS) and drive the server-side IntimeModelSelector. Gated on
            # BEST_MODEL_METRIC so runs without selection skip the extra validation pass.
            global_val_metrics = None
            if config.get("BEST_MODEL_METRIC"):
                g_loss, g_dice = validate(
                    model, val_loader, loss_fn, inferer, device, post_pred, post_pred_gt, dice_acc
                )
                global_val_metrics = {"VAL_LOSS": g_loss, "VAL_DICE": g_dice}
                logger.info("Global model validation — loss: %.4f, dice: %.4f", g_loss, g_dice)

            n_iterations = 0
            last_val_loss = 0.0
            last_val_dice = 0.0
            for epoch in range(config["LOCAL_ROUNDS"]):
                train_loss, n_batches = train_one_epoch(model, train_loader, loss_fn, optimizer, device)
                n_iterations += n_batches

                if epoch % config.get("VALIDATE_EVERY", 1) == 0:
                    last_val_loss, last_val_dice = validate(
                        model, val_loader, loss_fn, inferer, device, post_pred, post_pred_gt, dice_acc
                    )
                    logger.info(
                        "Validation — Epoch: %d, Loss: %.4f, Mean Dice: %.4f",
                        epoch + 1,
                        last_val_loss,
                        last_val_dice,
                    )

                # Per-epoch points: the "@epoch" tag suffix names the x-axis (the FLIP analytics
                # bridge parses "<label>[@<x_label>]" — see FLIP#148) and the cumulative epoch is
                # the coordinate. Non-validation epochs re-send the previous validation values,
                # matching the legacy trainer.
                cumulative_epoch = global_round * config["LOCAL_ROUNDS"] + epoch + 1
                writer.add_scalar("TRAIN_LOSS@epoch", float(train_loss), global_step=cumulative_epoch)
                writer.add_scalar("VAL_LOSS@epoch", float(last_val_loss), global_step=cumulative_epoch)
                writer.add_scalar("VAL_DICE@epoch", float(last_val_dice), global_step=cumulative_epoch)

            # Send back DIFF: the server aggregates the diffs directly and applies the average
            # to the global model (stock NVFLARE semantics).
            new_state = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
            diff = {k: new_state[k] - torch_weights[k].detach().cpu().numpy() for k in new_state}
            flare.send(
                flare.FLModel(
                    params=diff,
                    params_type="DIFF",
                    # metrics of the *received* global model — the framework treats params+metrics
                    # as "evaluation on the global model" and carries them as INITIAL_METRICS for
                    # IntimeModelSelector.
                    metrics=global_val_metrics,
                    # Batches across ALL local epochs this round (as in the xray app); the DP clip bound scales with it.
                    meta={"NUM_STEPS_CURRENT_ROUND": n_iterations},
                )
            )

        elif flare.is_evaluate():
            model.load_state_dict({k: torch.as_tensor(v) for k, v in input_model.params.items()})
            test_loss, test_dice = validate(
                model, val_loader, loss_fn, inferer, device, post_pred, post_pred_gt, dice_acc
            )
            logger.info("Model evaluation — loss: %.4f, dice: %.4f", test_loss, test_dice)
            writer.add_scalar("TEST_DICE", test_dice, global_step=0)
            # val_acc lands in the cross-validation results.json via ValidationJsonGenerator,
            # matching the legacy validator's reply payload.
            flare.send(flare.FLModel(metrics={"val_acc": test_dice}))

        else:
            logger.warning("Received unknown task; ignoring.")


if __name__ == "__main__":
    main()
