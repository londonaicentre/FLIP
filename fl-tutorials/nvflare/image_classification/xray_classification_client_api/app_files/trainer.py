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

import numpy as np
import nvflare.client as flare
import pandas as pd
import pydicom
import torch
from data_utils import Lesion, LesionDict, get_labels_from_radiology_row, get_lesion_label, get_xray_transforms
from flip import FLIP
from flip.constants import ResourceType
from loss_and_metrics import compute_precision_recall_f1, get_bce_loss
from models import get_model
from monai.data import DataLoader, Dataset
from nvflare.client.tracking import SummaryWriter
from tqdm import tqdm

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
        config = json.load(f)

    # value_to_numerical keys come from JSON as strings — coerce to int and validate.
    value_to_numerical = {int(k): v for k, v in config["value_to_numerical"].items()}
    if 0 not in value_to_numerical or 1 not in value_to_numerical:
        raise ValueError("value_to_numerical must contain mappings for 0 and 1.")
    config["value_to_numerical"] = value_to_numerical

    # The "-1" key in LESIONS is reserved for the normality label and must be
    # split out before turning the dict into a LesionDict of trainable classes.
    lesions = dict(config["LESIONS"])
    normal_key = lesions.pop("-1", "Normal")
    config["LESIONS"] = lesions
    config["NORMAL_KEY"] = normal_key
    return config


def build_datalist(
    flip: FLIP,
    dataframe: pd.DataFrame,
    project_id: str,
    lesions: LesionDict,
    value_to_numerical: dict,
    normal_key: str,
) -> list:
    """
    Iterate the cohort dataframe and return MONAI-compatible image items.

    Args:
        flip (FLIP): The FLIP client instance.
        dataframe (pd.DataFrame): The cohort dataframe containing accession IDs and labels.
        project_id (str): The FLIP project ID.
        lesions (LesionDict): The dictionary of lesions to extract labels for.
        value_to_numerical (dict): Mapping of label values to numerical representations.
        normal_key (str): The key in the dataframe that represents normal cases.

    Returns:
        datalist (list[dict[str, str]]): List of dicts containing image paths and corresponding labels.
    """
    datalist: list[dict[str, str]] = []

    for _, row in tqdm(
        dataframe.iterrows(),
        total=len(dataframe),
        desc="Processing cohort",
        unit="accession",
    ):
        accession_id = row["accession_id"]

        # Extract the pathology labels for this accession ID from the dataframe row
        pathology_dict = get_labels_from_radiology_row(row, lesions, value_to_numerical, normal_key)

        try:
            accession_folder_path = flip.get_by_accession_number(
                project_id, accession_id, resource_type=[ResourceType.DICOM]
            )
        except Exception as err:
            logger.info("⚠️ Could not fetch images for accession_id=%s: %s", accession_id, err)
            continue

        # get all images in the accession folder that match the pattern "*.dcm"
        all_images = list(accession_folder_path.rglob("*.dcm"))

        for img in all_images:
            try:
                _ = pydicom.dcmread(str(img), stop_before_pixels=True)
            except Exception as e:
                logger.warning("Skipping invalid DICOM %s: %s", img.name, e)
                continue

            item = {"image": str(img)}
            item.update(pathology_dict)
            datalist.append(item)

    logger.info("Dataset ready: %d images", len(datalist))
    return datalist


def split_datalist(datalist: list, val_split: float, test_split: float) -> tuple:
    """Deterministic 3-way split — splits happen before any shuffling/sampling."""
    train, val, test = np.split(
        datalist,
        [int(len(datalist) * (1 - val_split - test_split)), int(len(datalist) * (1 - test_split))],
    )
    logger.info(f"Split → train={len(train)}, val={len(val)}, test={len(test)}")
    return train, val, test


def log_class_distribution(datalist: list, dataset_name: str, lesions: LesionDict) -> None:
    logger.info(f"\n{'=' * 80}\n{dataset_name} dataset ({len(datalist)} samples)\n{'=' * 80}")
    for lesion in lesions.items:
        labels = [item[lesion.lesion] for item in datalist]
        pos = sum(1 for x in labels if x == 1)
        neg = sum(1 for x in labels if x == 0)
        masked = sum(1 for x in labels if x == -1)
        ratio = pos / (pos + neg) * 100 if pos + neg > 0 else 0.0
        logger.info(f"  {lesion.lesion:20s}: {pos:4d} pos, {neg:4d} neg, {masked:4d} masked  ({ratio:.2f}% pos)")


def epoch_loop(
    model: torch.nn.Module,
    dataloader: DataLoader,
    lesions: LesionDict,
    device: torch.device,
    optimizer: torch.optim.Optimizer | None,
    epoch: int,
    phase: str,
) -> dict:
    """Single pass over the dataloader. Returns batch metrics keyed by lesion."""
    is_train = optimizer is not None
    model.train(is_train)

    metrics: dict = {"loss": [], "precision": {}, "recall": {}, "f1-score": {}}
    for name in lesions.get_lesion_list():
        metrics["precision"][name] = []
        metrics["recall"][name] = []
        metrics["f1-score"][name] = []

    with torch.set_grad_enabled(is_train):
        for i, batch in enumerate(dataloader):
            images = batch["image"].to(device)
            labels = get_lesion_label(batch, lesions).to(device)

            labels_np = labels.detach().cpu().numpy()
            batch_info = f"Epoch {epoch + 1}, {phase} batch {i + 1}/{len(dataloader)} (size={labels.shape[0]}) - "
            for idx, lesion in enumerate(lesions.items):
                valid = labels_np[:, idx][labels_np[:, idx] != -1]
                if len(valid) > 0:
                    batch_info += f"{lesion.lesion}: {int(np.sum(valid == 1))}p/{int(np.sum(valid == 0))}n; "
                else:
                    batch_info += f"{lesion.lesion}: all masked; "
            logger.info(batch_info)

            # A fully-masked batch (every label -1) carries no supervision signal: the clamped loss
            # would be a flat 0.0 that trains nothing and drags the epoch mean down (pre-clamp, the
            # NaN it produced was excluded from the mean by np.nanmean). Skip it loudly so a
            # systematic label degeneracy (e.g. a broken label join) stays visible (FLIP#764).
            if (labels == -1).all():
                logger.warning(f"{phase} batch {i + 1}/{len(dataloader)}: all labels masked (-1), skipping batch")
                continue

            if is_train:
                optimizer.zero_grad()
            output = model(images)
            loss = get_bce_loss(output, labels)

            # Skip a non-finite batch instead of letting it poison the model: a single NaN/Inf loss
            # backpropagates into every weight via optimizer.step(), after which every subsequent
            # batch is NaN and the whole pass reports loss=nan (see FLIP#764).
            if not torch.isfinite(loss):
                logger.warning(f"Skipping {phase} batch {i + 1}/{len(dataloader)}: non-finite loss ({loss.item()})")
                continue

            if is_train:
                loss.backward()
                # Gradient clipping bounds the update so an exploding gradient on one batch can't
                # diverge the model to NaN within a single optimizer step.
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()

            metrics["loss"].append(loss.item())
            probs = torch.sigmoid(output)
            for name in lesions.get_lesion_list():
                precision, recall, f1 = compute_precision_recall_f1(probs, labels, name, lesions=lesions)
                metrics["precision"][name].append(precision)
                metrics["recall"][name].append(recall)
                metrics["f1-score"][name].append(f1)

    return metrics


def _safe_mean(values: list) -> float:
    if not values:
        return float("nan")
    return float(np.nanmean(values))


def _publish(writer: SummaryWriter, label: str, value: float, step: int) -> None:
    """Push a scalar through SummaryWriter, swapping NaN for 0.0 like the legacy code did."""
    if np.isnan(value):
        logger.warning(f"{label} is NaN — sending 0.0")
        value = 0.0
    writer.add_scalar(label, value, global_step=step)


def aggregate_and_publish(
    train_metrics: dict,
    val_metrics: dict | None,
    writer: SummaryWriter,
    lesions: LesionDict,
    step: int,
) -> None:
    _publish(writer, "TRAIN_LOSS", _safe_mean(train_metrics["loss"]), step)
    # Only publish VAL_* scalars when validation actually ran this epoch (per VALIDATE_EVERY) —
    # emitting a 0.0 placeholder would inject spurious zeros into the validation series.
    if val_metrics is not None:
        _publish(writer, "VAL_LOSS", _safe_mean(val_metrics["loss"]), step)

    for metric in ["f1-score", "precision", "recall"]:
        for name in lesions.get_lesion_list():
            # Include the lesion name in the tag so each lesion writes to a distinct series.
            _publish(writer, f"TRAIN-{metric.upper()}-{name}", _safe_mean(train_metrics[metric][name]), step)
            if val_metrics is not None:
                _publish(writer, f"VAL-{metric.upper()}-{name}", _safe_mean(val_metrics[metric][name]), step)


def local_train(
    model: torch.nn.Module,
    train_loader: DataLoader,
    val_loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler._LRScheduler,
    lesions: LesionDict,
    device: torch.device,
    epochs: int,
    validate_every: int,
    writer: SummaryWriter,
    global_round: int,
) -> int:
    """Train for `epochs` local epochs and stream metrics. Returns total iteration count."""
    n_iterations = 0
    for epoch in range(epochs):
        train_metrics = epoch_loop(model, train_loader, lesions, device, optimizer, epoch, "Train")
        n_iterations += len(train_metrics["loss"])

        val_metrics = None
        if epoch % validate_every == 0:
            val_metrics = epoch_loop(model, val_loader, lesions, device, optimizer=None, epoch=epoch, phase="Val")

        scheduler.step()

        step = global_round * epochs + epoch + 1
        aggregate_and_publish(train_metrics, val_metrics, writer, lesions, step)

    return n_iterations


def cross_site_validate(
    model: torch.nn.Module,
    test_loader: DataLoader,
    lesions: LesionDict,
    device: torch.device,
    writer: SummaryWriter,
) -> dict:
    """Run the cross-site validation pass against the held-out test split."""
    model.eval()
    metrics: dict = {"loss": [], "precision": {}, "recall": {}, "f1-score": {}}
    for name in lesions.get_lesion_list():
        metrics["precision"][name] = []
        metrics["recall"][name] = []
        metrics["f1-score"][name] = []

    with torch.no_grad():
        for i, batch in enumerate(test_loader):
            images = batch["image"].to(device)
            labels = get_lesion_label(batch, lesions).to(device)

            # Skip fully-masked batches: with the clamped loss they would contribute a spurious 0.0
            # to the test mean, where the pre-clamp NaN was excluded by np.nanmean in _safe_mean.
            if (labels == -1).all():
                logger.warning(f"Test batch {i + 1}/{len(test_loader)}: all labels masked (-1), skipping batch")
                continue

            output = model(images)
            metrics["loss"].append(get_bce_loss(output, labels).item())
            probs = torch.sigmoid(output)
            for name in lesions.get_lesion_list():
                precision, recall, f1 = compute_precision_recall_f1(probs, labels, name, lesions=lesions)
                metrics["precision"][name].append(precision)
                metrics["recall"][name].append(recall)
                metrics["f1-score"][name].append(f1)

    writer.add_scalar("TEST_LOSS", _safe_mean(metrics["loss"]), global_step=0)
    for metric in ["f1-score", "precision", "recall"]:
        for name in lesions.get_lesion_list():
            # Include the lesion name in the tag so each lesion writes to a distinct series
            # (matches aggregate_and_publish; without it every lesion collapses onto one tag).
            writer.add_scalar(f"TEST-{metric.upper()}-{name}", _safe_mean(metrics[metric][name]), global_step=0)

    return metrics


def load_global_weights(model: torch.nn.Module, input_model: flare.FLModel) -> dict:
    """Push the incoming server weights onto the local model and return them as torch tensors."""
    torch_weights = {k: torch.as_tensor(v) for k, v in input_model.params.items()}
    model.load_state_dict(torch_weights)
    return torch_weights


def main() -> None:
    args = parse_args()
    config = load_config()

    lesions = LesionDict(items=[Lesion(id=int(k), lesion=v) for k, v in config["LESIONS"].items()])
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    model = get_model().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config["LR_START"])
    # gamma_lr is sized so the LR decays LR_START -> LR_END over one global round's worth of local
    # epochs (LOCAL_ROUNDS steps). The optimizer/scheduler are created once and persist across the
    # whole flare.is_running() loop (never reset per global round), so from round 2 onwards the LR
    # keeps decaying below LR_END (continuous decay). This matches the legacy xray_classification
    # trainer's scheduler lifecycle.
    gamma_lr = (config["LR_END"] / config["LR_START"]) ** (1 / config["LOCAL_ROUNDS"])
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=gamma_lr)

    flip = FLIP()
    query = load_query()
    dataframe = flip.get_dataframe(args.project_id, query)
    if "accession_id" not in dataframe.columns:
        raise ValueError("The dataframe must contain 'accession_id' column.")

    datalist = build_datalist(
        flip, dataframe, args.project_id, lesions, config["value_to_numerical"], config["NORMAL_KEY"]
    )
    train_items, val_items, test_items = split_datalist(datalist, config["VAL_SPLIT"], config["TEST_SPLIT"])

    train_loader = DataLoader(
        Dataset(train_items, transform=get_xray_transforms()), batch_size=config["BATCH_SIZE"], shuffle=True
    )
    val_loader = DataLoader(
        Dataset(val_items, transform=get_xray_transforms(is_validation=True)),
        batch_size=config["BATCH_SIZE"],
        shuffle=False,
    )
    test_loader = DataLoader(
        Dataset(test_items, transform=get_xray_transforms(is_validation=True)),
        batch_size=config["BATCH_SIZE"],
        shuffle=False,
    )
    log_class_distribution(train_items, "TRAIN", lesions)
    log_class_distribution(val_items, "VAL", lesions)

    flare.init()
    writer = SummaryWriter()

    while flare.is_running():
        input_model = flare.receive()
        if input_model is None:
            break

        if flare.is_train():
            original_weights = load_global_weights(model, input_model)
            global_round = input_model.current_round or 0

            n_iterations = local_train(
                model,
                train_loader,
                val_loader,
                optimizer,
                scheduler,
                lesions,
                device,
                epochs=config["LOCAL_ROUNDS"],
                validate_every=config.get("VALIDATE_EVERY", 1),
                writer=writer,
                global_round=global_round,
            )

            # Send back DIFF (matches the original weight-update behaviour used with FullModelShareableGenerator).
            new_state = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
            diff = {k: new_state[k] - original_weights[k].detach().cpu().numpy() for k in new_state}
            flare.send(
                flare.FLModel(
                    params=diff,
                    params_type="DIFF",
                    meta={"NUM_STEPS_CURRENT_ROUND": n_iterations},
                )
            )

        elif flare.is_evaluate():
            load_global_weights(model, input_model)
            metrics = cross_site_validate(model, test_loader, lesions, device, writer)
            flare.send(flare.FLModel(metrics={"loss": _safe_mean(metrics["loss"])}))

        elif flare.is_submit_model():
            params = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
            flare.send(flare.FLModel(params=params, params_type="FULL"))

        else:
            logger.warning("Received unknown task; ignoring.")


if __name__ == "__main__":
    main()
