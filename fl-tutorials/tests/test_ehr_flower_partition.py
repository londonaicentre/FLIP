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
# The EHR Flower client used to read SUPERNODE_NAME directly. The simulator sets none, so every
# simulated site resolved to "unknown_client" and partition_for_client handed each of them the whole
# CSV: the run looked federated and was not (FLIP#1444). The client now takes its identity from the
# context and slices with flip.flower.identity.partition_cohort; this pins that two simulated sites
# get disjoint person_id partitions that together cover the cohort.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from flip.flower import identity
from tutorial_apps import flower_client_app, simulated_site_context

REPO_ROOT = Path(__file__).resolve().parents[2]
EHR_TUTORIAL = REPO_ROOT / "fl-tutorials" / "flower" / "ehr_risk_prediction"


@pytest.fixture
def ehr_client_app():
    """The EHR tutorial's client_app, imported under the ``app`` package name it ships as."""
    with flower_client_app(EHR_TUTORIAL) as client_app:
        yield client_app


@pytest.fixture
def cohort() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    n = 40
    return pd.DataFrame(
        {
            "person_id": np.arange(n),
            "age": rng.integers(30, 90, n),
            "is_female": rng.integers(0, 2, n),
            "label": rng.integers(0, 2, n),
        }
    )


_context = simulated_site_context


def test_two_simulated_sites_get_disjoint_partitions_that_cover_the_cohort(ehr_client_app, cohort, monkeypatch):
    monkeypatch.setattr(identity, "FlipConstants", SimpleNamespace(LOCAL_DEV=True))
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    monkeypatch.setattr(ehr_client_app, "FLIP", lambda: SimpleNamespace(get_dataframe=lambda *_: cohort.copy()))

    first = ehr_client_app._fetch_cohort(_context(0), ehr_client_app.client_identity(_context(0)))
    second = ehr_client_app._fetch_cohort(_context(1), ehr_client_app.client_identity(_context(1)))

    assert ehr_client_app.client_identity(_context(0)) == "site-1"
    assert ehr_client_app.client_identity(_context(1)) == "site-2"
    assert set(first["person_id"]).isdisjoint(second["person_id"])
    assert set(first["person_id"]) | set(second["person_id"]) == set(cohort["person_id"])
    assert 0 < len(first) < len(cohort), "a simulated site got the whole cohort (or nothing)"


def test_an_unidentified_site_is_refused_rather_than_given_the_whole_cohort(ehr_client_app, cohort, monkeypatch):
    monkeypatch.setattr(identity, "FlipConstants", SimpleNamespace(LOCAL_DEV=True))
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    monkeypatch.setattr(ehr_client_app, "FLIP", lambda: SimpleNamespace(get_dataframe=lambda *_: cohort.copy()))
    anonymous = SimpleNamespace(run_config={}, node_config={})

    with pytest.raises(RuntimeError):
        ehr_client_app._fetch_cohort(anonymous, ehr_client_app.client_identity(anonymous))
