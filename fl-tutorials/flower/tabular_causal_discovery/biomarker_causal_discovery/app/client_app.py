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
from flip.constants import FlipConstants
from flip.flower.identity import client_identity, partition_cohort
from flip.flower.privacy import flip_local_dp_mod
from flwr.app import Array, ArrayRecord, ConfigRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.common import log

from app.federated_fci import (
    STATE_KEYS,
    RoundFailed,
    assert_site_may_release,
    client_round,
    decode,
    encode,
    evaluate_state,
    prepare_cohort,
)
from app.models import get_model

app = ClientApp()


def _load_config() -> dict:
    with open(Path(__file__).parent / "config.json") as fh:
        return json.load(fh)


_COHORT_CACHE = "cohort"
_PENDING_ERROR = "pending_error"


def _fetch_cohort(context: Context, config: dict) -> tuple[np.ndarray, np.ndarray | None, int]:
    """This site's analysis matrix via FLIP, fetched once per run and kept in ``context.state``.

    Deployed: the trust's data-access-api serves its own cohort. Under ``LOCAL_DEV`` every
    site reads the same CSV, so ``partition_cohort`` slices out this site's ``person_id``
    partition (a no-op when deployed). Every round and the final evaluation call this; the cache
    means one row-level cohort egress per run at the trust, not one per round. Returns what
    ``federated_fci.prepare_cohort`` does: the matrix, the kept rows' ``person_id`` (or None)
    and the number of rows dropped as incomplete.
    """
    cached = context.state.get(_COHORT_CACHE)
    if cached is not None:
        persons = cached["person_ids"].numpy() if "person_ids" in cached else None
        return cached["matrix"].numpy().astype(np.float64), persons, int(cached["dropped"].numpy()[0])
    project_id = context.run_config.get("flip-project-id", "causal-flower-tutorial")
    query = context.run_config.get("flip-cohort-query", "*")
    log(INFO, f"Fetching FLIP dataframe project_id={project_id}")
    dataframe = partition_cohort(FLIP().get_dataframe(project_id, query), context)
    matrix, person_ids, dropped = prepare_cohort(dataframe, config)
    log(
        INFO,
        f"Cohort for {client_identity(context)}: {len(dataframe)} rows, {matrix.shape[0]} complete, {dropped} dropped",
    )
    record = {"matrix": Array(np.ascontiguousarray(matrix)), "dropped": Array(np.asarray([dropped], dtype=np.int64))}
    if person_ids is not None:
        record["person_ids"] = Array(np.ascontiguousarray(np.asarray(person_ids, dtype=np.int64)))
    context.state[_COHORT_CACHE] = ArrayRecord(record)
    return matrix, person_ids, dropped


def _raise_pending_error(context: Context) -> None:
    """A round that failed after the release sent its zeroing payload first; the job fails on the next task."""
    pending = context.state.get(_PENDING_ERROR)
    if pending is not None:
        raise RuntimeError(str(pending["message"]))


def _assert_local_dp_off(context: Context) -> None:
    """The statistics are not weights: the local-DP mod would clip and noise them into nothing, silently.

    ``config.toml`` sets ``dp-enabled = false``, but the upload path does not require that file,
    so an upload without it runs the base bundle's ``dp-enabled = true``. Refuse rather than
    report a graph computed from noise.
    """
    if bool(context.run_config.get("dp-enabled", False)):
        raise ValueError(
            "dp-enabled is true for this run: the per-update clip-and-noise mod is not a mechanism for pooled "
            "statistics (see the README's privacy section) — upload app/config.toml, which sets dp-enabled = false"
        )


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
    _raise_pending_error(context)
    _assert_local_dp_off(context)
    config = _load_config()
    client_name = client_identity(context)
    round_index = int(msg.content["config"]["server-round"]) - 1
    total_rounds = int(context.run_config.get("num-server-rounds", config.get("GLOBAL_ROUNDS", 2)))

    matrix, person_ids, dropped_rows = _fetch_cohort(context, config)
    received = decode(_received_arrays(msg))
    try:
        target, weight, metrics = client_round(
            received,
            round_index,
            total_rounds,
            matrix,
            client_name,
            config,
            local_dev=bool(FlipConstants.LOCAL_DEV),
            person_ids=person_ids,
            dropped_rows=dropped_rows,
        )
    except RoundFailed as failed:
        target, weight, metrics = failed.target, failed.weight, failed.metrics
        context.state[_PENDING_ERROR] = ConfigRecord({"message": str(failed.cause)})
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
    _raise_pending_error(context)
    config = _load_config()
    client_name = client_identity(context)
    matrix, person_ids, dropped_rows = _fetch_cohort(context, config)
    # A site that may not release reports nothing here either: num-examples below is its exact row count.
    assert_site_may_release(matrix, config, person_ids, dropped_rows, client_name)
    metrics = evaluate_state(decode(_received_arrays(msg)), config)
    log(INFO, f"Final state at {client_name}: {metrics}")
    record = {**{k: float(v) for k, v in metrics.items()}, **{f"{k}.x_0": float(v) for k, v in metrics.items()}}
    record["num-examples"] = int(matrix.shape[0])
    return Message(
        content=RecordDict({"metrics": MetricRecord(record), "config": ConfigRecord({"site": client_name})}),
        reply_to=msg,
    )
