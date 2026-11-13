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
"""The biomarker causal-discovery tutorial's Flower ClientApp, driven round by round.

``federated_fci`` decides what a site sends; these tests pin what the Flower transport around it
must do: refuse a run on which the local-DP mod would noise the statistics (reading the run config
exactly as the mod does), fetch the cohort once per run, report its weight as ``num-examples``,
apply the release floor at evaluation too, and fail the job after a round that sent its zeroing
payload. The cohort is invented in the shipped config's column shape; no ADNI value appears here.
A real flwr ``Message`` needs a running SuperLink's identity, so the tests pass a stand-in carrying
the two records the client reads (real ``ArrayRecord`` arrays, through the shipped model).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from biomarker_cohort import shipped_shape_cohort as cohort
from flip.flower import identity
from flwr.app import ConfigRecord, RecordDict
from tutorial_apps import flower_client_app

REPO_ROOT = Path(__file__).resolve().parents[2]
TUTORIAL = REPO_ROOT / "fl-tutorials" / "flower" / "tabular_causal_discovery" / "biomarker_causal_discovery"
CONFIG = json.loads((TUTORIAL / "app" / "config.json").read_text())


@pytest.fixture
def client_app(monkeypatch):
    with flower_client_app(TUTORIAL) as module:
        # A real Message needs a SuperLink run identity; the reply only has to carry its content.
        monkeypatch.setattr(module, "Message", lambda content, reply_to: SimpleNamespace(content=content))
        monkeypatch.setattr(identity, "FlipConstants", SimpleNamespace(LOCAL_DEV=True))
        monkeypatch.setattr(module, "FlipConstants", SimpleNamespace(LOCAL_DEV=True))
        monkeypatch.delenv("SUPERNODE_NAME", raising=False)
        yield module


class Trust:
    """FLIP().get_dataframe for a simulated site, counting the row-level fetches."""

    def __init__(self, frame: pd.DataFrame):
        self.frame, self.fetches = frame, 0

    def __call__(self):
        return SimpleNamespace(get_dataframe=self._get)

    def _get(self, *_):
        self.fetches += 1
        return self.frame.copy()


def context(**run_config) -> SimpleNamespace:
    return SimpleNamespace(
        run_config={"dp-enabled": False, "num-server-rounds": 2, **run_config},
        node_config={"partition-id": "0", "num-partitions": "1"},
        state=RecordDict(),
    )


def message(client_app, state: dict[str, np.ndarray], server_round: int) -> SimpleNamespace:
    return SimpleNamespace(
        content={
            "arrays": client_app._array_record(client_app.encode(state)),
            "config": ConfigRecord({"server-round": server_round}),
        }
    )


def empty_state(client_app) -> dict[str, np.ndarray]:
    from app.federated_fci import empty_state as make_empty

    return make_empty(CONFIG)


def assert_no_digits(text: str) -> None:
    assert not re.search(r"\d", text.replace("site-1", "")), text


def test_a_run_config_without_dp_enabled_is_refused_as_the_mod_reads_it(client_app, monkeypatch):
    """flip_local_dp_mod treats a missing dp-enabled as DP on; the guard must agree, or the mod noises silently."""
    monkeypatch.setattr(client_app, "FLIP", Trust(cohort(120)))
    without_key = context()
    del without_key.run_config["dp-enabled"]
    with pytest.raises(ValueError, match="dp-enabled is true"):
        client_app.train(message(client_app, empty_state(client_app), 1), without_key)
    with pytest.raises(ValueError, match="dp-enabled is true"):
        client_app.train(message(client_app, empty_state(client_app), 1), context(**{"dp-enabled": True}))


def test_a_pooled_run_fetches_once_and_reports_its_weight(client_app, monkeypatch):
    trust = Trust(cohort(120))
    monkeypatch.setattr(client_app, "FLIP", trust)
    ctx = context()
    first = client_app.train(message(client_app, empty_state(client_app), 1), ctx)
    assert first.content["metrics"]["num-examples"] == 120  # the size weight FedAvg pools by
    from app.federated_fci import decode

    pooled = decode({key: tensor.numpy() for key, tensor in first.content["arrays"].to_torch_state_dict().items()})
    assert np.any(pooled["stats_moment"])
    second = client_app.train(message(client_app, pooled, 2), ctx)
    final = decode({key: tensor.numpy() for key, tensor in second.content["arrays"].to_torch_state_dict().items()})
    assert not np.any(final["stats_moment"])  # zeroed in round 2
    assert np.any(final["pag"])
    evaluated = client_app.evaluate(message(client_app, final, 2), ctx)
    assert evaluated.content["metrics"]["num-examples"] == 120
    assert trust.fetches == 1  # one row-level cohort egress per run, not one per round


def test_evaluation_applies_the_release_floor_without_saying_how_many(client_app, monkeypatch):
    """A site that may not release must not report its exact row count as num-examples at evaluation."""
    monkeypatch.setattr(client_app, "FLIP", Trust(cohort(24)))
    with pytest.raises(ValueError, match="below the site floor") as refused:
        client_app.evaluate(message(client_app, empty_state(client_app), 2), context())
    assert_no_digits(str(refused.value))


def test_a_failed_round_two_sends_its_zeroing_payload_then_fails_the_next_task(client_app, monkeypatch):
    monkeypatch.setattr(client_app, "FLIP", Trust(cohort(120)))
    ctx = context()
    first = client_app.train(message(client_app, empty_state(client_app), 1), ctx)
    from app import federated_fci

    pooled = federated_fci.decode(
        {key: tensor.numpy() for key, tensor in first.content["arrays"].to_torch_state_dict().items()}
    )
    monkeypatch.setattr(
        federated_fci, "fci_from_correlation", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fci broke"))
    )
    reply = client_app.train(message(client_app, pooled, 2), ctx)
    sent = federated_fci.decode(
        {key: tensor.numpy() for key, tensor in reply.content["arrays"].to_torch_state_dict().items()}
    )
    assert not np.any(sent["stats_mean"])
    assert not np.any(sent["stats_moment"])
    assert reply.content["metrics"]["round_failed"] == 1.0
    with pytest.raises(RuntimeError, match="fci broke"):
        client_app.evaluate(message(client_app, sent, 2), ctx)


def test_the_floor_counts_persons_at_a_flower_site(client_app, monkeypatch):
    """Three rows for each of 20 persons clears a row floor of 30, never the person floor."""
    one = cohort(20)
    monkeypatch.setattr(client_app, "FLIP", Trust(pd.concat([one, one, one], ignore_index=True)))
    with pytest.raises(ValueError, match="below the site floor"):
        client_app.train(message(client_app, empty_state(client_app), 1), context())


def test_a_deployed_flower_site_refuses_voting_mode(client_app, monkeypatch):
    """The gate reads FlipConstants.LOCAL_DEV: a deployed client has no switch for the unanalysed mode."""
    voting = {**CONFIG, "FEDERATION": "layer_voting", "GLOBAL_ROUNDS": 6}
    monkeypatch.setattr(client_app, "_load_config", lambda: voting)
    monkeypatch.setattr(client_app, "FLIP", Trust(cohort(120)))
    monkeypatch.setattr(client_app, "FlipConstants", SimpleNamespace(LOCAL_DEV=False))
    from app.federated_fci import empty_state as make_empty
    from app.models import CausalDiscoveryState

    monkeypatch.setattr(client_app, "get_model", lambda: CausalDiscoveryState(voting))  # voting-mode array shapes

    with pytest.raises(ValueError, match="runs only on the simulator"):
        client_app.train(message(client_app, make_empty(voting), 1), context(**{"num-server-rounds": 6}))
