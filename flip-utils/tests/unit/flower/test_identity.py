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

import pandas as pd
import pytest

from flip.flower import identity
from flip.flower.identity import (
    UNKNOWN_CLIENT,
    check_splits_are_populated,
    client_identity,
    partition_cohort,
    partition_count,
)


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


def test_unresolvable_identity_is_unknown(monkeypatch: pytest.MonkeyPatch):
    """client_identity is a pure name resolver; refusing to guess is partition_cohort's job."""
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    assert client_identity(_context()) == UNKNOWN_CLIENT
    assert client_identity(SimpleNamespace()) == UNKNOWN_CLIENT


def test_partition_count_prefers_explicit_then_node_config_then_two():
    assert partition_count(_context({"num-partitions": "4"}), 3) == 3
    assert partition_count(_context({"num-partitions": "4"})) == 4
    assert partition_count(_context()) == 2


def _cohort(n: int, column: str = "accession_id") -> pd.DataFrame:
    if column == "accession_id":
        return pd.DataFrame({column: [f"subject_{i}" for i in range(n)]})
    return pd.DataFrame({column: list(range(n))})


def test_partitions_are_disjoint_and_covering(monkeypatch: pytest.MonkeyPatch):
    """Every row lands in exactly one partition — the property the whole helper exists for."""
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    cohort = _cohort(40)
    parts = [partition_cohort(cohort, _context({"partition-id": str(i), "num-partitions": "2"})) for i in (0, 1)]
    seen = pd.concat(parts).accession_id.tolist()
    assert sorted(seen) == sorted(cohort.accession_id.tolist())
    assert not set(parts[0].accession_id) & set(parts[1].accession_id)


def test_string_keys_partition_stably_across_processes(monkeypatch: pytest.MonkeyPatch):
    """accession_id is a string, and Python's hash() is salted per interpreter.

    Two ClientApp processes using hash() would disagree about who owns a row, so partitions
    would overlap and lose data. The bucket must be a fixed function of the id.
    """
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    ctx = _context({"partition-id": "0", "num-partitions": "2"})
    first = partition_cohort(_cohort(40), ctx).accession_id.tolist()
    assert first == partition_cohort(_cohort(40), ctx).accession_id.tolist()
    assert first, "partition 0 should not be empty for 40 rows"


def test_numeric_keys_keep_the_modulo_convention(monkeypatch: pytest.MonkeyPatch):
    """person_id keeps `id % n`, matching the tabular tutorial and the trust loader's split."""
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    part = partition_cohort(_cohort(10, "person_id"), _context({"partition-id": "1", "num-partitions": "2"}))
    assert part.person_id.tolist() == [1, 3, 5, 7, 9]


def test_supernode_name_selects_the_same_partition_as_node_config(monkeypatch: pytest.MonkeyPatch):
    """The compose stack and the simulator must agree, or a cohort splits differently per runtime."""
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    by_config = partition_cohort(_cohort(20), _context({"partition-id": "0", "num-partitions": "2"}))
    monkeypatch.setenv("SUPERNODE_NAME", "Trust_1")
    by_env = partition_cohort(_cohort(20), _context({}), num_partitions=2)
    assert by_env.accession_id.tolist() == by_config.accession_id.tolist()


def test_single_partition_returns_the_cohort_unchanged(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    cohort = _cohort(5)
    assert partition_cohort(cohort, _context({"partition-id": "0", "num-partitions": "1"})) is cohort


def test_partitioning_is_skipped_entirely_outside_local_dev(monkeypatch: pytest.MonkeyPatch):
    """A deployed trust's data-access-api already serves it a disjoint cohort.

    Partitioning again would silently discard most of it, so the gate lives in the helper
    rather than being a contract every researcher's app has to remember.
    """
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    monkeypatch.setattr(identity, "FlipConstants", SimpleNamespace(LOCAL_DEV=False))
    cohort = _cohort(40)
    assert partition_cohort(cohort, _context({"partition-id": "0", "num-partitions": "2"})) is cohort


def test_unidentifiable_client_raises_rather_than_returning_everything(monkeypatch: pytest.MonkeyPatch):
    """Never silently hand one client the entire cohort.

    Returning the frame unchanged here would make every client train on all the data, and the
    run would look federated while not being so.
    """
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    with pytest.raises(RuntimeError, match="Cannot tell which of 2 sites"):
        partition_cohort(_cohort(4), _context({"num-partitions": "2"}))


def test_missing_key_column_raises_when_it_would_partition(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    with pytest.raises(RuntimeError, match="Cannot split a cohort 2 ways"):
        partition_cohort(pd.DataFrame({"other": [1, 2]}), _context({"partition-id": "0", "num-partitions": "2"}))


def test_populated_splits_pass_silently():
    check_splits_are_populated({"train": 3, "val": 1}, cohort_rows=5, client_name="site-1")


def test_empty_split_names_the_real_cause():
    """Flower would otherwise surface this as InconsistentMessageReplies, which says nothing."""
    with pytest.raises(ValueError, match="split val is empty"):
        check_splits_are_populated({"train": 2, "val": 0}, cohort_rows=3, client_name="site-1", num_partitions=2)


def test_message_mentions_partitioning_only_when_partitioned():
    with pytest.raises(ValueError, match="split val is empty") as partitioned:
        check_splits_are_populated({"val": 0}, cohort_rows=3, client_name="site-1", num_partitions=2)
    assert "partitioning a shared cohort 2 ways" in str(partitioned.value)
    with pytest.raises(ValueError, match="split val is empty") as whole:
        check_splits_are_populated({"val": 0}, cohort_rows=3, client_name="site-1")
    assert "partitioning" not in str(whole.value)
