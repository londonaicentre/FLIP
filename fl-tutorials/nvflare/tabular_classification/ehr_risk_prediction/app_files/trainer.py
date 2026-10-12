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

"""NVFLARE Client-API trainer for the EHR risk-prediction tutorial.

Trains a small MLP to predict type-2-diabetes onset from OMOP tabular features (person
demographics + pre-diagnosis condition history). The entire cohort arrives through
``flip.get_dataframe`` — no imaging is fetched, so this is the tabular counterpart of the
imaging tutorials' trainer.
"""

import argparse
import json
import logging
import os
from pathlib import Path

import numpy as np
import nvflare.client as flare
import pandas as pd
import torch
from feature_engineering import (
    apply_preprocessor,
    binary_accuracy,
    fit_preprocessor,
    partition_for_client,
    positive_class_weight,
    safe_auroc,
    select_features,
    split_frame,
    to_tensors,
)
from flip import FLIP
from flip.constants import FlipConstants
from models import get_model
from nvflare.client.tracking import SummaryWriter
from torch.utils.data import DataLoader, TensorDataset

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


def resolve_dataframe(site_name: str, project_id: str, query: str) -> pd.DataFrame:
    """Load this client's cohort dataframe.

    Deployed (LOCAL_DEV=false): fetch from the trust's data-access-api via
    ``flip.get_dataframe`` — each trust already holds a disjoint cohort, so no partitioning.

    Simulator (LOCAL_DEV=true): resolve a local CSV with the arkplus-style precedence —
    per-site ``SITE{N}_DATAFRAME`` env first ("site-1" -> ``SITE1_DATAFRAME``), else the
    shared ``DEV_DATAFRAME``, which is then sliced into this site's partition by
    ``person_id`` modulo (``DEV_NUM_PARTITIONS``, default 2) so the run stays genuinely
    federated even off a single file.
    """
    if not FlipConstants.LOCAL_DEV:
        return FLIP().get_dataframe(project_id, query)

    site_env = site_name.replace("-", "").upper() if site_name else ""  # "site-1" -> "SITE1"
    per_site_path = os.environ.get(f"{site_env}_DATAFRAME") if site_env else None
    path = per_site_path or os.environ.get("DEV_DATAFRAME")
    if not path or not Path(path).exists():
        raise RuntimeError(
            f"No local dataframe for site {site_name!r}: set SITE{{N}}_DATAFRAME or DEV_DATAFRAME "
            f"(got {path!r}) — run `make -C fl-tutorials download-synthea-data` first."
        )
    dataframe = pd.read_csv(path)
    if per_site_path:
        return dataframe
    num_partitions = int(os.environ.get("DEV_NUM_PARTITIONS", "2"))
    return partition_for_client(dataframe, site_name, num_partitions)


def build_loaders(config: dict, dataframe: pd.DataFrame) -> tuple[dict[str, DataLoader], torch.Tensor]:
    """Split, preprocess and wrap the cohort into train/val/test loaders.

    Returns:
        tuple[dict[str, DataLoader], torch.Tensor]: The loaders keyed ``train``/``val``/``test``
        and the ``pos_weight`` for the training loss (from the train split's class balance).
    """
    train_df, val_df, test_df = split_frame(dataframe, config["VAL_SPLIT"], config["TEST_SPLIT"], config["SEED"])
    logger.info(f"Split → train={len(train_df)}, val={len(val_df)}, test={len(test_df)}")

    splits: dict[str, DataLoader] = {}
    stats = None
    pos_weight = torch.tensor(1.0)
    for name, split_df, shuffle in (("train", train_df, True), ("val", val_df, False), ("test", test_df, False)):
        features, labels = select_features(split_df, config["FEATURES"], config["LABEL_COLUMN"])
        if name == "train":
            # Imputation/standardisation statistics come from the local TRAIN split only —
            # never from val/test (leakage) and never from another site (nothing to federate).
            stats = fit_preprocessor(features)
            pos_weight = positive_class_weight(labels)
        x, y = to_tensors(apply_preprocessor(features, stats), labels)
        positives = int(y.sum().item())
        logger.info(f"{name}: {len(y)} persons, {positives} positive ({positives / max(len(y), 1):.1%})")
        splits[name] = DataLoader(TensorDataset(x, y), batch_size=config["BATCH_SIZE"], shuffle=shuffle)
    return splits, pos_weight


def run_epoch(
    model: torch.nn.Module,
    loader: DataLoader,
    device: torch.device,
    criterion: torch.nn.Module,
    optimizer: torch.optim.Optimizer | None = None,
) -> dict[str, float]:
    """One pass over the loader. Returns mean loss + AUROC + accuracy for the pass."""
    is_train = optimizer is not None
    model.train(is_train)

    losses: list[float] = []
    all_labels: list[np.ndarray] = []
    all_probs: list[np.ndarray] = []
    with torch.set_grad_enabled(is_train):
        for features, labels in loader:
            features, labels = features.to(device), labels.to(device)
            if is_train:
                optimizer.zero_grad()
            logits = model(features)
            loss = criterion(logits, labels)

            # Skip a non-finite batch instead of letting it poison the model: a single NaN/Inf
            # loss backpropagates into every weight via optimizer.step(), after which every
            # subsequent batch is NaN (same guard as the imaging trainers — FLIP#764).
            if not torch.isfinite(loss):
                logger.warning(f"Skipping batch with non-finite loss ({loss.item()})")
                continue

            if is_train:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()

            losses.append(loss.item())
            all_labels.append(labels.detach().cpu().numpy())
            all_probs.append(torch.sigmoid(logits).detach().cpu().numpy())

    labels_np = np.concatenate(all_labels) if all_labels else np.empty(0)
    probs_np = np.concatenate(all_probs) if all_probs else np.empty(0)
    return {
        "loss": float(np.mean(losses)) if losses else float("nan"),
        "auroc": safe_auroc(labels_np, probs_np),
        "accuracy": binary_accuracy(labels_np, probs_np),
    }


def _publish(writer: SummaryWriter, label: str, value: float, step: int) -> None:
    """Push a scalar through SummaryWriter, swapping NaN for 0.0 like the imaging tutorials do."""
    if np.isnan(value):
        logger.warning(f"{label} is NaN — sending 0.0")
        value = 0.0
    writer.add_scalar(label, value, global_step=step)


def local_train(
    model: torch.nn.Module,
    loaders: dict[str, DataLoader],
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.ExponentialLR,
    criterion: torch.nn.Module,
    val_criterion: torch.nn.Module,
    device: torch.device,
    epochs: int,
    writer: SummaryWriter,
    global_round: int,
) -> int:
    """Train for ``epochs`` local epochs, streaming per-epoch metrics. Returns iteration count."""
    n_iterations = 0
    for epoch in range(epochs):
        train_metrics = run_epoch(model, loaders["train"], device, criterion, optimizer)
        val_metrics = run_epoch(model, loaders["val"], device, val_criterion)
        scheduler.step()
        n_iterations += len(loaders["train"])

        # Per-epoch scalars: the "@epoch" tag suffix names the x-axis (the FLIP analytics
        # bridge parses "<label>[@<x_label>]" — FLIP#148) and `step` (cumulative epoch) is
        # the coordinate.
        step = global_round * epochs + epoch + 1
        _publish(writer, "TRAIN_LOSS@epoch", train_metrics["loss"], step)
        _publish(writer, "VAL_LOSS@epoch", val_metrics["loss"], step)
        _publish(writer, "VAL-AUROC@epoch", val_metrics["auroc"], step)
        _publish(writer, "VAL-ACCURACY@epoch", val_metrics["accuracy"], step)
        logger.info(
            f"Epoch {step}: train_loss={train_metrics['loss']:.4f} val_loss={val_metrics['loss']:.4f} "
            f"val_auroc={val_metrics['auroc']:.4f}"
        )
    return n_iterations


def evaluate_global_model(
    model: torch.nn.Module,
    loaders: dict[str, DataLoader],
    val_criterion: torch.nn.Module,
    device: torch.device,
) -> dict[str, float]:
    """Evaluate the received global model on the local validation split for best-model selection.

    Runs BEFORE local training mutates the weights, so the metrics describe the aggregated
    global model the server just broadcast — they ride back on the returned ``FLModel`` and
    drive the server-side ``IntimeModelSelector`` (``BEST_MODEL_METRIC`` in config.json).
    NaNs are mapped to 0.0 so a degenerate split cannot poison the cross-client average.
    """
    metrics = run_epoch(model, loaders["val"], device, val_criterion)
    flat = {
        "VAL_LOSS": metrics["loss"],
        "VAL-AUROC": metrics["auroc"],
        "VAL-ACCURACY": metrics["accuracy"],
    }
    return {label: (0.0 if np.isnan(value) else float(value)) for label, value in flat.items()}


def load_global_weights(model: torch.nn.Module, input_model: flare.FLModel) -> dict:
    """Push the incoming server weights onto the local model and return them as torch tensors."""
    torch_weights = {k: torch.as_tensor(v) for k, v in input_model.params.items()}
    model.load_state_dict(torch_weights)
    return torch_weights


def main() -> None:
    args = parse_args()
    config = load_config()
    torch.manual_seed(config["SEED"])

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    flare.init()
    site_name = flare.get_site_name()
    writer = SummaryWriter()

    dataframe = resolve_dataframe(site_name, args.project_id, load_query())
    logger.info(f"Cohort dataframe for {site_name}: {len(dataframe)} persons")
    loaders, pos_weight = build_loaders(config, dataframe)

    model = get_model().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config["LR_START"])
    # gamma_lr is sized so the LR decays LR_START -> LR_END over one global round's worth of
    # local epochs; created once and persisting across the flare.is_running() loop, so from
    # round 2 onwards the LR keeps decaying below LR_END (matches the imaging tutorials).
    gamma_lr = (config["LR_END"] / config["LR_START"]) ** (1 / config["LOCAL_ROUNDS"])
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=gamma_lr)

    # pos_weight rebalances the ~6%-positive cohort in the training loss; validation/test
    # report the plain unweighted loss so sites with different prevalence stay comparable.
    criterion = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight.to(device))
    val_criterion = torch.nn.BCEWithLogitsLoss()

    while flare.is_running():
        input_model = flare.receive()
        if input_model is None:
            break

        if flare.is_train():
            original_weights = load_global_weights(model, input_model)
            global_round = input_model.current_round or 0

            # Best-model selection: evaluate the received global model before local training
            # mutates it. Gated on BEST_MODEL_METRIC so runs without selection skip the pass.
            global_val_metrics = None
            if config.get("BEST_MODEL_METRIC"):
                global_val_metrics = evaluate_global_model(model, loaders, val_criterion, device)

            n_iterations = local_train(
                model,
                loaders,
                optimizer,
                scheduler,
                criterion,
                val_criterion,
                device,
                epochs=config["LOCAL_ROUNDS"],
                writer=writer,
                global_round=global_round,
            )

            # Send back DIFF (matches the weight-update behaviour used with
            # FullModelShareableGenerator across the imaging tutorials).
            new_state = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
            diff = {k: new_state[k] - original_weights[k].detach().cpu().numpy() for k in new_state}
            flare.send(
                flare.FLModel(
                    params=diff,
                    params_type="DIFF",
                    metrics=global_val_metrics,
                    meta={"NUM_STEPS_CURRENT_ROUND": n_iterations},
                )
            )

        elif flare.is_evaluate():
            load_global_weights(model, input_model)
            test_metrics = run_epoch(model, loaders["test"], device, val_criterion)
            _publish(writer, "TEST_LOSS", test_metrics["loss"], 0)
            _publish(writer, "TEST-AUROC", test_metrics["auroc"], 0)
            _publish(writer, "TEST-ACCURACY", test_metrics["accuracy"], 0)
            flare.send(flare.FLModel(metrics={"loss": test_metrics["loss"]}))

        elif flare.is_submit_model():
            params = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
            flare.send(flare.FLModel(params=params, params_type="FULL"))

        else:
            logger.warning("Received unknown task; ignoring.")


if __name__ == "__main__":
    main()
