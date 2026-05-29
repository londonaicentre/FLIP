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

"""quickstart-monai: A Flower / MONAI evaluation-only app."""

import os
from logging import INFO

import torch
from flwr.app import ArrayRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.common import log
from flwr.common.record import ConfigRecord
from monai.data import DataLoader, Dataset

from app.data_loading import FLIP_BASE
from app.models import get_model
from app.task import evaluate_func
from app.transforms import get_val_transforms

# Flower ClientApp
app = ClientApp()


@app.evaluate()
def evaluate(msg: Message, context: Context) -> Message:
    """Evaluate the model on local test data."""
    # Configure evaluation parameters
    run_config = context.run_config

    # NOTE this needs to match the name of the trust in the central hub database
    client_name = os.getenv("SUPERNODE_NAME", "unknown_client")

    # Configure FLIP
    flip_utils = FLIP_BASE()
    flip_utils.project_id = run_config.get("flip-project-id", "monai-flower-tutorial")
    flip_utils.query = run_config.get("flip-cohort-query", "*")
    log(INFO, "Fetching FLIP dataframe using project_id=%s and query=%s", flip_utils.project_id, flip_utils.query)
    flip_utils.dataframe = flip_utils.flip.get_dataframe(project_id=flip_utils.project_id, query=flip_utils.query)
    log(INFO, f"FLIP dataframe has {len(flip_utils.dataframe)} rows.")

    # Setup device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log(INFO, "Evaluating on device: %s", device)

    # Evaluation-only: score every matched image/label pair in this client's
    # cohort, not a held-out fraction.
    test_datalist = flip_utils.get_test_data_list()
    dataset_test = Dataset(test_datalist, transform=get_val_transforms())
    test_loader = DataLoader(dataset_test, batch_size=1, shuffle=False)

    # Load the model weights distributed by the ServerApp.
    arrays: ArrayRecord = msg.content["arrays"]
    model = get_model()
    # strict=True (default) — server and client both build get_model() from the
    # same factory, so any missing/unexpected key means the wire format has
    # drifted from the architecture, not a legitimate partial load. Better to
    # fail the eval task than score with random weights in mismatched layers.
    model.load_state_dict(arrays.to_torch_state_dict())
    model.to(device)

    # evaluate_func sweeps the whole test_loader once and returns the mean of
    # each segmentation metric across subjects; with deterministic eval
    # transforms and model.eval(), repeating the sweep produces identical numbers.
    metrics = evaluate_func(
        model=model,
        test_loader=test_loader,
        device=device,
    )
    log(INFO, "Evaluation metrics: %s", metrics)

    # Return the metrics in a flat MetricRecord. The server's FedAvg strategy
    # weights by "num-examples" and averages every other key across clients, so
    # whatever metrics evaluate_func returns flow through unchanged.
    metric_values = {
        "num-examples": int(len(test_loader.dataset)),
        **{name: float(value) for name, value in metrics.items()},
    }

    # Construct and return the reply Message
    # Send client_name in ConfigRecord (MetricRecord only accepts numeric types)
    metric_record = MetricRecord(metric_values)
    config_record = ConfigRecord({"client_name": client_name})
    content = RecordDict({"metrics": metric_record, "config": config_record})
    return Message(content=content, reply_to=msg)
