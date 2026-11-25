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

"""Platform-owned ClientApp of the fingerprint job type.

The researcher uploads ``cases.py``, which loads this site's cases, and ``config.json``. This
module, which an upload cannot replace, turns the cases into the histograms ``app.fingerprint``
describes and applies the release floors before anything leaves the site. Nothing is trained, and
no local-DP mod is registered: its clip-and-noise is calibrated for weight updates and would turn
the histograms into noise.

What the site sends does not depend on what it receives, so every round sends the same arrays and
one round is enough.
"""

from __future__ import annotations

from logging import INFO

import numpy as np
import torch
from flip.flower.identity import client_identity
from flwr.app import Array, ArrayRecord, ConfigRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.common import log

from app.cases import iter_cases
from app.fingerprint import STATE_KEYS, analyze_case, assert_site_may_release, site_slot, site_statistics
from app.models import get_model, load_config

app = ClientApp()

_CACHE = "fingerprint"


def _site_statistics(context: Context, client_name: str) -> dict[str, np.ndarray]:
    """This site's arrays, computed once per run and kept in ``context.state``."""
    cached = context.state.get(_CACHE)
    if cached is not None:
        return {key: cached[key].numpy() for key in STATE_KEYS}

    config = load_config()
    cases = [analyze_case(image, foreground, spacing) for image, foreground, spacing in iter_cases(context)]
    assert_site_may_release(len(cases), config, client_name)
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


@app.train()
def train(msg: Message, context: Context) -> Message:
    """Return this site's histograms, weighted by its case count."""
    # Loaded through the model so a config.json that differs from the server's fails here (strict keys and shapes).
    get_model().load_state_dict(msg.content["arrays"].to_torch_state_dict())
    client_name = client_identity(context)
    return _reply(msg, _site_statistics(context, client_name), client_name, arrays=True)


@app.evaluate()
def evaluate(msg: Message, context: Context) -> Message:
    """Report the site's case count; there is nothing to evaluate."""
    client_name = client_identity(context)
    return _reply(msg, _site_statistics(context, client_name), client_name, arrays=False)
