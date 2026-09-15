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

"""Feature engineering for the EHR risk-prediction tutorial.

Pure pandas/numpy/torch helpers over the cohort dataframe returned by ``flip.get_dataframe``
(or its local-dev CSV). No FLIP, NVFLARE or Flower imports — the same file serves both
backends and is kept byte-identical between
``fl-tutorials/nvflare/tabular_classification/ehr_risk_prediction/app_files/feature_engineering.py``
and ``fl-tutorials/flower/ehr_risk_prediction/app/feature_engineering.py``
(drift-checked by ``scripts/check_tutorial_sync.sh``).

Preprocessing statistics (imputation medians, standardisation mean/std) are always fitted on
the **local training split only**: no fitted state ever crosses sites, so there is no leakage
between train and evaluation splits and nothing to federate. A real study might replace this
with federated statistics; per-site scaling is the deliberate, simple choice here.
"""

from __future__ import annotations

import hashlib
import re
import warnings

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import roc_auc_score


def select_features(
    dataframe: pd.DataFrame, feature_columns: list[str], label_column: str
) -> tuple[pd.DataFrame, pd.Series]:
    """Project the cohort dataframe onto the configured feature/label columns.

    Args:
        dataframe (pd.DataFrame): The cohort dataframe. Extra columns (``accession_id``,
            ``person_id``) are simply not selected.
        feature_columns (list[str]): Ordered feature column names (``config.json``'s
            ``FEATURES`` — the order defines the model's input layout).
        label_column (str): The binary label column.

    Returns:
        tuple[pd.DataFrame, pd.Series]: ``(features, labels)`` — features coerced to numeric
        (non-numeric cells become NaN for the imputer), labels as float32 0/1.

    Raises:
        KeyError: If any configured column is missing from the dataframe — a broken cohort
            query fails loudly here rather than training on a silently truncated feature set.
    """
    missing = [column for column in [*feature_columns, label_column] if column not in dataframe.columns]
    if missing:
        raise KeyError(f"Cohort dataframe is missing column(s) {missing}; got {list(dataframe.columns)}")
    features = dataframe[list(feature_columns)].apply(pd.to_numeric, errors="coerce")
    labels = pd.to_numeric(dataframe[label_column], errors="coerce").fillna(0).astype(np.float32)
    return features, labels


def _split_key(person_id: object, seed: int) -> str:
    """A stable pseudo-random ordering key for one person.

    ``hashlib`` rather than the built-in ``hash()``: Python salts that per process, so the same
    person would sort differently in two invocations of the same app.
    """
    return hashlib.blake2b(f"{seed}:{person_id}".encode(), digest_size=8).hexdigest()


def split_frame(
    dataframe: pd.DataFrame, val_split: float, test_split: float, seed: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Deterministic person-level train/val/test split (each row is one person).

    Which split a person lands in is a pure function of their ``person_id`` and ``seed``, never of
    their position in the frame. That is load-bearing for Flower, whose ClientApp fetches the cohort
    separately for training and for evaluation: two fetches can return the same persons in a
    different order, and a positional shuffle would then put a person in train on one call and in
    test on the next, leaking trained-on rows into the held-out metric with nothing to show for it.
    Sorting by a stable per-person key keeps the split sizes exact and the membership reproducible.
    """
    if val_split + test_split >= 1.0:
        raise ValueError("Invalid split configuration: val_split + test_split must be < 1.0")
    if "person_id" not in dataframe.columns:
        raise KeyError("split_frame needs a 'person_id' column to split deterministically")
    keys = dataframe["person_id"].map(lambda person_id: _split_key(person_id, seed))
    shuffled = dataframe.assign(_split_key=keys).sort_values("_split_key", kind="mergesort")
    shuffled = shuffled.drop(columns="_split_key").reset_index(drop=True)
    n_total = len(shuffled)
    n_train = int(n_total * (1 - val_split - test_split))
    n_val = int(n_total * (1 - test_split))
    train = shuffled.iloc[:n_train].reset_index(drop=True)
    val = shuffled.iloc[n_train:n_val].reset_index(drop=True)
    test = shuffled.iloc[n_val:].reset_index(drop=True)
    return train, val, test


def fit_preprocessor(train_features: pd.DataFrame) -> dict[str, np.ndarray]:
    """Fit imputation + standardisation statistics on the local TRAINING split only.

    Returns:
        dict[str, np.ndarray]: ``medians`` (per-column imputation values), ``means`` and
        ``stds`` (z-score statistics computed after imputation). A constant column gets
        std 1.0 so standardisation maps it to 0 instead of dividing by zero.
    """
    values = train_features.to_numpy(dtype=np.float64)
    with warnings.catch_warnings():
        # An all-NaN column legitimately reaches nanmedian (its "All-NaN slice" warning) and
        # is imputed to 0.0 on the next line — the constant-column std clamp then maps it to 0.
        warnings.simplefilter("ignore", RuntimeWarning)
        medians = np.nanmedian(values, axis=0)
    medians = np.where(np.isnan(medians), 0.0, medians)
    imputed = np.where(np.isnan(values), medians, values)
    means = imputed.mean(axis=0)
    stds = imputed.std(axis=0)
    stds = np.where(stds < 1e-8, 1.0, stds)
    return {"medians": medians, "means": means, "stds": stds}


def apply_preprocessor(features: pd.DataFrame, stats: dict[str, np.ndarray]) -> np.ndarray:
    """Impute (train-split medians) then z-score (train-split mean/std). Returns float32."""
    values = features.to_numpy(dtype=np.float64)
    imputed = np.where(np.isnan(values), stats["medians"], values)
    return ((imputed - stats["means"]) / stats["stds"]).astype(np.float32)


def partition_for_client(dataframe: pd.DataFrame, client_name: str, num_clients: int) -> pd.DataFrame:
    """Slice a shared local-dev dataframe into this client's disjoint site partition.

    LOCAL_DEV only: the simulator/dev stack hands every client the same CSV, so each keeps
    the rows where ``person_id % num_clients`` matches its own site index — the same modulo
    convention ``omop_db_tools.dataset`` uses to split the mock trusts. In a deployed run the
    trusts already hold disjoint cohorts and this must not be called.

    Args:
        dataframe (pd.DataFrame): The full local-dev cohort (must carry ``person_id``).
        client_name (str): The client's site name; the trailing digits select the partition
            (``site-1``, ``site1`` and ``supernode-1`` all resolve to the first).
        num_clients (int): Number of partitions.

    Returns:
        pd.DataFrame: This client's partition, or the input unchanged when the name carries
        no site number or only one partition is requested.
    """
    match = re.search(r"(\d+)\s*$", str(client_name or ""))
    if match is None or num_clients <= 1:
        return dataframe
    if "person_id" not in dataframe.columns:
        raise KeyError("partition_for_client needs a 'person_id' column to slice deterministically")
    site_index = (int(match.group(1)) - 1) % num_clients
    person_id = pd.to_numeric(dataframe["person_id"], errors="raise").astype(np.int64)
    return dataframe[person_id % num_clients == site_index].reset_index(drop=True)


def to_tensors(features: np.ndarray, labels: pd.Series) -> tuple[torch.Tensor, torch.Tensor]:
    """Preprocessed features + labels as float32 tensors, labels shaped ``[N, 1]`` for BCE."""
    # torch.tensor (not as_tensor) so the tensors own a writable copy — pandas can hand out
    # read-only arrays, which torch warns about on every zero-copy conversion.
    x = torch.tensor(np.asarray(features, dtype=np.float32))
    y = torch.tensor(labels.to_numpy(dtype=np.float32)).reshape(-1, 1)
    return x, y


def positive_class_weight(train_labels: pd.Series) -> torch.Tensor:
    """``pos_weight`` for ``BCEWithLogitsLoss``: n_negative / n_positive on the local train split.

    Falls back to 1.0 when the split holds no positives, so the loss stays finite and the
    degenerate split surfaces as a NaN AUROC (see :func:`safe_auroc`) instead of a crash.
    """
    positives = float(train_labels.sum())
    if positives <= 0:
        return torch.tensor(1.0)
    return torch.tensor((len(train_labels) - positives) / positives)


def safe_auroc(labels: np.ndarray, probabilities: np.ndarray) -> float:
    """AUROC that returns NaN (instead of raising) when only one class is present.

    The NaN is deliberate and visible: both backends' best-model selectors refuse a
    non-finite round with a warning rather than pinning "best" to a degenerate split.
    """
    labels = np.asarray(labels).ravel()
    probabilities = np.asarray(probabilities).ravel()
    if labels.size == 0 or np.unique(labels).size < 2:
        return float("nan")
    return float(roc_auc_score(labels, probabilities))


def binary_accuracy(labels: np.ndarray, probabilities: np.ndarray) -> float:
    """Accuracy at the 0.5 probability threshold; NaN on an empty split."""
    labels = np.asarray(labels).ravel()
    probabilities = np.asarray(probabilities).ravel()
    if labels.size == 0:
        return float("nan")
    return float(((probabilities >= 0.5) == (labels >= 0.5)).mean())
