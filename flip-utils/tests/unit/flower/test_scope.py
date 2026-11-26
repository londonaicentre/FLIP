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

"""Tests for flip.flower.scope — keeping a run on its approved trusts' SuperNodes.

The nodes are real ``NodeInfo`` protos, because the whole point of the filter is the
``name`` the SuperLink puts there (the slot name registered against that SuperNode's
public key); a dict would not pin the field this depends on. The grid underneath is a
stub recording what it was asked to push, so a test can tell "never dispatched" apart
from "dispatched and ignored".
"""

import logging

import pytest
from flwr.app import Message, RecordDict
from flwr.proto.node_pb2 import NodeInfo

from flip.flower.scope import TRUSTS_KEY, TrustScopedGrid, scope_grid_to_trusts, trusts_from_run_config


class _StubGrid:
    """The Grid surface the wrapper delegates to."""

    def __init__(self, nodes: list[tuple[int, str]]):
        self._nodes = [NodeInfo(node_id=node_id, name=name) for node_id, name in nodes]
        self.pushed: list[Message] = []
        self.sent: list[Message] = []
        self.pulled: list = []
        self.run = "run-object"

    def get_nodes(self) -> list[NodeInfo]:
        return list(self._nodes)

    def get_node_ids(self) -> list[int]:
        return [node.node_id for node in self._nodes]

    def set_run(self, run) -> None:
        self.run = run

    def create_message(self, content, message_type, dst_node_id, group_id, ttl=None) -> Message:
        return Message(content, dst_node_id=dst_node_id, message_type=message_type, group_id=group_id)

    def push_messages(self, messages):
        self.pushed = list(messages)
        return [f"msg-{i}" for i, _ in enumerate(self.pushed)]

    def pull_messages(self, reply_to_message_ids=None, *, message_ids=None):
        self.pulled.append((reply_to_message_ids, message_ids))
        return []

    def send_and_receive(self, messages, *, timeout=None):
        self.sent = list(messages)
        self.timeout = timeout
        return []

    def some_future_flwr_method(self) -> str:
        return "delegated"


def _grid(nodes: list[tuple[int, str]] | None = None) -> _StubGrid:
    return _StubGrid(nodes if nodes is not None else [(1, "Trust_1"), (2, "Trust_2"), (3, "Trust_3")])


def _message(dst_node_id: int) -> Message:
    return Message(RecordDict(), dst_node_id=dst_node_id, message_type="train", group_id="1")


# --- reading the key -------------------------------------------------------------------


def test_an_absent_key_means_unscoped():
    """Off-platform runs (simulator, submit_tutorial) have no run config injected at all."""
    assert trusts_from_run_config({}) is None


def test_a_blank_value_means_unscoped():
    """The declared placeholder in each app's pyproject.toml is the empty string."""
    assert trusts_from_run_config({TRUSTS_KEY: ""}) is None
    assert trusts_from_run_config({TRUSTS_KEY: "   "}) is None


def test_the_names_are_read_in_order_without_duplicates():
    assert trusts_from_run_config({TRUSTS_KEY: "Trust_2, Trust_1 ,Trust_2"}) == ["Trust_2", "Trust_1"]


def test_a_non_string_value_is_refused():
    """Coercion here would turn a configuration mistake into an unscoped run."""
    with pytest.raises(ValueError, match="comma-separated string"):
        trusts_from_run_config({TRUSTS_KEY: 2})


def test_a_value_of_separators_alone_is_refused():
    """Present-but-empty must fail loudly: silently unscoped is the one outcome to avoid."""
    with pytest.raises(ValueError, match="names no trust"):
        trusts_from_run_config({TRUSTS_KEY: " , , "})


# --- the scoped grid -------------------------------------------------------------------


def test_only_the_runs_trusts_are_visible():
    scoped = TrustScopedGrid(_grid(), ["Trust_1", "Trust_3"])

    assert sorted(scoped.get_node_ids()) == [1, 3]
    assert sorted(node.name for node in scoped.get_nodes()) == ["Trust_1", "Trust_3"]


def test_a_node_the_superlink_has_no_name_for_is_outside_every_project():
    """The filter is an allow-list, so a node registered without a name is excluded."""
    scoped = TrustScopedGrid(_grid([(1, "Trust_1"), (9, "")]), ["Trust_1"])

    assert list(scoped.get_node_ids()) == [1]


def test_an_approved_trust_that_is_not_connected_simply_is_not_there():
    """No invention: scoping narrows the live view, it never adds a node to it."""
    scoped = TrustScopedGrid(_grid([(1, "Trust_1")]), ["Trust_1", "Trust_2"])

    assert list(scoped.get_node_ids()) == [1]


def test_an_empty_allow_list_is_refused_where_it_is_made():
    """Zero allowed nodes would stall every round in flwr's unbounded sample_nodes wait."""
    with pytest.raises(ValueError, match="at least one trust"):
        TrustScopedGrid(_grid(), [])


def test_messages_to_the_runs_trusts_are_pushed():
    grid = _grid()
    scoped = TrustScopedGrid(grid, ["Trust_1", "Trust_2"])

    ids = scoped.push_messages([_message(1), _message(2)])

    assert len(list(ids)) == 2
    assert [msg.metadata.dst_node_id for msg in grid.pushed] == [1, 2]


def test_a_message_to_a_trust_outside_the_run_is_never_pushed():
    """The whole item: a node on the shared net that this project was not approved for."""
    grid = _grid()
    scoped = TrustScopedGrid(grid, ["Trust_1", "Trust_2"])

    with pytest.raises(ValueError, match=r"not SuperNodes of the trusts"):
        scoped.push_messages([_message(1), _message(3)])

    assert grid.pushed == [], "the whole batch must be refused, not partially delivered"


def test_send_and_receive_is_checked_too():
    """FedAvg's round loop goes through send_and_receive, not push_messages."""
    grid = _grid()
    scoped = TrustScopedGrid(grid, ["Trust_1"])

    with pytest.raises(ValueError, match=r"not SuperNodes of the trusts"):
        scoped.send_and_receive([_message(2)])

    assert grid.sent == []


def test_an_allowed_send_and_receive_passes_the_timeout_through():
    grid = _grid()
    scoped = TrustScopedGrid(grid, ["Trust_1"])

    scoped.send_and_receive([_message(1)], timeout=12.0)

    assert [msg.metadata.dst_node_id for msg in grid.sent] == [1]
    assert grid.timeout == 12.0


def test_a_generator_of_messages_is_still_delivered():
    """The checks materialise the iterable; consuming it twice would deliver nothing."""
    grid = _grid()
    scoped = TrustScopedGrid(grid, ["Trust_1"])

    scoped.push_messages(_message(1) for _ in range(2))

    assert len(grid.pushed) == 2


def test_create_message_refuses_a_destination_outside_the_run():
    scoped = TrustScopedGrid(_grid(), ["Trust_1"])

    with pytest.raises(ValueError, match=r"not SuperNodes of the trusts"):
        scoped.create_message(RecordDict(), "train", 3, "1")

    assert scoped.create_message(RecordDict(), "train", 1, "1").metadata.dst_node_id == 1


def test_pulling_replies_and_the_run_object_are_delegated_unchanged():
    """A reply can only come from a node we were allowed to write to."""
    grid = _grid()
    scoped = TrustScopedGrid(grid, ["Trust_1"])

    scoped.pull_messages(["abc"])
    scoped.set_run("another-run")

    assert grid.pulled == [(["abc"], None)]
    assert scoped.run == "another-run"


def test_the_deprecated_message_ids_alias_is_forwarded_as_given():
    """flwr still accepts it, and swallowing it here would lose every reply."""
    grid = _grid()
    scoped = TrustScopedGrid(grid, ["Trust_1"])

    scoped.pull_messages(message_ids=["abc"])

    assert grid.pulled == [(None, ["abc"])]


def test_anything_else_on_the_grid_is_delegated():
    """A minor-version addition to Grid must not fail the run with an AttributeError."""
    assert TrustScopedGrid(_grid(), ["Trust_1"]).some_future_flwr_method() == "delegated"


# --- the helper strategies use ----------------------------------------------------------


def test_scoping_to_nothing_leaves_the_grid_alone_and_says_so(caplog: pytest.LogCaptureFixture):
    grid = _grid()
    with caplog.at_level(logging.WARNING):
        assert scope_grid_to_trusts(grid, None) is grid
        assert scope_grid_to_trusts(grid, []) is grid

    assert TRUSTS_KEY in caplog.text


def test_scoping_is_idempotent():
    """Strategies scope on every hook, so stacking wrappers must not be possible."""
    scoped = scope_grid_to_trusts(_grid(), ["Trust_1"])

    assert scope_grid_to_trusts(scoped, ["Trust_1"]) is scoped


def test_rescoping_narrows_and_never_widens():
    """Scoping an already-scoped grid nests, so the result is the intersection.

    Fail-closed on purpose: the alternative — unwrapping and re-wrapping — would let a
    second call hand the run nodes the first call had excluded.
    """
    scoped = scope_grid_to_trusts(_grid(), ["Trust_1"])

    widened = scope_grid_to_trusts(scoped, ["Trust_1", "Trust_2"])

    assert list(widened.get_node_ids()) == [1]
