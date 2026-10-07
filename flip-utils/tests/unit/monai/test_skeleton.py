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

"""Unit tests for the monai-flip.yaml skeleton generator (FLIP#1105).

``fixtures/spleen_ct_segmentation/configs/metadata.json`` is the real Model Zoo file, unmodified, from
Project-MONAI/model-zoo ``models/spleen_ct_segmentation/configs/metadata.json`` at commit 55fc1da849.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from flip.monai.overlay import OverlayError, parse_overlay
from flip.monai.skeleton import SkeletonError, generate_skeleton, main

FIXTURE = Path(__file__).parent / "fixtures" / "spleen_ct_segmentation"


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """A spleen bundle tree: the real metadata.json plus empty train/evaluate configs."""
    root = tmp_path / "spleen_ct_segmentation"
    shutil.copytree(FIXTURE, root)
    (root / "configs" / "train.json").write_text("{}")
    (root / "configs" / "evaluate.json").write_text("{}")
    (root / "configs" / "inference.json").write_text("{}")
    return root


def _fill(skeleton: dict) -> dict:
    """Do what the researcher does: replace the placeholders."""
    skeleton["flip"]["query_ref"] = "query.sql"
    skeleton["flip"]["imaging"]["resource"] = "NIFTI"
    skeleton["flip"]["imaging"]["keys"] = {
        "image": {"pattern": "input_*.nii.gz"},
        "label": {"pattern": "label_*.nii.gz"},
    }
    return skeleton


def test_bundle_block_from_real_model_zoo_metadata(bundle: Path):
    data = yaml.safe_load(generate_skeleton(bundle, "monai-model-zoo"))
    assert data["bundle"] == {
        "source": "monai-model-zoo",
        # The identifier comes from the directory; metadata.json's "name" is a display title.
        "name": "spleen_ct_segmentation",
        "version": json.loads((FIXTURE / "configs" / "metadata.json").read_text())["version"],
        "configs": {
            "train": "configs/train.json",
            "evaluate": ["configs/train.json", "configs/evaluate.json"],
        },
    }
    assert data["bundle"]["version"] == "0.6.1"


def test_skeleton_is_refused_until_filled(bundle: Path):
    data = yaml.safe_load(generate_skeleton(bundle))
    with pytest.raises(OverlayError, match="skeleton placeholder"):
        parse_overlay(data)
    overlay = parse_overlay(_fill(data))
    assert overlay.bundle.source.value == "local"


def test_every_value_line_is_marked_locked_or_editable(bundle: Path):
    text = generate_skeleton(bundle)
    marked = {line.split(":")[0].strip() for line in text.splitlines() if "# LOCKED" in line or "# EDITABLE" in line}
    for key in ("source", "name", "version", "configs", "query_ref", "resource", "keys", "local_epochs", "profile"):
        assert key in marked, key
    for key in ("source", "name", "version", "configs", "profile"):
        line = next(ln for ln in text.splitlines() if ln.strip().startswith(f"{key}:"))
        assert "# LOCKED" in line, line


def test_yaml_configs_detected(bundle: Path):
    (bundle / "configs" / "train.json").rename(bundle / "configs" / "train.yaml")
    (bundle / "configs" / "evaluate.json").unlink()
    data = yaml.safe_load(generate_skeleton(bundle))
    assert data["bundle"]["configs"] == {"train": "configs/train.yaml"}


def test_ambiguous_train_config_refused(bundle: Path):
    (bundle / "configs" / "train.yaml").write_text("{}")
    with pytest.raises(SkeletonError, match="more than one train config"):
        generate_skeleton(bundle)


def test_inference_only_bundle_refused(bundle: Path):
    """What flip.export writes has no train config; the on-ramp cannot train it."""
    (bundle / "configs" / "train.json").unlink()
    with pytest.raises(SkeletonError, match="no train config"):
        generate_skeleton(bundle)


def test_missing_metadata_refused(bundle: Path):
    (bundle / "configs" / "metadata.json").unlink()
    with pytest.raises(SkeletonError, match="metadata.json not found"):
        generate_skeleton(bundle)


def test_metadata_without_version_refused(bundle: Path):
    (bundle / "configs" / "metadata.json").write_text(json.dumps({"name": "x"}))
    with pytest.raises(SkeletonError, match="no string 'version'"):
        generate_skeleton(bundle)


def test_not_a_bundle(tmp_path: Path):
    with pytest.raises(SkeletonError, match="no configs/ directory"):
        generate_skeleton(tmp_path)


def test_unknown_source_refused(bundle: Path):
    with pytest.raises(ValueError, match="github"):
        generate_skeleton(bundle, "github")


def test_cli_writes_file_and_refuses_overwrite(bundle: Path, tmp_path: Path, capsys):
    out = tmp_path / "monai-flip.yaml"
    assert main([str(bundle), "--source", "hub-mirror", "-o", str(out)]) == 0
    assert yaml.safe_load(out.read_text())["bundle"]["source"] == "hub-mirror"
    assert main([str(bundle), "-o", str(out)]) == 1
    assert "not overwriting" in capsys.readouterr().err


def test_cli_stdout_and_error(bundle: Path, tmp_path: Path, capsys):
    assert main([str(bundle)]) == 0
    assert "bundle:" in capsys.readouterr().out
    assert main([str(tmp_path)]) == 1
    assert "no configs/ directory" in capsys.readouterr().err
