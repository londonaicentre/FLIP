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

"""biomarker-causal-discovery: Flower ClientApp for federated bootstrapped FCI.

Nothing is trained. Each global round the site receives the federated state (the arrays
``app.federated_fci`` describes), works out what it should become — its sufficient statistics,
its vote on the skeleton, or the oriented PAGs — and returns it with the weight the round's
aggregation needs. The template ``server_app.py`` / ``strategy.py`` (FedAvg) are untouched.

Hyperparameters come from ``app/config.json`` rather than ``run_config`` — the base bundle
ships one ``pyproject.toml`` for every tutorial, so config.json is the only per-tutorial knob
that rides through the upload flow.
"""

import json
from logging import INFO
from pathlib import Path

import numpy as np
import torch
from flip import FLIP
from flip.flower.identity import client_identity, partition_cohort
from flip.flower.privacy import flip_local_dp_mod
from flwr.app import ArrayRecord, ConfigRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.common import log

from app.federated_fci import STATE_KEYS, client_round, decode, encode, evaluate_state, prepare_matrix
from app.models import get_model

app = ClientApp()


def _load_config() -> dict:
    with open(Path(__file__).parent / "config.json") as fh:
        return json.load(fh)


def _fetch_matrix(context: Context, config: dict) -> np.ndarray:
    """This site's analysis matrix via FLIP.

    Deployed: the trust's data-access-api serves its own cohort. Under ``LOCAL_DEV`` every
    site reads the same CSV, so ``partition_cohort`` slices out this site's ``person_id``
    partition (a no-op when deployed).
    """
    project_id = context.run_config.get("flip-project-id", "causal-flower-tutorial")
    query = context.run_config.get("flip-cohort-query", "*")
    log(INFO, f"Fetching FLIP dataframe project_id={project_id}")
    dataframe = partition_cohort(FLIP().get_dataframe(project_id, query), context)
    matrix = prepare_matrix(dataframe, config)
    log(INFO, f"Cohort for {client_identity(context)}: {len(dataframe)} persons, {matrix.shape[0]} complete rows")
    return matrix


def _received_arrays(msg: Message) -> dict[str, np.ndarray]:
    """The global arrays, loaded through the model so a wire-format drift fails loudly (strict keys)."""
    model = get_model()
    model.load_state_dict(msg.content["arrays"].to_torch_state_dict())
    return {key: tensor.detach().cpu().numpy().astype(np.float32) for key, tensor in model.state_dict().items()}


def _array_record(encoded: dict[str, np.ndarray]) -> ArrayRecord:
    model = get_model()
    model.load_state_dict({key: torch.from_numpy(np.ascontiguousarray(encoded[key])) for key in STATE_KEYS})
    return ArrayRecord(model.state_dict())


# The local-DP mod stays registered as in every FLIP app, but config.toml sets dp-enabled = false
# for this tutorial: clipping and noising a correlation matrix (or a 0/1 vote) is not a
# meaningful privacy mechanism for it. See the README's privacy section.
@app.train(mods=[flip_local_dp_mod])
def train(msg: Message, context: Context) -> Message:
    """One federated round: statistics, a skeleton vote, or the oriented PAGs (see federated_fci)."""
    config = _load_config()
    client_name = client_identity(context)
    round_index = int(msg.content["config"]["server-round"]) - 1
    total_rounds = int(context.run_config.get("num-server-rounds", config.get("GLOBAL_ROUNDS", 2)))

    matrix = _fetch_matrix(context, config)
    received = decode(_received_arrays(msg))
    target, weight, metrics = client_round(received, round_index, total_rounds, matrix, client_name, config)
    log(INFO, f"Round {round_index + 1}/{total_rounds} at {client_name}: {metrics}")

    # "@round" names the x-axis and ".x_<N>" the coordinate — the "<label>[@<x_label>][.x_<V>]"
    # key grammar of flip.flower.metrics (FLIP#148).
    per_round = {f"{label}@round.x_{round_index + 1}": float(value) for label, value in metrics.items()}
    metric_record = MetricRecord(
        {**{k: float(v) for k, v in metrics.items()}, **per_round, "num-examples": int(weight)}
    )
    content = RecordDict(
        {
            "arrays": _array_record(encode(target)),
            "metrics": metric_record,
            "config": ConfigRecord({"site": client_name}),
        }
    )
    return Message(content=content, reply_to=msg)


@app.evaluate()
def evaluate(msg: Message, context: Context) -> Message:
    """Report the final state's edge counts (the ".x_0" suffix plots a single point by convention)."""
    config = _load_config()
    client_name = client_identity(context)
    matrix = _fetch_matrix(context, config)
    metrics = evaluate_state(decode(_received_arrays(msg)), config)
    log(INFO, f"Final state at {client_name}: {metrics}")
    record = {**{k: float(v) for k, v in metrics.items()}, **{f"{k}.x_0": float(v) for k, v in metrics.items()}}
    record["num-examples"] = int(matrix.shape[0])
    return Message(
        content=RecordDict({"metrics": MetricRecord(record), "config": ConfigRecord({"site": client_name})}),
        reply_to=msg,
    )
