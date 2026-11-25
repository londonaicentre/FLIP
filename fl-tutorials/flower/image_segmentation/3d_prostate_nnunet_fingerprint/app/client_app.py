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

"""3d-prostate-nnunet-fingerprint: Flower ClientApp for the federated nnU-Net fingerprint.

Nothing is trained. The site loads each study exactly as the segmentation app does, reduces its
cohort to the histograms ``app.fingerprint`` describes, and returns them weighted by its case
count; the template ``server_app.py`` / ``strategy.py`` (FedAvg) pool them unchanged. What the site
sends does not depend on what it receives, so every round sends the same arrays and one round is
enough.
"""

from __future__ import annotations

from logging import INFO

import nibabel as nib
import numpy as np
import torch
from flip.flower.identity import client_identity, partition_cohort
from flip.flower.privacy import LocalDpConfig, flip_local_dp_mod
from flwr.app import Array, ArrayRecord, ConfigRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.common import log

from app.data_loading import FLIP_BASE
from app.dataset import IMAGE_KEY, PZ_TZ_KEY, WHOLE_GLAND_KEY, build_loader, load_case
from app.fingerprint import STATE_KEYS, analyze_case, assert_site_may_release, site_slot, site_statistics
from app.models import get_model, load_config

app = ClientApp()

_CACHE = "fingerprint"


def _assert_local_dp_off(context: Context) -> None:
    """The histograms are not weights: the local-DP mod would clip and noise them into nothing, silently.

    ``config.toml`` sets ``dp-enabled = false``, but the upload path does not require that file, and
    a run config without the key is DP on. Read it exactly as the mod does and refuse.
    """
    if LocalDpConfig.from_run_config(context.run_config).enabled:
        raise ValueError(
            "dp-enabled is true for this run: the per-update clip-and-noise mod is not a mechanism for histograms "
            "(see the README's privacy section) — upload app/config.toml, which sets dp-enabled = false"
        )


def _site_statistics(context: Context, client_name: str) -> dict[str, np.ndarray]:
    """This site's arrays, computed once per run and kept in ``context.state``."""
    cached = context.state.get(_CACHE)
    if cached is not None:
        return {key: cached[key].numpy() for key in STATE_KEYS}

    config = load_config()
    flip_utils = FLIP_BASE()
    flip_utils.project_id = str(context.run_config.get("flip-project-id", "prostate-fingerprint-tutorial"))
    flip_utils.query = str(context.run_config.get("flip-cohort-query", "*"))
    flip_utils.fetch_dataframe()
    # Slice the shared dev cohort so the simulated sites really differ; a no-op off LOCAL_DEV.
    flip_utils.dataframe = partition_cohort(flip_utils.dataframe, context)
    # No validation or test split: the fingerprint describes every case the site would train on.
    datalist, _ = flip_utils.get_case_list(config["MODALITY"], val_split=0.0, test_split=0.0)
    assert_site_may_release(len(datalist), config, client_name)

    loader = build_loader()
    cases = []
    for item in datalist:
        image, mask = load_case({key: item[key] for key in (IMAGE_KEY, WHOLE_GLAND_KEY, PZ_TZ_KEY)}, loader)
        # (C, h, w, d) -> nnU-Net's (C, d, h, w), spacing to match. Foreground is the whole gland.
        height, width, depth = nib.affines.voxel_sizes(np.asarray(image.affine, dtype=np.float64))
        cases.append(
            analyze_case(
                image.numpy().transpose(0, 3, 1, 2),
                mask[0].numpy().transpose(2, 0, 1) > 0,
                (depth, height, width),
            )
        )
    statistics = site_statistics(cases, config, client_name)
    context.state[_CACHE] = ArrayRecord({key: Array(value) for key, value in statistics.items()})
    log(INFO, "Fingerprint for %s: %d cases", client_name, len(cases))
    return statistics


def _reply(msg: Message, statistics: dict[str, np.ndarray], client_name: str, arrays: bool) -> Message:
    num_cases = int(statistics["site_cases"][site_slot(client_name)])
    content = {
        "metrics": MetricRecord({"cases": float(num_cases), "num-examples": num_cases}),
        "config": ConfigRecord({"site": client_name}),
    }
    if arrays:
        model = get_model()
        model.load_state_dict({key: torch.from_numpy(statistics[key]) for key in STATE_KEYS})
        content["arrays"] = ArrayRecord(model.state_dict())
    return Message(content=RecordDict(content), reply_to=msg)


# The local-DP mod stays registered as in every FLIP app; config.toml sets dp-enabled = false and
# _assert_local_dp_off refuses a run that turns it back on.
@app.train(mods=[flip_local_dp_mod])
def train(msg: Message, context: Context) -> Message:
    """Return this site's histograms, weighted by its case count."""
    _assert_local_dp_off(context)
    # Loaded through the model so a config.json that differs from the server's fails here (strict keys and shapes).
    get_model().load_state_dict(msg.content["arrays"].to_torch_state_dict())
    client_name = client_identity(context)
    return _reply(msg, _site_statistics(context, client_name), client_name, arrays=True)


@app.evaluate()
def evaluate(msg: Message, context: Context) -> Message:
    """Report the site's case count; there is nothing to evaluate."""
    client_name = client_identity(context)
    return _reply(msg, _site_statistics(context, client_name), client_name, arrays=False)
