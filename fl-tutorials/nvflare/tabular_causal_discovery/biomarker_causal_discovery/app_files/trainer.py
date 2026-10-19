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

"""NVFLARE Client-API "trainer" for the biomarker causal-discovery tutorial.

Nothing is trained. Each global round the site receives the federated state (the arrays
``federated_fci`` describes), works out what it should become — its sufficient statistics,
its vote on the skeleton, or the oriented PAGs — and sends the difference back with the
weight the round's aggregation needs. The whole cohort arrives through ``flip.get_dataframe``;
no imaging is fetched.
"""

import argparse
import json
import logging
import os
from pathlib import Path

import numpy as np
import nvflare.client as flare
import pandas as pd
from federated_fci import (
    STATE_KEYS,
    client_round,
    decode,
    encode,
    evaluate_state,
    partition_for_site,
    prepare_matrix,
)
from flip import FLIP
from flip.constants import FlipConstants
from nvflare.client.tracking import SummaryWriter

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project_id", type=str, default="")
    return parser.parse_args()


def load_query() -> str:
    """Read the cohort query from the client app config (see the EHR tutorial for why)."""
    client_cfg = Path(__file__).parent.parent / "config" / "config_fed_client.json"
    if client_cfg.exists():
        try:
            return json.loads(client_cfg.read_text()).get("query", "")
        except Exception:
            return ""
    return ""


def load_config() -> dict:
    with open(Path(__file__).parent.resolve() / "config.json") as fh:
        return json.load(fh)


def resolve_dataframe(site_name: str, project_id: str, query: str) -> pd.DataFrame:
    """This site's cohort: the trust's data-access-api when deployed, a local CSV under the simulator.

    Simulator precedence mirrors the EHR tutorial: per-site ``SITE{N}_DATAFRAME`` first, else
    the shared ``DEV_DATAFRAME`` sliced by ``person_id`` modulo ``DEV_NUM_PARTITIONS``.
    """
    if not FlipConstants.LOCAL_DEV:
        return FLIP().get_dataframe(project_id, query)

    site_env = site_name.replace("-", "").upper() if site_name else ""
    per_site_path = os.environ.get(f"{site_env}_DATAFRAME") if site_env else None
    path = per_site_path or os.environ.get("DEV_DATAFRAME")
    if not path or not Path(path).exists():
        raise RuntimeError(
            f"No local dataframe for site {site_name!r}: set SITE{{N}}_DATAFRAME or DEV_DATAFRAME "
            f"(got {path!r}) — build the cohort from your ADNI download first: "
            "`make -C fl-tutorials convert-adni-cohort ADNI_DIR=<IDA tables>` (tutorial README)."
        )
    dataframe = pd.read_csv(path)
    if per_site_path:
        return dataframe
    return partition_for_site(dataframe, site_name, int(os.environ.get("DEV_NUM_PARTITIONS", "2")))


def _publish(writer: SummaryWriter, metrics: dict[str, float], step: int) -> None:
    """Stream the round's scalars to the FLIP analytics bridge (``@round`` names the x-axis)."""
    for label, value in metrics.items():
        if not np.isfinite(value):
            value = 0.0
        writer.add_scalar(f"{label.upper()}@round", float(value), global_step=step)


def main() -> None:
    args = parse_args()
    config = load_config()

    flare.init()
    site_name = flare.get_site_name()
    writer = SummaryWriter()

    dataframe = resolve_dataframe(site_name, args.project_id, load_query())
    matrix = prepare_matrix(dataframe, config)
    logger.info(f"Cohort for {site_name}: {len(dataframe)} persons, {matrix.shape[0]} complete rows")

    while flare.is_running():
        input_model = flare.receive()
        if input_model is None:
            break

        if flare.is_train():
            received_arrays = {key: np.asarray(input_model.params[key], dtype=np.float32) for key in STATE_KEYS}
            round_index = int(input_model.current_round or 0)
            total_rounds = int(input_model.total_rounds or config.get("GLOBAL_ROUNDS", 2))
            target, weight, metrics = client_round(
                decode(received_arrays),
                round_index,
                total_rounds,
                matrix,
                site_name,
                config,
                local_dev=bool(FlipConstants.LOCAL_DEV),
            )
            encoded = encode(target)
            # DIFF relative to the received state: the server adds the weighted-mean diff to its
            # global state, so the result is the weighted mean of every site's target.
            diff = {key: encoded[key] - received_arrays[key] for key in STATE_KEYS}
            _publish(writer, metrics, step=round_index + 1)
            logger.info(f"Round {round_index + 1}/{total_rounds}: {metrics}")
            flare.send(
                flare.FLModel(
                    params=diff,
                    params_type="DIFF",
                    metrics={k: float(v) for k, v in metrics.items()},
                    meta={"NUM_STEPS_CURRENT_ROUND": int(weight)},
                )
            )

        elif flare.is_evaluate():
            received = decode({key: np.asarray(input_model.params[key], dtype=np.float32) for key in STATE_KEYS})
            metrics = evaluate_state(received, config)
            for label, value in metrics.items():
                writer.add_scalar(label.upper(), float(value), global_step=0)
            logger.info(f"Final state: {metrics}")
            flare.send(flare.FLModel(metrics={k: float(v) for k, v in metrics.items()}))

        elif flare.is_submit_model():
            params = {key: np.asarray(input_model.params[key], dtype=np.float32) for key in STATE_KEYS}
            flare.send(flare.FLModel(params=params, params_type="FULL"))

        else:
            logger.warning("Received unknown task; ignoring.")


if __name__ == "__main__":
    main()
