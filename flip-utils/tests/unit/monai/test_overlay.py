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

"""Unit tests for the monai-flip.yaml v0.1 overlay schema (FLIP#1105)."""

import copy
import sys
from pathlib import Path

import pytest
import yaml

from flip.constants.flip_constants import ResourceType
from flip.monai.overlay import BundleSource, OverlayError, load_overlay, parse_overlay

VALID = {
    "bundle": {
        "source": "monai-model-zoo",
        "name": "spleen_ct_segmentation",
        "version": "0.6.1",
        "configs": {
            "train": "configs/train.json",
            "evaluate": ["configs/train.json", "configs/evaluate.json"],
        },
    },
    "flip": {
        "query_ref": "query.sql",
        "imaging": {
            "resource": "NIFTI",
            "keys": {"image": {"pattern": "input_*.nii.gz"}, "label": {"pattern": "label_*.nii.gz"}},
        },
        "fl": {
            "local_epochs": 1,
            "exchange": {
                "weights": "weight_diff",
                "metrics": ["dice", "loss"],
                "statistics": ["data_count", "fail_count", "data_stats"],
            },
        },
        "governance": {"profile": "monai-flip-v0.1", "allowed_outputs": ["metrics", "model"]},
    },
}


def _with(path: str, value):
    """Return a deep copy of VALID with the dotted ``path`` set to ``value`` (or deleted if value is ...)."""
    data = copy.deepcopy(VALID)
    *parents, leaf = path.split(".")
    node = data
    for p in parents:
        node = node[p]
    if value is ...:
        del node[leaf]
    else:
        node[leaf] = value
    return data


def test_valid_overlay_parses():
    overlay = parse_overlay(VALID)
    assert overlay.bundle.source is BundleSource.MONAI_MODEL_ZOO
    assert overlay.flip.imaging.resource is ResourceType.NIFTI
    assert overlay.flip.imaging.keys["label"].pattern == "label_*.nii.gz"
    assert overlay.bundle.configs.evaluate == ["configs/train.json", "configs/evaluate.json"]


def test_seg_resource_resolves_by_value():
    """The overlay says SEG; the enum member is SEGMENTATION."""
    overlay = parse_overlay(_with("flip.imaging.resource", "SEG"))
    assert overlay.flip.imaging.resource is ResourceType.SEGMENTATION


def test_defaults_fill_optional_sections():
    data = _with("flip.fl", ...)
    overlay = parse_overlay(data)
    assert overlay.flip.fl.local_epochs == 1
    assert overlay.flip.fl.exchange.weights == "weight_diff"


def test_yaml_config_paths_accepted():
    overlay = parse_overlay(_with("bundle.configs", {"train": "configs/train.yaml"}))
    assert overlay.bundle.configs.train == "configs/train.yaml"


def test_load_overlay_from_file(tmp_path: Path):
    path = tmp_path / "monai-flip.yaml"
    path.write_text(yaml.safe_dump(VALID))
    assert load_overlay(path).bundle.name == "spleen_ct_segmentation"


@pytest.mark.parametrize(
    ("path", "value", "fragment"),
    [
        # Unknown keys fail closed, including an attempt to pin the round-loop class.
        ("flip.fl.client_algo", "monai.fl.client.MonaiAlgo", "flip.fl.client_algo"),
        ("bundle.extra", "x", "bundle.extra"),
        # Cohort source/join_key are fixed in v0.1; the old cohort block is gone.
        ("flip.cohort", {"source": "omop"}, "flip.cohort"),
        ("bundle.source", "github", "bundle.source"),
        ("bundle.name", "../escape", "bundle.name"),
        ("bundle.configs.train", "train.json", "under configs/"),
        ("bundle.configs.train", "configs/train.py", "configs/train.py"),
        ("bundle.configs.train", "configs/../../etc/passwd.json", "no '..'"),
        ("bundle.configs.evaluate", [], "at least one"),
        ("flip.query_ref", "/abs/query.sql", "inside the bundle"),
        ("flip.query_ref", "query.txt", ".sql"),
        ("flip.imaging.resource", "ALL", "ALL cannot be paired"),
        ("flip.imaging.resource", "PNG", "flip.imaging.resource"),
        ("flip.imaging.keys", {"label": {"pattern": "label_*.nii.gz"}}, "'image' key"),
        ("flip.imaging.keys", {"image": "input_*.nii.gz"}, "flip.imaging.keys.image"),
        ("flip.imaging.keys", {"image": {"pattern": "a/*.nii.gz"}}, "file-name glob"),
        ("flip.imaging.keys", {"image": {"pattern": "x*"}, "label": {"pattern": "x*"}}, "distinct"),
        ("flip.imaging.keys", {"image": {"pattern": "x*"}, "my-key": {"pattern": "y*"}}, "identifiers"),
        ("flip.fl.local_epochs", 0, "flip.fl.local_epochs"),
        ("flip.fl.exchange", {"statistics": ["histogram"]}, "statistics"),
        ("flip.governance.profile", "monai-flip-v9", "flip.governance.profile"),
        ("flip.governance.allowed_outputs", [], "allowed_outputs"),
        ("flip.governance.allowed_outputs", ["raw_data"], "allowed_outputs"),
        ("flip.governance.allowed_outputs", ["model"], "'metrics' is not in governance.allowed_outputs"),
        ("flip.query_ref", ..., "flip.query_ref"),
    ],
)
def test_invalid_overlays_rejected_with_field_path(path, value, fragment):
    with pytest.raises(OverlayError) as excinfo:
        parse_overlay(_with(path, value), source="test.yaml")
    message = str(excinfo.value)
    assert message.startswith("test.yaml: invalid monai-flip overlay")
    assert fragment in message


def test_placeholder_is_refused_with_its_own_message():
    with pytest.raises(OverlayError, match="skeleton placeholder"):
        parse_overlay(_with("flip.query_ref", "<FILL_ME: path to the cohort .sql>"))


def test_every_error_is_reported_at_once():
    data = _with("flip.query_ref", "query.txt")
    data["flip"]["imaging"]["resource"] = "ALL"
    with pytest.raises(OverlayError) as excinfo:
        parse_overlay(data)
    assert "flip.query_ref" in str(excinfo.value)
    assert "flip.imaging.resource" in str(excinfo.value)


@pytest.mark.parametrize("data", [None, [], "bundle: x"])
def test_non_mapping_rejected(data):
    with pytest.raises(OverlayError, match="must be a YAML mapping"):
        parse_overlay(data)


def test_load_overlay_bad_yaml(tmp_path: Path):
    path = tmp_path / "monai-flip.yaml"
    path.write_text("bundle: [unclosed")
    with pytest.raises(OverlayError, match="not valid YAML"):
        load_overlay(path)


def test_load_overlay_missing_file(tmp_path: Path):
    with pytest.raises(OverlayError, match="cannot read overlay"):
        load_overlay(tmp_path / "absent.yaml")


def test_load_overlay_uses_safe_loader(tmp_path: Path):
    """A YAML tag that constructs a Python object must not be honoured."""
    path = tmp_path / "monai-flip.yaml"
    path.write_text("!!python/object/apply:os.system ['true']\n")
    with pytest.raises(OverlayError, match="not valid YAML"):
        load_overlay(path)


def test_package_imports_without_torch_or_monai():
    """flip.monai must stay importable in flip-utils' base install (no torch, no MONAI)."""
    import importlib

    for mod in [m for m in sys.modules if m == "flip.monai" or m.startswith("flip.monai.")]:
        del sys.modules[mod]
    before = {m for m in sys.modules if m.split(".")[0] in {"torch", "monai"} or m.startswith("flip.export")}
    importlib.import_module("flip.monai")
    after = {m for m in sys.modules if m.split(".")[0] in {"torch", "monai"} or m.startswith("flip.export")}
    assert after == before
