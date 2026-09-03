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

"""Which site a Flower ClientApp is, and which slice of a shared dev cohort is its own.

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

import hashlib
import os
import re
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only, flwr is not a runtime dependency
    from flwr.common import Context

PARTITION_ID_KEY = "partition-id"
NUM_PARTITIONS_KEY = "num-partitions"
UNKNOWN_CLIENT = "unknown_client"
# Cohort columns tried in order when no key column is named.
DEFAULT_KEY_COLUMNS = ("person_id", "accession_id")


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


def partition_index(context: Context, num_partitions: int) -> int | None:
    """This client's 0-based partition index, from the same sources as :func:`client_identity`.

    Args:
        context (Context): The Flower ``Context``; only ``node_config`` is read.
        num_partitions (int): Number of partitions the cohort is being split into.

    Returns:
        int | None: The index, or ``None`` when neither source identifies this client.
    """
    supernode_name = os.getenv("SUPERNODE_NAME")
    if supernode_name:
        # "Trust_1", "site-1" and "supernode-1" all mean the first partition.
        match = re.search(r"(\d+)\s*$", supernode_name)
        return (int(match.group(1)) - 1) % num_partitions if match else None
    partition_id = getattr(context, "node_config", {}).get(PARTITION_ID_KEY)
    return int(partition_id) % num_partitions if partition_id is not None else None


def partition_cohort(
    dataframe: pd.DataFrame,
    context: Context,
    num_partitions: int | None = None,
    key_column: str | None = None,
) -> pd.DataFrame:
    """Slice a shared LOCAL_DEV cohort into this client's disjoint partition.

    **LOCAL_DEV only.** A deployed trust's data-access-api already serves that trust its own
    cohort; partitioning again there would silently discard most of it. Callers must gate on
    ``FlipConstants.LOCAL_DEV``.

    Needed because a shared dev cohort reaches every client identically: the simulator runs all
    ClientApps in one process against one ``DEV_DATAFRAME`` / ``DEV_IMAGES_DIR``, and the compose
    stack hands each SuperNode the same CSV. (The imaging tutorials have historically relied on
    each SuperNode's own ``net-N`` image mount for disjointness, which a single process cannot
    reproduce — hence this.)

    Args:
        dataframe (pd.DataFrame): The shared cohort.
        context (Context): The Flower ``Context``, for this client's partition index.
        num_partitions (int | None): How many partitions. Defaults to ``node_config``'s
            ``num-partitions``, then to 2.
        key_column (str | None): Column to partition on. Defaults to the first of
            ``person_id`` / ``accession_id`` present.

    Returns:
        pd.DataFrame: This client's rows, index reset. The input unchanged when there is one
        partition, when no key column is present, or when this client cannot be identified —
        callers wanting the last case to be fatal should use :func:`client_identity`'s
        ``num_partitions`` guard, which raises.
    """
    if num_partitions is None:
        declared = getattr(context, "node_config", {}).get(NUM_PARTITIONS_KEY)
        num_partitions = int(declared) if declared is not None else 2
    if num_partitions <= 1:
        return dataframe

    if key_column is None:
        key_column = next((c for c in DEFAULT_KEY_COLUMNS if c in dataframe.columns), None)
    if key_column is None:
        return dataframe

    index = partition_index(context, num_partitions)
    if index is None:
        return dataframe

    keys = dataframe[key_column]
    numeric = pd.to_numeric(keys, errors="coerce")
    if not numeric.isna().any():
        # Numeric ids keep the modulo convention the tabular tutorial and the trust loader use,
        # so a cohort splits the same way however it is partitioned.
        buckets = numeric.astype("int64") % num_partitions
    else:
        # Non-numeric ids (accession_id is a string) need a hash that is stable ACROSS PROCESSES.
        # Python's built-in hash() is salted per interpreter, so two ClientApp processes would
        # disagree and the partitions would overlap and lose rows.
        buckets = keys.astype(str).map(
            lambda value: int(hashlib.sha256(value.encode()).hexdigest(), 16) % num_partitions
        )
    return dataframe[buckets == index].reset_index(drop=True)


def check_splits_are_populated(
    splits: dict[str, int],
    cohort_rows: int,
    client_name: str,
    num_partitions: int | None = None,
) -> None:
    """Fail with the actual cause when a site's cohort is too small to split.

    An empty split does not crash where it happens. The client trains, then returns a metric
    record missing the keys the empty pass could not produce, and Flower rejects the round with
    ``InconsistentMessageReplies`` raised deep inside the strategy — which says nothing about
    cohort size. Observed with the Flower spleen tutorial's shipped 6-case dev cohort: split
    across two sites, then 40/30/30, one site's validation split came out empty.

    Args:
        splits (dict[str, int]): Split name to row count, e.g. ``{"train": 2, "val": 0}``.
        cohort_rows (int): Rows this client holds after any partitioning.
        client_name (str): This client's site name, for the message.
        num_partitions (int | None): Partitions the shared cohort was split into, if it was.

    Raises:
        ValueError: If any split is empty.
    """
    empty = [name for name, count in splits.items() if count <= 0]
    if not empty:
        return
    detail = ", ".join(f"{name}={count}" for name, count in splits.items())
    partitioned = (
        f" after partitioning a shared cohort {num_partitions} ways" if num_partitions and num_partitions > 1 else ""
    )
    raise ValueError(
        f"{client_name}: split {', '.join(empty)} is empty ({detail}) from {cohort_rows} row(s)"
        f"{partitioned}. The cohort is too small to split this many ways — download more cases, "
        "or reduce the number of sites. Left alone this surfaces much later as Flower's "
        "InconsistentMessageReplies, which does not name the cause."
    )
