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

"""Keep a Flower run on the SuperNodes of the trusts it was approved for.

A FLIP Flower net is shared: every trust onboarded onto it registers a SuperNode with the
same SuperLink, and flwr's ``FedAvg`` samples from *every* connected node
(``grid.get_node_ids()`` in ``flwr.serverapp.strategy.fedavg``). A run approved for two
trusts on a four-trust net would therefore dispatch its training and evaluation rounds to
all four. The FLARE adapter has never had this gap — it builds a ``deploy_map`` and
``mandatory_clients`` from the project's trusts.

The missing piece was never the identity, only the list. Each SuperNode authenticates to the
SuperLink with the key pair in its participant kit, and the hub registers that **public** key
under the kit's slot name (``flwr supernode register <key> --name Trust_<N>``, see
``fl-services/flower/register-supernode-keys.sh``). The SuperLink keeps the name beside the
key and reports it to the ServerApp as ``NodeInfo.name`` (``Grid.get_nodes()``). That name is
SuperLink-side information about an authenticated key — it is not ``node_config``, and nothing
a ClientApp runs can claim another trust's name without that trust's private key. It is also
the *same* namespace as the hub's FL kit slot names, which is what flip-api already sends to
the fl-api as the participating-trust list.

So this module closes the loop: fl-api-flower writes the approved slot names into the run
config as :data:`TRUSTS_KEY`, :func:`trusts_from_run_config` reads them, and
:class:`TrustScopedGrid` presents flwr with a Grid that contains the project's nodes and
nothing else — so sampling, the ``min_available_nodes`` wait and every dispatch are scoped,
with no change to the strategies themselves.

Scope of the guarantee. This is the platform's server-side default and it binds every app FLIP
deploys (``app/server_app.py`` and ``app/strategy.py`` are template files the bundler refuses
to let an uploaded file replace). It is not a process boundary: the ServerApp also imports the
uploaded ``models.py``, so code running *inside* the ServerApp process is not contained by it.
Containing that needs a per-run SuperLink/SuperNode (FLIP#1773, FLIP#1774).
"""

from collections.abc import Iterable, Mapping
from logging import WARNING
from typing import Any

from flwr.app import Message, RecordDict, UserConfigValue
from flwr.common import log
from flwr.proto.node_pb2 import NodeInfo  # type: ignore[import-untyped]
from flwr.serverapp import Grid
from flwr.supercore.run import Run

__all__ = ["TRUSTS_KEY", "TrustScopedGrid", "scope_grid_to_trusts", "trusts_from_run_config"]

# Run-config key carrying the slot names of the trusts the run was approved for, injected by
# fl-api-flower at submit time from the list flip-api sends (``UploadAppRequest.trusts``).
# Declared under ``[tool.flwr.app.config]`` in each app's pyproject.toml, as the other ``flip-*``
# keys are — flwr rejects a ``--run-config`` override it has not been declared.
#
# A comma-separated string rather than a list: a Flower run-config value is a scalar
# (``UserConfigValue`` is bool | bytes | float | int | str), so a TOML array cannot be carried.
TRUSTS_KEY = "flip-trusts"


def trusts_from_run_config(run_config: Mapping[str, UserConfigValue]) -> list[str] | None:
    """Read the approved trusts' slot names from a Flower run config.

    Kept here rather than in each app template for the same reason as
    :func:`~flip.flower.strategy.min_clients_from_run_config`: the templates carry no
    run-config parsing.

    Args:
        run_config (Mapping[str, UserConfigValue]): The app's run config (``Context.run_config``).

    Returns:
        list[str] | None: The slot names, in the order given and without duplicates, or ``None``
        when the key is absent or blank — meaning "this run is not scoped", which is the
        simulator and ``submit_tutorial`` case (nothing injects a run config there, and the
        simulator's nodes carry no registered names at all). Every run the hub submits carries
        the key, because fl-api-flower refuses an upload that names no trust.

    Raises:
        ValueError: If the value is not a string, or is a string of nothing but separators.
            Both would otherwise degrade into "unscoped" — the one failure mode this must never
            take silently, since it is what the scoping exists to prevent.
    """
    value = run_config.get(TRUSTS_KEY)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"{TRUSTS_KEY} must be a comma-separated string of trust slot names, got {value!r} — "
            "a non-string value cannot name the nodes the run is allowed to reach"
        )
    if not value.strip():
        return None
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise ValueError(
            f"{TRUSTS_KEY}={value!r} names no trust — a run config that carries the key but no "
            "name would leave the run unscoped"
        )
    return list(dict.fromkeys(names))


class TrustScopedGrid(Grid):
    """A ``Grid`` that exposes only the SuperNodes of the run's approved trusts.

    Wraps the Grid the ServerApp was handed and filters it on the SuperLink-authenticated
    ``NodeInfo.name`` — the slot name the hub registered against that SuperNode's public key.
    Every read is filtered (so flwr's sampling and its ``min_available_nodes`` wait only ever
    see the project's nodes) and every write is checked (so a message addressed to a node
    outside the project fails the run rather than reaching a trust that never approved it).

    A node whose name the SuperLink does not know (registered without one) is outside every
    project: the filter is an allow-list, so it is excluded.

    Args:
        grid: The Grid to wrap.
        trusts: Slot names of the trusts the run was approved for.

    Raises:
        ValueError: If ``trusts`` is empty — an empty allow-list would stall every round in
            flwr's unbounded ``sample_nodes`` wait, so it is refused where the mistake is made.
    """

    def __init__(self, grid: Grid, trusts: Iterable[str]) -> None:
        self._grid = grid
        self._trusts = frozenset(trusts)
        if not self._trusts:
            raise ValueError(
                "TrustScopedGrid needs at least one trust slot name: an empty allow-list leaves "
                "no node a round can be dispatched to"
            )

    @property
    def trusts(self) -> frozenset[str]:
        """The slot names this grid is scoped to."""
        return self._trusts

    def set_run(self, run: Run) -> None:
        """Set the run this Grid operates in (delegated unchanged)."""
        self._grid.set_run(run)

    @property
    def run(self) -> Run:
        """Run information (delegated unchanged)."""
        return self._grid.run

    def get_nodes(self) -> Iterable[NodeInfo]:
        """The connected nodes whose registered name is one of the run's trusts."""
        return [node for node in self._grid.get_nodes() if getattr(node, "name", "") in self._trusts]

    def get_node_ids(self) -> Iterable[int]:
        """The node ids of those nodes — what flwr's ``sample_nodes`` draws from."""
        return [node.node_id for node in self.get_nodes()]

    def create_message(
        self,
        content: RecordDict,
        message_type: str,
        dst_node_id: int,
        group_id: str,
        ttl: float | None = None,
    ) -> Message:
        """Create a message, refusing a destination outside the run's trusts."""
        self._assert_in_scope([dst_node_id])
        return self._grid.create_message(content, message_type, dst_node_id, group_id, ttl)

    def push_messages(self, messages: Iterable[Message]) -> Iterable[str]:
        """Push messages, refusing any addressed outside the run's trusts."""
        pushed = self._checked(messages)
        return self._grid.push_messages(pushed)

    def pull_messages(
        self,
        reply_to_message_ids: Iterable[str] | None = None,
        *,
        message_ids: Iterable[str] | None = None,
    ) -> Iterable[Message]:
        """Pull replies (delegated unchanged: a reply can only come from a node we wrote to)."""
        if message_ids is not None:
            return self._grid.pull_messages(reply_to_message_ids, message_ids=message_ids)
        return self._grid.pull_messages(reply_to_message_ids)

    def send_and_receive(self, messages: Iterable[Message], *, timeout: float | None = None) -> Iterable[Message]:
        """Send and collect replies, refusing any message addressed outside the run's trusts."""
        sent = self._checked(messages)
        return self._grid.send_and_receive(sent, timeout=timeout)

    def __getattr__(self, name: str) -> Any:
        """Delegate anything flwr adds to ``Grid`` that this wrapper does not name itself.

        Only reached for attributes this class does not define, so it cannot weaken the
        filtered methods above. It keeps a minor-version addition to ``Grid`` from turning into
        an AttributeError mid-run, which would fail the run for a reason unrelated to scoping.
        """
        return getattr(self._grid, name)

    def _checked(self, messages: Iterable[Message]) -> list[Message]:
        """Materialise ``messages`` (they may be a generator) and check every destination."""
        checked = list(messages)
        self._assert_in_scope([msg.metadata.dst_node_id for msg in checked])
        return checked

    def _assert_in_scope(self, node_ids: Iterable[int]) -> None:
        """Fail the run rather than let a message reach a trust outside the project.

        Raises:
            ValueError: If any id is not one of the run's trusts' nodes.
        """
        allowed = set(self.get_node_ids())
        outside = sorted({node_id for node_id in node_ids if node_id not in allowed})
        if outside:
            raise ValueError(
                f"Nodes {outside} are not SuperNodes of the trusts this run was approved for "
                f"({sorted(self._trusts)}), so no message may be sent to them"
            )


def scope_grid_to_trusts(grid: Grid, trusts: Iterable[str] | None) -> Grid:
    """Wrap ``grid`` so the run only ever reaches its approved trusts' SuperNodes.

    Idempotent for the same set of trusts, so a strategy may call it on every hook without
    stacking wrappers. Called on an already-scoped grid with a *different* set it nests, which
    intersects: scoping can only ever narrow, never hand back a node an earlier call excluded.

    Args:
        grid (Grid): The Grid the ServerApp was handed.
        trusts (Iterable[str] | None): Slot names of the approved trusts, or ``None`` when the
            run config names none (simulator and tutorial runs).

    Returns:
        Grid: A :class:`TrustScopedGrid`, or ``grid`` unchanged when there is no list to scope
        to — logged at WARNING, because on the platform that means the run config lost the key
        and the run would reach every node on the net.
    """
    names = list(trusts) if trusts is not None else []
    if not names:
        log(
            WARNING,
            "This run is not scoped to any trust (%s is unset), so every SuperNode connected to "
            "this SuperLink can be sampled. Expected only off-platform runs (flwr simulator, "
            "submit_tutorial).",
            TRUSTS_KEY,
        )
        return grid
    if isinstance(grid, TrustScopedGrid) and grid.trusts == frozenset(names):
        return grid
    return TrustScopedGrid(grid, names)
