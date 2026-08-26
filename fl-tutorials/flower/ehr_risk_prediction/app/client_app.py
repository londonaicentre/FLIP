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

"""ehr-risk-prediction: Flower ClientApp for EHR risk prediction on OMOP tabular data.

Trains a small MLP to predict type-2-diabetes onset from person demographics +
pre-diagnosis condition history. The whole cohort arrives through ``flip.get_dataframe``
— no imaging is fetched, making this the tabular counterpart of the imaging tutorials.

Hyperparameters come from the per-tutorial ``app/config.json`` rather than
``run_config`` — the base bundle ships a single ``pyproject.toml`` shared by
all tutorials, so config.json is the only per-tutorial knob that rides
through the upload flow.
"""

import json
import os
from logging import INFO
from pathlib import Path

import torch
from flip import FLIP
from flip.constants import FlipConstants
from flip.flower.privacy import flip_local_dp_mod
from flwr.app import ArrayRecord, ConfigRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.common import log
from torch.utils.data import DataLoader, TensorDataset

from app.feature_engineering import (
    apply_preprocessor,
    fit_preprocessor,
    partition_for_client,
    positive_class_weight,
    select_features,
    split_frame,
    to_tensors,
)
from app.models import get_model
from app.task import train_func, validate_func

app = ClientApp()


def _load_config() -> dict:
    config_path = Path(__file__).parent / "config.json"
    with open(config_path) as fh:
        return json.load(fh)


def _fetch_cohort(config: dict, context: Context, client_name: str):
    """Fetch this client's cohort dataframe via FLIP.

    Deployed: each trust's data-access-api already serves a disjoint cohort. LOCAL_DEV
    (the dev compose stack): every SuperNode reads the same mounted CSV, so it is sliced
    into this client's partition by ``person_id`` modulo — the injected trust count
    (``flip-min-clients``) doubles as the partition count.
    """
    project_id = context.run_config.get("flip-project-id", "ehr-flower-tutorial")
    query = context.run_config.get("flip-cohort-query", "*")
    log(INFO, f"Fetching FLIP dataframe project_id={project_id}")
    dataframe = FLIP().get_dataframe(project_id, query)
    if FlipConstants.LOCAL_DEV:
        num_partitions = int(context.run_config.get("flip-min-clients", 2))
        dataframe = partition_for_client(dataframe, client_name, num_partitions)
    log(INFO, f"Cohort dataframe for {client_name}: {len(dataframe)} persons")
    return dataframe


def _build_loaders(config: dict, dataframe) -> tuple[dict[str, DataLoader], torch.Tensor]:
    """Split, preprocess and wrap the cohort into train/val/test loaders (+ loss pos_weight)."""
    train_df, val_df, test_df = split_frame(dataframe, config["VAL_SPLIT"], config["TEST_SPLIT"], config["SEED"])
    log(INFO, f"Split → train={len(train_df)}, val={len(val_df)}, test={len(test_df)}")

    loaders: dict[str, DataLoader] = {}
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
        loaders[name] = DataLoader(TensorDataset(x, y), batch_size=config["BATCH_SIZE"], shuffle=shuffle)
    return loaders, pos_weight


def _load_model_on_device(msg: Message) -> tuple[torch.nn.Module, torch.device]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log(INFO, "Using device: %s", device)
    model = get_model()
    # strict=True (default) — server and client build get_model() from the same
    # factory, so any missing/unexpected key means the wire format has drifted
    # from the architecture; fail loudly instead of running with random weights
    # in mismatched layers.
    model.load_state_dict(msg.content["arrays"].to_torch_state_dict())
    model.to(device)
    return model, device


# The DP mod clips the update to `dp-clipping-norm` and adds Gaussian noise calibrated to
# (dp-epsilon, dp-delta) before the reply leaves the SuperNode — see pyproject.toml's
# [tool.flwr.app.config]. It is applied to @app.train only; @app.evaluate below is untouched.
# The MLP is all-float parameters, so the whole update is privatised.
@app.train(mods=[flip_local_dp_mod])
def train(msg: Message, context: Context) -> Message:
    """Train the risk MLP for ``LOCAL_ROUNDS`` epochs against the local cohort."""
    config = _load_config()
    torch.manual_seed(int(config["SEED"]))
    local_rounds = int(config["LOCAL_ROUNDS"])
    lr_start = float(config["LR_START"])
    lr_end = float(config["LR_END"])

    client_name = os.getenv("SUPERNODE_NAME", "unknown_client")
    global_round = int(msg.content["config"]["server-round"]) - 1

    dataframe = _fetch_cohort(config, context, client_name)
    loaders, pos_weight = _build_loaders(config, dataframe)

    model, device = _load_model_on_device(msg)

    optimizer = torch.optim.Adam(model.parameters(), lr=lr_start)
    gamma_lr = (lr_end / lr_start) ** (1 / max(local_rounds, 1))
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=gamma_lr)

    # pos_weight rebalances the ~6%-positive cohort in the training loss; validation/test
    # report the plain unweighted loss so sites with different prevalence stay comparable.
    criterion = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight.to(device))
    val_criterion = torch.nn.BCEWithLogitsLoss()

    per_epoch_metrics: dict[str, float] = {}
    last_train: dict = {}
    last_val: dict = {}
    for epoch in range(local_rounds):
        log(INFO, f"Starting local epoch {epoch + 1}/{local_rounds}")
        last_train = train_func(model, loaders["train"], optimizer, criterion, device)
        last_val = validate_func(model, loaders["val"], val_criterion, device)
        scheduler.step()

        # Per-epoch points: "@epoch" names the x-axis and ".x_<N>" is the coordinate (the
        # cumulative epoch count) — see the "<label>[@<x_label>][.x_<V>]" key grammar in
        # flip.flower.metrics (FLIP#148).
        cumulative_epoch = global_round * local_rounds + epoch + 1
        per_epoch_metrics[f"train_loss@epoch.x_{cumulative_epoch}"] = last_train["loss"]
        per_epoch_metrics[f"val_loss@epoch.x_{cumulative_epoch}"] = last_val["loss"]
        per_epoch_metrics[f"val_auroc@epoch.x_{cumulative_epoch}"] = last_val["auroc"]
        per_epoch_metrics[f"val_accuracy@epoch.x_{cumulative_epoch}"] = last_val["accuracy"]

    metrics: dict[str, float] = {
        "train_loss": last_train["loss"],
        "val_loss": last_val["loss"],
        "val_auroc": last_val["auroc"],
        "val_accuracy": last_val["accuracy"],
        "num-examples": len(loaders["train"].dataset),
        "num-iterations": len(loaders["train"]) * local_rounds,
        **per_epoch_metrics,
    }

    model_record = ArrayRecord(model.state_dict())
    site_config = ConfigRecord({"site": client_name})
    metric_record = MetricRecord(metrics)
    content = RecordDict({"arrays": model_record, "metrics": metric_record, "config": site_config})
    return Message(content=content, reply_to=msg)


@app.evaluate()
def evaluate(msg: Message, context: Context) -> Message:
    """Evaluate the global model on the local held-out test split."""
    config = _load_config()
    client_name = os.getenv("SUPERNODE_NAME", "unknown_client")

    dataframe = _fetch_cohort(config, context, client_name)
    loaders, _ = _build_loaders(config, dataframe)

    model, device = _load_model_on_device(msg)
    test_metrics = validate_func(model, loaders["test"], torch.nn.BCEWithLogitsLoss(), device)

    # test_auroc is the one interpretable number this app is judged on, and what
    # best-model selection scores (best-model-metric = "test_auroc"). The ".x_0" suffix
    # plots the single test point at x=0 by convention.
    metrics: dict[str, float] = {
        "test_loss": float(test_metrics["loss"]),
        "test_loss.x_0": float(test_metrics["loss"]),
        "test_auroc": float(test_metrics["auroc"]),
        "test_auroc.x_0": float(test_metrics["auroc"]),
        "test_accuracy": float(test_metrics["accuracy"]),
        "num-examples": len(loaders["test"].dataset),
    }

    site_config = ConfigRecord({"site": client_name})
    return Message(
        content=RecordDict({"metrics": MetricRecord(metrics), "config": site_config}),
        reply_to=msg,
    )
