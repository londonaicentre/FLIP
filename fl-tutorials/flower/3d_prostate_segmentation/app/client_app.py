# Copyright (c) 2026 Flower Labs GmbH
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

"""3d-prostate-segmentation: Flower / MONAI ClientApp for prostate zonal segmentation (training-only).

One round, from a client's point of view:

1. Fetch this trust's cohort (``flip.get_dataframe``) and pull each study's NIfTI files
   (``flip.get_by_accession_number``); pick the T2-weighted series and its two enrichment masks.
2. Build the network from the shipped nnU-Net plan (``models.get_model``) and load the global weights.
3. Run ``local-epochs`` of ``train_helpers.train_seg`` — nnU-Net's recipe: whole-volume augmentation,
   patch tiling, deep-supervised Dice + binary cross-entropy, SGD with polynomial decay.
4. Reply with the updated weights and the metrics; the fl-server forwards them to the Central Hub on
   this client's behalf (clients hold no hub credential). Per-epoch points use the
   ``<label>[@<x_label>][.x_<V>]`` key grammar of ``flip.flower.metrics`` (FLIP#148).
"""

from __future__ import annotations

import json
from logging import INFO
from pathlib import Path
from typing import Any

import torch
from flip.flower.identity import check_splits_are_populated, client_identity, partition_cohort, partition_count
from flip.flower.privacy import flip_local_dp_mod
from flwr.app import ArrayRecord, ConfigRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.common import log
from monai.data import DataLoader, list_data_collate
from monai.transforms import Compose
from monai.utils import set_determinism

from app.data_loading import FLIP_BASE, build_dataset
from app.models import get_model, load_plan, set_deep_supervision_enabled
from app.preprocess import build_augmentations, build_case_transform, build_patch_iter
from app.task import build_criterion, build_optimizer, build_scheduler, evaluate_func, plan_geometry
from app.train_helpers import train_seg

# Flower ClientApp
app = ClientApp()


def _load_config() -> dict[str, Any]:
    """``config.json``: the job type plus the prostate-specific settings the run-config cannot carry.

    The platform only accepts ``--run-config`` keys the standard template declares, so ``MODALITY`` and
    the ``TRAIN`` block (``train_seg``'s ``conf``) live here, the way xray_classification does it.
    """
    with open(Path(__file__).with_name("config.json")) as handle:
        return json.load(handle)


def _fetch_cohort(run_config: dict[str, Any], context: Context) -> FLIP_BASE:
    flip_utils = FLIP_BASE()
    flip_utils.project_id = str(run_config.get("flip-project-id", "prostate-flower-tutorial"))
    flip_utils.query = str(run_config.get("flip-cohort-query", "*"))
    flip_utils.fetch_dataframe()
    # Slice the shared dev cohort so the simulated sites really differ; a no-op off LOCAL_DEV.
    flip_utils.dataframe = partition_cohort(flip_utils.dataframe, context)
    log(
        INFO,
        "FLIP dataframe has %d rows (%d studies).",
        len(flip_utils.dataframe),
        flip_utils.dataframe["accession_id"].nunique(),
    )
    return flip_utils


def _load_global_model(msg: Message, conf: dict[str, Any], device: torch.device) -> torch.nn.Module:
    model = get_model()
    # strict=True (default) — server and client build get_model() from the same plan, so any
    # missing/unexpected key means the wire format has drifted from the architecture; better to
    # fail than train with random weights in mismatched layers.
    model.load_state_dict(msg.content["arrays"].to_torch_state_dict())
    set_deep_supervision_enabled(bool(conf.get("deep_supervision", True)), network=model)
    return model.to(device)


# The DP mod clips the update to `dp-clipping-norm` and adds Gaussian noise calibrated to
# (dp-epsilon, dp-delta) before the reply leaves the SuperNode — see pyproject.toml's
# [tool.flwr.app.config]. It is applied to @app.train only; @app.evaluate below is untouched.
# DynUNet with affine instance norm has only floating-point tensors, so every array is privatised
# (the spleen UNet's integer batch-norm counters, which the mod has to skip, do not arise here).
@app.train(mods=[flip_local_dp_mod])
def train(msg: Message, context: Context) -> Message:
    """Train the model on local data, validating after every epoch."""
    run_config = context.run_config
    num_rounds = int(run_config.get("num-server-rounds", 1))
    local_epochs = int(run_config.get("local-epochs", 1))
    learning_rate = float(run_config.get("learning-rate", 0.01))
    val_split = float(run_config.get("val-split", 0.2))
    test_split = float(run_config.get("test-split", 0.2))
    batch_size = int(run_config.get("batch-size", 1))
    config = _load_config()
    conf: dict[str, Any] = config["TRAIN"]
    modality: str = config["MODALITY"]

    # NOTE this needs to match the name of the trust in the central hub database
    client_name = client_identity(context)
    # global_round from server is 1-based - convert to 0-based for easier calculations of round numbers during training
    global_round = int(msg.content["config"]["server-round"]) - 1
    # A different augmentation draw per round, reproducible per (seed, round).
    set_determinism(seed=int(conf.get("seed", 42)) + global_round)

    if val_split + test_split >= 1.0:
        # fl-server sees the raised error and forwards it to the Central Hub via
        # handle_client_exception; it is also responsible for transitioning the model status to ERROR.
        raise ValueError("Invalid split configuration: val_split + test_split must be < 1.0")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log(INFO, "Training on device: %s", device)

    # Data: cohort -> pulled files -> whole-volume preprocessing + augmentation -> patch grid
    flip_utils = _fetch_cohort(run_config, context)
    train_datalist, val_datalist = flip_utils.get_case_list(modality, val_split, test_split, is_test=False)
    check_splits_are_populated(
        {"train": len(train_datalist), "val": len(val_datalist)},
        cohort_rows=len(flip_utils.dataframe),
        client_name=client_name,
        num_partitions=partition_count(context),
    )
    geometry = plan_geometry(load_plan(), custom_patch=bool(conf.get("custom_patch", False)))
    preprocess = build_case_transform(geometry.crop_size, geometry.image_mean, geometry.image_std)
    patch_iter = build_patch_iter(geometry.patch_size)
    train_dataset = build_dataset(
        train_datalist, Compose([preprocess, build_augmentations()]), geometry.target_spacing, patch_iter
    )
    val_dataset = build_dataset(val_datalist, preprocess, geometry.target_spacing, patch_iter)
    # Each dataset item is a LIST of patches; list_data_collate flattens `batch-size` volumes' worth.
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, collate_fn=list_data_collate)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, collate_fn=list_data_collate)

    # Model + nnU-Net recipe
    model = _load_global_model(msg, conf, device)
    num_outputs = 1 + int(model.deep_supr_num) if conf.get("deep_supervision", True) else 1
    criterion = build_criterion(conf, num_outputs)
    optimizer = build_optimizer(model, learning_rate, conf)
    scheduler = build_scheduler(
        optimizer,
        total_epochs=num_rounds * local_epochs,
        start_epoch=global_round * local_epochs,
        power=float(conf.get("poly_power", 0.9)),
    )

    per_epoch_metrics: dict[str, float] = {}
    history: dict[str, list[float]] = {
        "train_loss": [],
        "val_loss": [],
        "val_dice_mean": [],
        "val_dice_wg": [],
        "val_dice_pz": [],
        "val_dice_tz": [],
    }
    for epoch in range(local_epochs):
        log(INFO, "Starting epoch %d/%d (round %d)", epoch + 1, local_epochs, global_round + 1)
        train_loss, val_loss, metrics = train_seg(
            conf, model, optimizer, scheduler, train_loader, val_loader, criterion, device
        )
        scheduler.step()
        epoch_values = {
            "train_loss": float(train_loss),
            "val_loss": float(val_loss),
            "val_dice_mean": float(metrics["val/mean_dice_avg"]),
            "val_dice_wg": float(metrics["val/wp_dice_avg"]),
            "val_dice_pz": float(metrics["val/pz_dice_avg"]),
            "val_dice_tz": float(metrics["val/tz_dice_avg"]),
        }
        # Per-epoch points: "@epoch" names the x-axis and ".x_<N>" is the coordinate (the cumulative
        # epoch count), so the fl-server forwards one Hub point per epoch (FLIP#148).
        cumulative_epoch = global_round * local_epochs + epoch + 1
        log(
            INFO,
            "Epoch %d/%d (round %d): train_loss %.4f, val_loss %.4f, val_dice_mean %.4f",
            epoch + 1,
            local_epochs,
            global_round + 1,
            epoch_values["train_loss"],
            epoch_values["val_loss"],
            epoch_values["val_dice_mean"],
        )
        for key, value in epoch_values.items():
            per_epoch_metrics[f"{key}@epoch.x_{cumulative_epoch}"] = value
            history[key].append(value)

    averages = {key: (sum(values) / len(values) if values else -1.0) for key, values in history.items()}
    metrics_record = MetricRecord(
        {
            **averages,
            "num-examples": len(train_datalist),
            "num-iterations": len(train_loader) * local_epochs,
            **per_epoch_metrics,
        }
    )
    content = RecordDict(
        {
            "arrays": ArrayRecord(model.state_dict()),
            "metrics": metrics_record,
            "config": ConfigRecord({"site": client_name}),
        }
    )
    return Message(content=content, reply_to=msg)


@app.evaluate()
def evaluate(msg: Message, context: Context) -> Message:
    """Score the global model on this client's test split, one whole volume at a time."""
    run_config = context.run_config
    val_split = float(run_config.get("val-split", 0.2))
    test_split = float(run_config.get("test-split", 0.2))
    config = _load_config()
    conf: dict[str, Any] = config["TRAIN"]
    modality: str = config["MODALITY"]

    client_name = client_identity(context)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log(INFO, "Evaluating on device: %s", device)

    flip_utils = _fetch_cohort(run_config, context)
    test_datalist = flip_utils.get_case_list(modality, val_split, test_split, is_test=True)
    check_splits_are_populated(
        {"test": len(test_datalist)},
        cohort_rows=len(flip_utils.dataframe),
        client_name=client_name,
        num_partitions=partition_count(context),
    )
    geometry = plan_geometry(load_plan(), custom_patch=bool(conf.get("custom_patch", False)))
    preprocess = build_case_transform(geometry.crop_size, geometry.image_mean, geometry.image_std)
    # Whole volumes (no patch grid): evaluate_func tiles them with a sliding window instead.
    test_loader = DataLoader(
        build_dataset(test_datalist, preprocess, geometry.target_spacing), batch_size=1, shuffle=False
    )

    model = _load_global_model(msg, conf, device)
    criterion = build_criterion({**conf, "deep_supervision": False}, num_outputs=1)

    site_config = ConfigRecord({"site": client_name})
    if len(test_loader.dataset) == 0:
        log(INFO, "No test data found!")
        metrics = {"test_loss": 0.0, "test_dice_mean": 0.0, "num-examples": 0}
        return Message(content=RecordDict({"metrics": MetricRecord(metrics), "config": site_config}), reply_to=msg)

    test_loss, dice = evaluate_func(model, test_loader, criterion, device, geometry.patch_size_zyx)
    log(
        INFO,
        "Evaluation completed for client %s. Test loss: %.4f, mean Dice: %.4f",
        client_name,
        test_loss,
        dice["dice_mean"],
    )

    # Test metrics are a single point, plotted at x=0 by convention using the label.x_0 suffix
    # understood by handle_client_metrics server-side.
    metrics = {"test_loss": float(test_loss), **{f"test_{key}": float(value) for key, value in dice.items()}}
    metrics.update({f"{key}.x_0": value for key, value in metrics.items()})
    metrics["num-examples"] = len(test_loader.dataset)
    return Message(content=RecordDict({"metrics": MetricRecord(metrics), "config": site_config}), reply_to=msg)
