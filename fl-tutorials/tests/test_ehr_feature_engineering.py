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

"""Unit tests for the EHR risk-prediction tutorial's shared feature engineering and model.

The module under test is the NVFLARE copy — ``scripts/check_tutorial_sync.sh`` pins the Flower
copy byte-identical to it, so one import covers both backends. Fixtures are synthesised
in-process (no dataset download); everything runs on CPU in the flip-utils[full] env.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest
import torch
from tutorial_apps import TUTORIALS_ROOT

NVFLARE_APP_FILES = TUTORIALS_ROOT / "nvflare" / "tabular_classification" / "ehr_risk_prediction" / "app_files"
FLOWER_APP = TUTORIALS_ROOT / "flower" / "ehr_risk_prediction" / "app"


def _load_module(module_name: str, path: Path) -> ModuleType:
    """Import a loose tutorial script from its file path (the apps are not packages)."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None, f"cannot load {path}"
    assert spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


fe = _load_module("ehr_under_test.feature_engineering", NVFLARE_APP_FILES / "feature_engineering.py")
models = _load_module("ehr_under_test.models", NVFLARE_APP_FILES / "models.py")


@pytest.fixture
def cohort() -> pd.DataFrame:
    """A small synthetic cohort in the derived-dataframe shape, with missing values."""
    rng = np.random.default_rng(7)
    n = 40
    frame = pd.DataFrame(
        {
            "person_id": np.arange(n),
            "accession_id": [str(i) for i in range(n)],
            "age": rng.integers(20, 90, n).astype(float),
            "is_female": rng.integers(0, 2, n),
            "label": rng.integers(0, 2, n),
        }
    )
    frame.loc[frame.index[:5], "age"] = np.nan
    return frame


def test_select_features_rejects_missing_columns(cohort: pd.DataFrame):
    with pytest.raises(KeyError, match="missing column"):
        fe.select_features(cohort, ["age", "not_a_column"], "label")


def test_select_features_drops_unlisted_columns(cohort: pd.DataFrame):
    features, labels = fe.select_features(cohort, ["age", "is_female"], "label")
    assert list(features.columns) == ["age", "is_female"]
    assert labels.dtype == np.float32
    assert set(labels.unique()) <= {0.0, 1.0}


def test_split_frame_is_deterministic_and_covering(cohort: pd.DataFrame):
    train, val, test = fe.split_frame(cohort, val_split=0.2, test_split=0.2, seed=42)
    train2, val2, test2 = fe.split_frame(cohort, val_split=0.2, test_split=0.2, seed=42)
    pd.testing.assert_frame_equal(train, train2)
    pd.testing.assert_frame_equal(val, val2)
    pd.testing.assert_frame_equal(test, test2)
    ids = pd.concat([train, val, test])["person_id"]
    assert sorted(ids) == sorted(cohort["person_id"])
    assert len(train) == 24
    assert len(val) == 8
    assert len(test) == 8


def test_split_frame_rejects_degenerate_splits(cohort: pd.DataFrame):
    with pytest.raises(ValueError, match="val_split"):
        fe.split_frame(cohort, val_split=0.6, test_split=0.5, seed=0)


def test_preprocessor_statistics_come_from_the_train_split_only(cohort: pd.DataFrame):
    train, val, _ = fe.split_frame(cohort, val_split=0.2, test_split=0.2, seed=1)
    train_features, _ = fe.select_features(train, ["age", "is_female"], "label")
    val_features, _ = fe.select_features(val, ["age", "is_female"], "label")

    stats = fe.fit_preprocessor(train_features)
    assert stats["medians"][0] == np.nanmedian(train_features["age"].to_numpy())

    # Applying to train yields ~zero mean/unit std; applying the SAME stats to val does not
    # re-fit (val's transformed mean is whatever the train statistics make it).
    transformed_train = fe.apply_preprocessor(train_features, stats)
    assert np.all(np.isfinite(transformed_train))
    np.testing.assert_allclose(transformed_train.mean(axis=0), 0.0, atol=1e-5)
    transformed_val = fe.apply_preprocessor(val_features, stats)
    assert np.all(np.isfinite(transformed_val))
    assert transformed_train.dtype == np.float32


def test_preprocessor_handles_constant_and_all_nan_columns():
    train = pd.DataFrame({"constant": [3.0, 3.0, 3.0], "empty": [np.nan, np.nan, np.nan]})
    stats = fe.fit_preprocessor(train)
    transformed = fe.apply_preprocessor(train, stats)
    # Constant column standardises to 0 (std clamped to 1), all-NaN column imputes to 0.
    assert np.all(np.isfinite(transformed))
    np.testing.assert_allclose(transformed, 0.0)


@pytest.mark.parametrize("names", [("site-1", "site-2"), ("Trust_1", "Trust_2"), ("supernode-1", "supernode-2")])
def test_partition_for_client_is_disjoint_covering_and_modulo(cohort: pd.DataFrame, names: tuple[str, str]):
    first = fe.partition_for_client(cohort, names[0], num_clients=2)
    second = fe.partition_for_client(cohort, names[1], num_clients=2)
    assert set(first["person_id"]).isdisjoint(second["person_id"])
    assert sorted([*first["person_id"], *second["person_id"]]) == sorted(cohort["person_id"])
    # The modulo convention matches omop_db_tools.dataset's mock-trust split.
    assert (first["person_id"] % 2 == 0).all()
    assert (second["person_id"] % 2 == 1).all()


def test_partition_for_client_without_a_site_number_returns_everything(cohort: pd.DataFrame):
    assert fe.partition_for_client(cohort, "default", num_clients=2) is cohort
    assert fe.partition_for_client(cohort, "site-1", num_clients=1) is cohort


def test_positive_class_weight_balances_and_survives_no_positives():
    assert fe.positive_class_weight(pd.Series([0.0, 1.0, 0.0, 0.0])).item() == pytest.approx(3.0)
    assert fe.positive_class_weight(pd.Series([0.0, 0.0])).item() == pytest.approx(1.0)


def test_safe_auroc_is_nan_on_a_single_class_split_and_exact_on_separable_data():
    assert np.isnan(fe.safe_auroc(np.zeros(4), np.array([0.1, 0.2, 0.3, 0.4])))
    assert np.isnan(fe.safe_auroc(np.empty(0), np.empty(0)))
    assert fe.safe_auroc(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])) == pytest.approx(1.0)


def test_binary_accuracy_thresholds_at_half():
    assert fe.binary_accuracy(np.array([0, 1, 1]), np.array([0.2, 0.9, 0.4])) == pytest.approx(2 / 3)
    assert np.isnan(fe.binary_accuracy(np.empty(0), np.empty(0)))


def test_model_forward_shape_and_state_dict_roundtrip():
    n_features = 9
    model = models.get_model(n_features)
    logits = model(torch.randn(5, n_features))
    assert logits.shape == (5, 1)
    assert torch.isfinite(logits).all()

    # The state dict round-trips through the same factory — the wire contract both
    # backends' aggregation relies on.
    clone = models.get_model(n_features)
    clone.load_state_dict(model.state_dict())
    clone.eval()
    model.eval()
    batch = torch.randn(3, n_features)
    torch.testing.assert_close(model(batch), clone(batch))


def test_default_model_width_and_configs_agree_across_backends():
    """FEATURES/LABEL_COLUMN drive both backends' preprocessing and the model input width.

    The two config.json files are NOT sync-checked (Flower's carries no NVFLARE-only keys),
    so this is the guard that keeps the shared columns — and therefore the model each
    backend builds — from drifting apart.
    """
    nvflare_config = json.loads((NVFLARE_APP_FILES / "config.json").read_text())
    flower_config = json.loads((FLOWER_APP / "config.json").read_text())
    assert nvflare_config["FEATURES"] == flower_config["FEATURES"]
    assert nvflare_config["LABEL_COLUMN"] == flower_config["LABEL_COLUMN"]

    default_model = models.get_model()
    first_linear = next(layer for layer in default_model if isinstance(layer, torch.nn.Linear))
    assert first_linear.in_features == len(nvflare_config["FEATURES"])


def test_to_tensors_shapes_labels_for_bce(cohort: pd.DataFrame):
    features, labels = fe.select_features(cohort, ["age", "is_female"], "label")
    stats = fe.fit_preprocessor(features)
    x, y = fe.to_tensors(fe.apply_preprocessor(features, stats), labels)
    assert x.dtype == torch.float32
    assert y.shape == (len(cohort), 1)
    assert y.dtype == torch.float32
