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

"""Which site a Flower ClientApp is, resolved the same way in every runtime.

A FLIP Flower app has to answer "which site am I?" in three settings, and the answer must not
require the researcher to edit their app between them:

* **Deployed on FLIP** — the SuperNode container carries ``SUPERNODE_NAME=Trust_1``. The name is
  used only for logging: each trust's data-access-api already serves a disjoint cohort.
* **The local compose stack** (``fl-services/flower``) — same env var, and the name additionally
  selects a partition of the shared dev CSV.
* **The flwr simulator** (``flwr run . local-simulation``) — no container, so no env var. Flower
  instead populates ``context.node_config`` with ``partition-id`` / ``num-partitions``
  (``flwr.simulation.ray_transport.ray_client_proxy``), the same keys a deployed SuperNode accepts
  via ``--node-config 'partition-id=0 num-partitions=2'``. Flower's own generated ClientApp reads
  ``context.node_config.get("partition-id", ...)``, so this is the framework's intended mechanism
  rather than a simulator special case.

Consulting both sources is what lets one app run unchanged in all three.

``flwr`` is deliberately not imported at runtime — flip-utils does not depend on it — so the
``Context`` type sits behind a ``TYPE_CHECKING`` guard and only ``node_config`` is touched.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, flwr is not a runtime dependency
    from flwr.common import Context

PARTITION_ID_KEY = "partition-id"
UNKNOWN_CLIENT = "unknown_client"


def client_identity(context: Context, num_partitions: int | None = None) -> str:
    """Resolve this client's site name identically in simulation and deployment.

    Args:
        context (Context): The Flower ``Context``; only ``node_config`` is read.
        num_partitions (int | None): How many partitions the caller is about to split a shared
            dev cohort into. Pass it whenever the name will select a partition, so an
            unresolvable identity fails loudly instead of silently handing this client the whole
            cohort. ``None`` or ``1`` means the name is only used for logging.

    Returns:
        str: ``SUPERNODE_NAME`` when set (deployed and compose-stack runs), otherwise a
        ``site-<n>`` name derived from ``node_config``'s ``partition-id`` (simulator runs, and any
        SuperNode started with ``--node-config``), otherwise ``"unknown_client"``.

    Raises:
        RuntimeError: If the identity cannot be resolved but ``num_partitions`` is greater than 1.
            Returning an unusable name there would make ``partition_for_client`` hand **every**
            client the full cohort — a run that trains happily and reports plausible metrics while
            not being federated at all.
    """
    supernode_name = os.getenv("SUPERNODE_NAME")
    if supernode_name:
        return supernode_name

    # Simulation writes these as strings; --node-config parses them as ints. Accept either.
    partition_id = getattr(context, "node_config", {}).get(PARTITION_ID_KEY)
    if partition_id is not None:
        return f"site-{int(partition_id) + 1}"

    if num_partitions is not None and num_partitions > 1:
        raise RuntimeError(
            f"Cannot tell which of {num_partitions} sites this client is: neither the "
            f"SUPERNODE_NAME environment variable nor node_config's {PARTITION_ID_KEY!r} is set. "
            "Refusing to continue, because partitioning on an unrecognised name would give every "
            "client the whole cohort and the run would look federated without being so. Set "
            "SUPERNODE_NAME, or start the SuperNode with --node-config "
            "'partition-id=<n> num-partitions=<N>', or run under the flwr simulator."
        )
    return UNKNOWN_CLIENT
