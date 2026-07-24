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

"""quickstart-monai: A Flower / MONAI training-only app.

Metrics flow through the reply Message's MetricRecord. The fl-server forwards
them to the Central Hub on this client's behalf — clients must not hold the
hub credential. Per-epoch granularity is preserved via the
``<label>[@<x_label>][.x_<V>]`` key convention understood by
``flip.flower.metrics.handle_client_metrics`` (FLIP#148).
"""

import os
from logging import INFO

import torch
from flwr.app import ArrayRecord, ConfigRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.common import log
from monai.data import DataLoader, Dataset
from monai.losses import DiceCELoss

from app.data_loading import FLIP_BASE
from app.models import get_model
from app.task import train_func, validate_func
from app.transforms import get_train_transforms, get_val_transforms

# Flower ClientApp
app = ClientApp()


@app.train()
def train(msg: Message, context: Context) -> Message:
    """Train the model on local data (no evaluation)."""
    # Configure training parameters
    run_config = context.run_config
    local_epochs = int(run_config.get("local-epochs", 1))
    learning_rate = float(run_config.get("learning-rate", 1e-4))
    val_split = float(run_config.get("val-split", 0.2))
    test_split = float(run_config.get("test-split", 0.2))
    # test_split = float(run_config.get("test-split", 0.2))
    batch_size = int(run_config.get("batch-size", 2))

    # NOTE this needs to match the name of the trust in the central hub database
    client_name = os.getenv("SUPERNODE_NAME", "unknown_client")

    # global_round from server is 1-based - convert to 0-based for easier calculations of round numbers during training
    global_round = int(msg.content["config"]["server-round"]) - 1

    # Configure FLIP
    flip_utils = FLIP_BASE()
    flip_utils.project_id = run_config.get("flip-project-id", "monai-flower-tutorial")
    flip_utils.query = run_config.get("flip-cohort-query", "*")
    log(INFO, "Fetching FLIP dataframe using project_id=%s and query=%s", flip_utils.project_id, flip_utils.query)
    flip_utils.dataframe = flip_utils.flip.get_dataframe(project_id=flip_utils.project_id, query=flip_utils.query)
    log(INFO, f"FLIP dataframe has {len(flip_utils.dataframe)} rows.")

    # Setup device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log(INFO, "Training on device: %s", device)

    # Get data
    if val_split + test_split >= 1.0:
        log(INFO, "Invalid split configuration: val_split + test_split must be < 1.0")
        # fl-server sees the raised error and forwards it to the Central Hub
        # via handle_client_exception; it is also responsible for transitioning
        # the model status to ERROR.
        raise ValueError("Invalid split configuration: val_split + test_split must be < 1.0")

    train_datalist, val_datalist = flip_utils.get_image_and_label_list(
        _val_split=val_split, _test_split=test_split, is_test=False
    )
    dataset_train = Dataset(train_datalist, transform=get_train_transforms())
    dataset_val = Dataset(val_datalist, transform=get_val_transforms())
    train_loader = DataLoader(dataset_train, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(dataset_val, batch_size=1, shuffle=False)

    # Initialize model and load received weights
    model = get_model()
    state_dict = msg.content["arrays"].to_torch_state_dict()
    # strict=True (default) — server and client build get_model() from the same
    # factory, so any missing/unexpected key means the wire format has drifted
    # from the architecture; better to fail than train with random weights in
    # mismatched layers.
    model.load_state_dict(state_dict=state_dict)
    model.to(device)

    # Initialize optimizer and loss function
    # DiceCELoss recipe mirrors the reference trainer (line 205 of
    # its trainer.py). The 0.2*CE component pushes argmax predictions off the
    # all-background baseline that pure DiceLoss gets stuck on with this dataset
    # (spleen is <1% of voxels); batch=True aggregates the loss across the whole
    # batch so a single sample with few foreground voxels doesn't dominate.
    loss_fn = DiceCELoss(
        to_onehot_y=True,
        softmax=True,
        squared_pred=False,
        batch=True,
        lambda_ce=0.2,
        lambda_dice=0.8,
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    # Perform training
    per_epoch_metrics: dict[str, float] = {}
    losses: dict[str, list[float]] = {"train": [], "val": []}
    dice: dict[str, list[float]] = {"val": []}
    for epoch in range(local_epochs):
        log(INFO, f"Starting epoch {epoch + 1}/{local_epochs}")
        train_loss = train_func(
            model=model,
            train_loader=train_loader,
            optimizer=optimizer,
            loss_fn=loss_fn,
            device=device,
        )
        cumulative_epoch = global_round * (local_epochs) + epoch + 1

        val_dice, val_loss = validate_func(
            model=model,
            val_loader=val_loader,
            device=device,
            loss_fn=loss_fn,
        )

        # Per-epoch points: "@epoch" names the x-axis and ".x_<N>" is the coordinate (the cumulative
        # epoch count), so the fl-server forwards one Hub point per epoch — see the
        # "<label>[@<x_label>][.x_<V>]" key grammar in flip.flower.metrics (FLIP#148).
        per_epoch_metrics[f"train_loss@epoch.x_{cumulative_epoch}"] = float(train_loss)
        per_epoch_metrics[f"val_loss@epoch.x_{cumulative_epoch}"] = float(val_loss)
        per_epoch_metrics[f"val_dice@epoch.x_{cumulative_epoch}"] = float(val_dice)

        losses["train"].append(train_loss)
        losses["val"].append(val_loss)
        dice["val"].append(val_dice)

    # Get average metrics across all epochs (handle empty lists)
    avg_train_loss = sum(losses["train"]) / len(losses["train"]) if losses["train"] else -1
    avg_val_loss = sum(losses["val"]) / len(losses["val"]) if losses["val"] else -1
    avg_val_dice = sum(dice["val"]) / len(dice["val"]) if dice["val"] else -1

    # Construct and return the reply Message
    model_record = ArrayRecord(model.state_dict())

    site_config = ConfigRecord({"site": client_name})
    metrics = {
        "train_loss": avg_train_loss,
        "val_loss": avg_val_loss,
        "val_dice": avg_val_dice,
        "num-examples": len(train_loader.dataset),
        "num-iterations": len(train_loader) * local_epochs,
        **per_epoch_metrics,
    }
    metric_record = MetricRecord(metrics)
    content = RecordDict({"arrays": model_record, "metrics": metric_record, "config": site_config})
    return Message(content=content, reply_to=msg)


@app.evaluate()
def evaluate(msg: Message, context: Context) -> Message:
    """Evaluate the model on test data.

    This function:
    1. Loads the model with received weights
    2. Loads test data with appropriate transforms
    3. Runs evaluation on test set
    4. Computes Dice metric and loss
    5. Returns evaluation metrics
    """
    # Configure evaluation parameters
    run_config = context.run_config
    val_split = float(run_config.get("val-split", 0.2))
    test_split = float(run_config.get("test-split", 0.2))

    # NOTE this needs to match the name of the trust in the central hub database
    client_name = os.getenv("SUPERNODE_NAME", "unknown_client")

    # Setup device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log(INFO, "Evaluating on device: %s", device)

    # Get FLIP config
    flip_utils = FLIP_BASE()
    flip_utils.project_id = run_config.get("flip-project-id", "monai-flower-tutorial")
    flip_utils.query = run_config.get("flip-cohort-query", "*")
    log(INFO, "Fetching FLIP dataframe using project_id=%s and query=%s", flip_utils.project_id, flip_utils.query)
    flip_utils.dataframe = flip_utils.flip.get_dataframe(project_id=flip_utils.project_id, query=flip_utils.query)
    log(INFO, f"FLIP dataframe has {len(flip_utils.dataframe)} rows.")

    # Get test data
    test_datalist = flip_utils.get_image_and_label_list(_val_split=val_split, _test_split=test_split, is_test=True)
    test_dataset = Dataset(test_datalist, transform=get_val_transforms())
    test_loader = DataLoader(test_dataset, batch_size=1, shuffle=False)

    # Initialize model and load received weights
    model = get_model()
    state_dict = msg.content["arrays"].to_torch_state_dict()
    # strict=True (default) — see note in the train handler above; same wire
    # contract applies to the test path.
    model.load_state_dict(state_dict=state_dict)
    model.to(device)

    # Initialize loss function
    # DiceCELoss recipe mirrors the reference trainer (line 205 of
    # its trainer.py). The 0.2*CE component pushes argmax predictions off the
    # all-background baseline that pure DiceLoss gets stuck on with this dataset
    # (spleen is <1% of voxels); batch=True aggregates the loss across the whole
    # batch so a single sample with few foreground voxels doesn't dominate.
    loss_fn = DiceCELoss(
        to_onehot_y=True,
        softmax=True,
        squared_pred=False,
        batch=True,
        lambda_ce=0.2,
        lambda_dice=0.8,
    )

    # Perform evaluation
    if len(test_loader.dataset) == 0:
        log(INFO, "No test data found!")
        metrics = {"test_loss": 0.0, "test_dice": 0.0, "num-examples": 0}
        metric_record = MetricRecord(metrics)
        content = RecordDict({"metrics": metric_record})
        return Message(content=content, reply_to=msg)

    test_dice, test_loss = validate_func(
        model=model,
        val_loader=test_loader,
        device=device,
        loss_fn=loss_fn,
    )

    log(
        INFO,
        "Evaluation completed for client %s. Test Loss: %.4f, Test Dice: %.4f",
        client_name,
        test_loss,
        test_dice,
    )

    # Test metrics are a single point, plotted at x=0 by convention using the
    # label.x_0 suffix understood by handle_client_metrics server-side.
    metrics = {
        "test_loss": test_loss,
        "test_dice": test_dice,
        "test_loss.x_0": float(test_loss),
        "test_dice.x_0": float(test_dice),
        "num-examples": len(test_loader.dataset),
    }

    site_config = ConfigRecord({"site": client_name})
    metric_record = MetricRecord(metrics)
    content = RecordDict({"metrics": metric_record, "config": site_config})
    return Message(content=content, reply_to=msg)
