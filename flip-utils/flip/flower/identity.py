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
* **The flwr simulator** — no container, so no env var. Flower instead populates
  ``context.node_config`` with ``partition-id`` / ``num-partitions``
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

from flip.constants.flip_constants import FlipConstants

if TYPE_CHECKING:  # pragma: no cover - typing only, flwr is not a runtime dependency
    from flwr.common import Context

PARTITION_ID_KEY = "partition-id"
NUM_PARTITIONS_KEY = "num-partitions"
UNKNOWN_CLIENT = "unknown_client"
# Cohort columns tried in order when no key column is named.
DEFAULT_KEY_COLUMNS = ("person_id", "accession_id")


def client_identity(context: Context) -> str:
    """Resolve this client's site name identically in simulation and deployment.

    Args:
        context (Context): The Flower ``Context``; only ``node_config`` is read.

    Returns:
        str: ``SUPERNODE_NAME`` when set (deployed and compose-stack runs), otherwise a
        ``site-<n>`` name derived from ``node_config``'s ``partition-id`` (simulator runs, and any
        SuperNode started with ``--node-config``), otherwise ``"unknown_client"``.
    """
    supernode_name = os.getenv("SUPERNODE_NAME")
    if supernode_name:
        return supernode_name

    # Simulation writes these as strings; --node-config parses them as ints. Accept either.
    partition_id = getattr(context, "node_config", {}).get(PARTITION_ID_KEY)
    if partition_id is not None:
        return f"site-{int(partition_id) + 1}"

    return UNKNOWN_CLIENT


def partition_count(context: Context, num_partitions: int | None = None) -> int:
    """How many partitions a shared dev cohort is split into for this run.

    Single source for the count, so a caller reporting on a split cannot disagree with the
    split :func:`partition_cohort` actually performed.

    Args:
        context (Context): The Flower ``Context``; only ``node_config`` is read.
        num_partitions (int | None): An explicit override, returned as-is when given.

    Returns:
        int: ``num_partitions`` if given, else ``node_config``'s ``num-partitions``, else 2 —
        the compose stack sets no ``node_config`` but does run two SuperNodes off one CSV, and
        every shipped tutorial declares ``flip-min-clients = 2``.
    """
    if num_partitions is not None:
        return num_partitions
    declared = getattr(context, "node_config", {}).get(NUM_PARTITIONS_KEY)
    return int(declared) if declared is not None else 2


def _partition_index(context: Context, num_partitions: int) -> int | None:
    """This client's 0-based partition index, derived from :func:`client_identity`'s name.

    Deriving it from the resolved name rather than re-reading the two sources keeps one
    precedence rule: were these to disagree, the logged site name and the data slice would
    come from different places.

    Args:
        context (Context): The Flower ``Context``; only ``node_config`` is read.
        num_partitions (int): Number of partitions the cohort is being split into.

    Returns:
        int | None: The index, or ``None`` when neither source identifies this client.
    """
    # "Trust_1", "site-1" and "supernode-1" all mean the first partition.
    match = re.search(r"(\d+)\s*$", client_identity(context))
    return (int(match.group(1)) - 1) % num_partitions if match else None


def partition_cohort(
    dataframe: pd.DataFrame,
    context: Context,
    num_partitions: int | None = None,
    key_column: str | None = None,
) -> pd.DataFrame:
    """Slice a shared LOCAL_DEV cohort into this client's disjoint partition.

    Outside ``LOCAL_DEV`` this returns the cohort untouched: a deployed trust's data-access-api
    already serves that trust its own rows, and partitioning again would silently discard most
    of them. The check lives here rather than in each caller because forgetting it is silent and
    severe — a real trust would train on half its cohort and report plausible metrics.

    Needed because a shared dev cohort reaches every client identically: the simulator runs all
    ClientApps in one process against one ``DEV_DATAFRAME`` / ``DEV_IMAGES_DIR``, and the compose
    stack hands each SuperNode the same CSV. Only the *images* are split there, by each
    SuperNode's own ``net-N`` mount — which one process has no equivalent of.

    Args:
        dataframe (pd.DataFrame): The shared cohort.
        context (Context): The Flower ``Context``, for this client's partition index.
        num_partitions (int | None): How many partitions; see :func:`partition_count` for
            the default.
        key_column (str | None): Column to partition on. Defaults to the first of
            ``person_id`` / ``accession_id`` present.

    Returns:
        pd.DataFrame: This client's rows, index reset — or the input unchanged when not in
        ``LOCAL_DEV`` or when there is only one partition.

    Raises:
        RuntimeError: If the cohort must be split but this client cannot be identified, or no
            key column is present. Returning everything there would hand *every* client the
            whole cohort — a run that trains happily and reports plausible metrics while not
            being federated at all.
    """
    if not FlipConstants.LOCAL_DEV:
        return dataframe

    num_partitions = partition_count(context, num_partitions)
    if num_partitions <= 1:
        return dataframe

    if key_column is None:
        key_column = next((c for c in DEFAULT_KEY_COLUMNS if c in dataframe.columns), None)
    if key_column is None:
        raise RuntimeError(
            f"Cannot split a cohort {num_partitions} ways: none of {list(DEFAULT_KEY_COLUMNS)} is "
            f"a column (found {list(dataframe.columns)}). Pass key_column= to name the column to "
            "partition on."
        )

    index = _partition_index(context, num_partitions)
    if index is None:
        raise RuntimeError(
            f"Cannot tell which of {num_partitions} sites this client is: neither the "
            f"SUPERNODE_NAME environment variable nor node_config's {PARTITION_ID_KEY!r} is set. "
            "Refusing to continue, because handing every client the whole cohort would look "
            "federated without being so. Set SUPERNODE_NAME, or start the SuperNode with "
            "--node-config 'partition-id=<n> num-partitions=<N>', or run under the flwr simulator."
        )

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
            lambda value: int.from_bytes(hashlib.sha256(value.encode()).digest(), "big") % num_partitions
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
    cohort size.

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
