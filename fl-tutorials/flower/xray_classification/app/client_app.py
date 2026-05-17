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

"""xray-classification: Flower / MONAI ClientApp for chest-X-ray multi-lesion classification.

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
from flwr.app import ArrayRecord, ConfigRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.common import log
from monai.data import DataLoader, Dataset

from app.data_loading import FLIP_BASE, Lesion, LesionDict
from app.models import get_model
from app.task import train_func, validate_func
from app.transforms import get_xray_transforms

app = ClientApp()


def _load_config() -> dict:
    config_path = Path(__file__).parent / "config.json"
    with open(config_path) as fh:
        return json.load(fh)


def _build_lesions(config: dict) -> tuple[LesionDict, dict, str]:
    lesions_raw = dict(config["LESIONS"])
    # The "-1" key in LESIONS holds the column name that, when "Yes", forces
    # every lesion to negative — it's a normal-override marker, not a lesion.
    normal_key = lesions_raw.pop("-1", "Normal")
    lesions = LesionDict(items=[Lesion(id=int(k), lesion=v) for k, v in lesions_raw.items()])

    value_to_numerical = {int(k): v for k, v in config["value_to_numerical"].items()}
    if 0 not in value_to_numerical or 1 not in value_to_numerical:
        raise ValueError("value_to_numerical must contain mappings for 0 and 1.")
    return lesions, value_to_numerical, normal_key


def _flatten_per_lesion(metrics: dict, prefix: str) -> dict[str, float]:
    return {f"{prefix}_{k}": float(v) for k, v in metrics.items()}


def _build_flip_utils(context: Context) -> FLIP_BASE:
    flip_utils = FLIP_BASE()
    flip_utils.project_id = context.run_config.get("flip-project-id", "xray-flower-tutorial")
    flip_utils.query = context.run_config.get("flip-cohort-query", "*")
    flip_utils.fetch_dataframe()
    return flip_utils


def _load_model_on_device(msg: Message) -> tuple[torch.nn.Module, torch.device]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log(INFO, "Using device: %s", device)
    model = get_model()
    model.load_state_dict(msg.content["arrays"].to_torch_state_dict(), strict=False)
    model.to(device)
    return model, device


@app.train()
def train(msg: Message, context: Context) -> Message:
    """Train chest-X-ray DenseNet for ``LOCAL_ROUNDS`` epochs against the local cohort."""
    config = _load_config()
    local_rounds = int(config["LOCAL_ROUNDS"])
    lr_start = float(config["LR_START"])
    lr_end = float(config["LR_END"])
    val_split = float(config["VAL_SPLIT"])
    test_split = float(config["TEST_SPLIT"])
    batch_size = int(config["BATCH_SIZE"])

    lesions, value_to_numerical, normal_key = _build_lesions(config)

    client_name = os.getenv("SUPERNODE_NAME", "unknown_client")
    global_round = int(msg.content["config"]["server-round"]) - 1

    if val_split + test_split >= 1.0:
        # fl-server sees the raised error and forwards it via handle_client_exception;
        # it transitions the model status to ERROR.
        raise ValueError("Invalid split configuration: val_split + test_split must be < 1.0")

    flip_utils = _build_flip_utils(context)
    train_datalist, val_datalist = flip_utils.get_image_and_label_list(
        lesions=lesions,
        value_to_numerical=value_to_numerical,
        normal_key=normal_key,
        val_split=val_split,
        test_split=test_split,
        is_test=False,
    )

    train_dataset = Dataset(train_datalist, transform=get_xray_transforms(is_validation=False))
    val_dataset = Dataset(val_datalist, transform=get_xray_transforms(is_validation=True))
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

    model, device = _load_model_on_device(msg)

    optimizer = torch.optim.Adam(model.parameters(), lr=lr_start)
    gamma_lr = (lr_end / lr_start) ** (1 / max(local_rounds, 1))
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=gamma_lr)

    per_epoch_metrics: dict[str, float] = {}
    last_train: dict = {}
    last_val: dict = {}
    for epoch in range(local_rounds):
        log(INFO, f"Starting local epoch {epoch + 1}/{local_rounds}")
        last_train = train_func(model, train_loader, optimizer, device, lesions)
        last_val = validate_func(model, val_loader, device, lesions)
        scheduler.step()

        # Use epoch_{N}_ format for per-epoch metrics (NEW format for handle_client_metrics)
        per_epoch_metrics[f"epoch_{epoch}_train_loss"] = last_train["loss"]
        per_epoch_metrics[f"epoch_{epoch}_val_loss"] = last_val["loss"]
        for name in lesions.get_lesion_list():
            per_epoch_metrics[f"epoch_{epoch}_train_f1-{name}"] = last_train[f"f1-score-{name}"]
            per_epoch_metrics[f"epoch_{epoch}_val_f1-{name}"] = last_val[f"f1-score-{name}"]

    metrics: dict[str, float] = {
        **_flatten_per_lesion(last_train, "train"),
        **_flatten_per_lesion(last_val, "val"),
        "num-examples": len(train_loader.dataset),
        "num-iterations": len(train_loader) * local_rounds,
        **per_epoch_metrics,
    }

    model_record = ArrayRecord(model.state_dict())
    site_config = ConfigRecord({"site": client_name, "local_epochs": local_rounds})
    metric_record = MetricRecord(metrics)
    content = RecordDict({"arrays": model_record, "metrics": metric_record, "config": site_config})
    return Message(content=content, reply_to=msg)


@app.evaluate()
def evaluate(msg: Message, context: Context) -> Message:
    """Evaluate the global model on the local test split (last round only)."""
    config = _load_config()
    val_split = float(config["VAL_SPLIT"])
    test_split = float(config["TEST_SPLIT"])
    batch_size = int(config["BATCH_SIZE"])
    lesions, value_to_numerical, normal_key = _build_lesions(config)

    client_name = os.getenv("SUPERNODE_NAME", "unknown_client")

    flip_utils = _build_flip_utils(context)
    test_datalist = flip_utils.get_image_and_label_list(
        lesions=lesions,
        value_to_numerical=value_to_numerical,
        normal_key=normal_key,
        val_split=val_split,
        test_split=test_split,
        is_test=True,
    )
    test_dataset = Dataset(test_datalist, transform=get_xray_transforms(is_validation=True))
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)

    model, device = _load_model_on_device(msg)

    if len(test_loader.dataset) == 0:
        log(INFO, "No test data found!")
        empty: dict[str, float] = {"test_loss": 0.0, "num-examples": 0}
        for name in lesions.get_lesion_list():
            empty[f"test_f1-{name}"] = 0.0
        site_config = ConfigRecord({"site": client_name})
        return Message(
            content=RecordDict({"metrics": MetricRecord(empty), "config": site_config}),
            reply_to=msg,
        )

    test_metrics = validate_func(model, test_loader, device, lesions)

    # Preserve the "send at round=0" convention via the .round_N suffix
    # so handle_client_metrics forwards one Hub point per metric.
    metrics: dict[str, float] = {
        **_flatten_per_lesion(test_metrics, "test"),
        "num-examples": len(test_loader.dataset),
        "test_loss.round_0": float(test_metrics["loss"]),
    }
    for name in lesions.get_lesion_list():
        metrics[f"test_f1-{name}.round_0"] = float(test_metrics[f"f1-score-{name}"])

    site_config = ConfigRecord({"site": client_name})
    return Message(
        content=RecordDict({"metrics": MetricRecord(metrics), "config": site_config}),
        reply_to=msg,
    )
