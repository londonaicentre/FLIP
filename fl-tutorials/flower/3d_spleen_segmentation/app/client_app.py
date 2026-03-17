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

"""quickstart-monai: A Flower / MONAI training-only app."""

import logging
import os

import torch
from flwr.app import ArrayRecord, ConfigRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from monai.data import DataLoader, Dataset
from monai.losses import DiceLoss

from app.data_loading import FLIP_BASE
from app.models import get_model
from app.task import train_func, validate_func
from app.transforms import get_train_transforms, get_val_transforms

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

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

    # FLIP variables
    model_id = run_config.get("flip-model-id", "monai-flower-tutorial-model")

    # NOTE this needs to match the name of the trust in the central hub database
    client_name = os.getenv("SUPERNODE_NAME", "unknown_client")

    # global_round from server is 1-based - convert to 0-based for easier calculations of round numbers during training
    global_round = int(msg.content["config"]["server-round"]) - 1

    # Configure FLIP
    flip_utils = FLIP_BASE()
    flip_utils.project_id = run_config.get("flip-project-id", "monai-flower-tutorial")
    flip_utils.query = run_config.get("flip-cohort-query", "*")
    logger.info("Fetching FLIP dataframe using project_id=%s and query=%s", flip_utils.project_id, flip_utils.query)
    flip_utils.dataframe = flip_utils.flip.get_dataframe(project_id=flip_utils.project_id, query=flip_utils.query)
    logger.info(f"FLIP dataframe has {len(flip_utils.dataframe)} rows.")

    # Setup device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("Training on device: %s", device)

    # Get data
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
    model.load_state_dict(state_dict=state_dict, strict=False)
    model.to(device)

    # Initialize optimizer and loss function
    loss_fn = DiceLoss(to_onehot_y=True, softmax=True)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    # Perform training
    losses: dict[str, list[float]] = {"train": [], "val": []}
    dice: dict[str, list[float]] = {"val": []}
    for epoch in range(local_epochs):
        logger.info(f"Starting epoch {epoch + 1}/{local_epochs}")
        train_loss = train_func(
            model=model,
            train_loader=train_loader,
            optimizer=optimizer,
            loss_fn=loss_fn,
            device=device,
        )
        round = global_round * (local_epochs) + epoch + 1
        flip_utils.flip.send_metrics(client_name, model_id, label="TRAIN_LOSS", value=train_loss, round=round)

        val_dice, val_loss = validate_func(
            model=model,
            val_loader=val_loader,
            device=device,
            loss_fn=loss_fn,
        )
        flip_utils.flip.send_metrics(client_name, model_id, label="VAL_LOSS", value=val_loss, round=round)
        flip_utils.flip.send_metrics(client_name, model_id, label="VAL_DICE", value=val_dice, round=round)

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
    batch_size = int(run_config.get("batch-size", 2))

    # FLIP variables
    model_id = run_config.get("flip-model-id", "monai-flower-tutorial-model")
    client_name = os.getenv("SUPERNODE_NAME", "unknown_client")

    # Setup device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("Evaluating on device: %s", device)

    # Get FLIP config
    flip_utils = FLIP_BASE()
    flip_utils.project_id = run_config.get("flip-project-id", "monai-flower-tutorial")
    flip_utils.query = run_config.get("flip-cohort-query", "*")
    logger.info("Fetching FLIP dataframe using project_id=%s and query=%s", flip_utils.project_id, flip_utils.query)
    flip_utils.dataframe = flip_utils.flip.get_dataframe(project_id=flip_utils.project_id, query=flip_utils.query)
    logger.info(f"FLIP dataframe has {len(flip_utils.dataframe)} rows.")

    # Get test data
    test_datalist = flip_utils.get_image_and_label_list(_val_split=val_split, _test_split=test_split, is_test=True)
    test_dataset = Dataset(test_datalist, transform=get_val_transforms())
    test_loader = DataLoader(test_dataset, batch_size=1, shuffle=False)

    # Initialize model and load received weights
    model = get_model()
    state_dict = msg.content["arrays"].to_torch_state_dict()
    model.load_state_dict(state_dict=state_dict, strict=False)
    model.to(device)

    # Initialize loss function
    loss_fn = DiceLoss(to_onehot_y=True, softmax=True)

    # Perform evaluation
    if len(test_loader.dataset) == 0:
        logger.info("No test data found!")
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

    logger.info(
        "Evaluation completed for client %s. Test Loss: %.4f, Test Dice: %.4f",
        client_name,
        test_loss,
        test_dice,
    )

    # Optionally send metrics to FLIP
    flip_utils.flip.send_metrics(client_name, model_id, label="TEST_LOSS", value=test_loss, round=0)
    flip_utils.flip.send_metrics(client_name, model_id, label="TEST_DICE", value=test_dice, round=0)

    metrics = {
        "test_loss": test_loss,
        "test_dice": test_dice,
        "num-examples": len(test_loader.dataset),
    }

    site_config = ConfigRecord({"site": client_name})
    metric_record = MetricRecord(metrics)
    content = RecordDict({"metrics": metric_record, "config": site_config})
    return Message(content=content, reply_to=msg)
