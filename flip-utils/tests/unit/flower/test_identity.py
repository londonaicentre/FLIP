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

"""Tests for flip.flower.identity.

The flwr package is not a runtime dependency of flip-utils; the module under test references
Flower types behind a ``TYPE_CHECKING`` guard and touches only ``node_config``. These tests
therefore use a plain stand-in rather than importing ``flwr``.
"""

from types import SimpleNamespace

import pytest

from flip.flower.identity import UNKNOWN_CLIENT, client_identity


def _context(node_config: dict | None = None) -> SimpleNamespace:
    """A stand-in Flower Context carrying only the attribute the helper reads."""
    return SimpleNamespace(node_config=node_config if node_config is not None else {})


def test_supernode_name_wins_when_set(monkeypatch: pytest.MonkeyPatch):
    """Deployed and compose-stack runs: the container's own name is the most meaningful."""
    monkeypatch.setenv("SUPERNODE_NAME", "Trust_1")
    assert client_identity(_context({"partition-id": "0"})) == "Trust_1"


def test_partition_id_is_used_when_no_env(monkeypatch: pytest.MonkeyPatch):
    """Simulator runs: no container, so identity comes from node_config."""
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    assert client_identity(_context({"partition-id": "0"})) == "site-1"
    assert client_identity(_context({"partition-id": "1"})) == "site-2"


def test_partition_id_accepts_int_and_str(monkeypatch: pytest.MonkeyPatch):
    """The simulator writes str(partition_id); --node-config parses it as an int."""
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    assert client_identity(_context({"partition-id": 1})) == "site-2"
    assert client_identity(_context({"partition-id": "1"})) == "site-2"


def test_empty_supernode_name_falls_through(monkeypatch: pytest.MonkeyPatch):
    """An exported-but-empty env var must not shadow a usable node_config."""
    monkeypatch.setenv("SUPERNODE_NAME", "")
    assert client_identity(_context({"partition-id": "0"})) == "site-1"


def test_unresolvable_identity_is_unknown_when_not_partitioning(monkeypatch: pytest.MonkeyPatch):
    """With one partition (or none declared) the name is only used for logging."""
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    assert client_identity(_context()) == UNKNOWN_CLIENT
    assert client_identity(_context(), num_partitions=1) == UNKNOWN_CLIENT


def test_unresolvable_identity_raises_when_it_would_partition(monkeypatch: pytest.MonkeyPatch):
    """The whole point of the helper: never silently hand one client the entire cohort.

    ``partition_for_client`` returns the frame unchanged for a name with no site number, so
    without this guard every client would train on all the data and the run would look
    federated while not being so.
    """
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    with pytest.raises(RuntimeError, match="Cannot tell which of 2 sites"):
        client_identity(_context(), num_partitions=2)


def test_context_without_node_config_is_tolerated(monkeypatch: pytest.MonkeyPatch):
    """Older/!simulated contexts may not carry node_config at all."""
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    assert client_identity(SimpleNamespace()) == UNKNOWN_CLIENT
