# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""FL apps must not fetch anything from the internet at run time (FLIP#1206).

Every weight, checkpoint or auxiliary network an app needs arrives through the scanned
model-file upload path, so what a reviewer approved is byte-for-byte what runs on the hub's FL
server and on every trust's client. A ``pretrained=True``, ``torch.hub`` or ``from_pretrained``
call sidesteps that: the app is reviewed, the weights are fetched later from a URL that can serve
different bytes, and ``torch.load`` on a swapped checkpoint is code execution next to patient
data. It is also a functional failure — the FL server on a platform-managed estate and a trust
host behind an NHS firewall have no internet route at all (a ``pretrained=True`` ServerApp
hangs, the client dies with ``cannot sync with server Runner``).

This guard walks every shipped app — the tutorials' ``app_files/`` and ``app/`` trees and the
``fl-apps/`` templates — parses each module and fails on the *call sites* that download.
Working on the AST rather than the text means a docstring or comment that mentions
``pretrained=True`` (as the fixed apps do, to explain themselves) is not an offence. Dataset
tooling under ``fl-tutorials/datasets/`` is host-side and deliberately exempt: it is *supposed*
to download, into the gitignored ``fl-tutorials/data/``.

Apps that consume weights do so from a file beside their code, produced by that tutorial's
``make weights`` target; the last test keeps the two in step.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

FL_TUTORIALS = Path(__file__).resolve().parents[1]
REPO_ROOT = FL_TUTORIALS.parent
FL_APPS = REPO_ROOT / "fl-apps"

# Functions that download by design. Matched on the final attribute/name of the callee, so
# `torch.hub.load_state_dict_from_url(...)` and a bare imported `load_state_dict_from_url(...)`
# are both caught.
DOWNLOADING_CALLEES = {
    "load_state_dict_from_url",
    "download_url_to_file",
    "hf_hub_download",
    "snapshot_download",
}
# Perceptual-loss backbones MONAI/lpips resolve through torch.hub at first use. SqueezeNet is
# the one small enough to ship; the rest download.
HUB_BACKBONES = re.compile(r"^(alex|vgg|radimagenet_.*|medicalnet_.*|resnet50)$")


def _callee_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _dotted(node: ast.expr) -> str:
    """`torch.hub.load` for an Attribute chain, '' for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _is_true(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_hub_id(node: ast.expr) -> bool:
    """A string literal shaped like a Hugging Face repo id (`org/name`), i.e. not a local path."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "/" in node.value
        and not node.value.startswith(("/", "./", "../", "~"))
    )


def offences_in(source: str) -> list[tuple[int, str]]:
    """Every run-time download call site in ``source``, as (line, label)."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node)
        dotted = _dotted(node.func)
        keywords = {k.arg: k.value for k in node.keywords if k.arg}
        if "pretrained" in keywords and _is_true(keywords["pretrained"]):
            found.append((node.lineno, "pretrained=True"))
        weights = keywords.get("weights")
        if weights is not None and _dotted(weights).split(".")[0].endswith("_Weights"):
            found.append((node.lineno, "weights=<torchvision enum> (downloads)"))
        if name in DOWNLOADING_CALLEES:
            found.append((node.lineno, f"{name}("))
        if dotted == "torch.hub.load":
            found.append((node.lineno, "torch.hub.load("))
        if name == "from_pretrained" and node.args and _is_hub_id(node.args[0]):
            found.append((node.lineno, "from_pretrained(<hub id>)"))
        backbone = keywords.get("network_type")
        if isinstance(backbone, ast.Constant) and isinstance(backbone.value, str):
            if HUB_BACKBONES.match(backbone.value):
                found.append((node.lineno, f"network_type={backbone.value!r} is fetched via torch.hub"))
    return sorted(found)


def _app_dirs() -> list[Path]:
    """Every directory whose contents are uploaded or bundled as an FL app."""
    dirs: set[Path] = set()
    for backend in ("nvflare", "flower"):
        root = FL_TUTORIALS / backend
        dirs.update(p for p in root.rglob("app_files") if p.is_dir())
        dirs.update(p for p in root.rglob("app") if p.is_dir())
    for backend in ("nvflare", "flower"):
        root = FL_APPS / backend
        if root.is_dir():
            dirs.update(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    return sorted(d for d in dirs if ".venv" not in d.parts and "__pycache__" not in d.parts)


def _python_files(app_dir: Path) -> list[Path]:
    return sorted(p for p in app_dir.rglob("*.py") if ".venv" not in p.parts and "__pycache__" not in p.parts)


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def test_the_guard_sees_every_shipped_app():
    dirs = {_relative(d) for d in _app_dirs()}
    # The three apps that motivated FLIP#1206 must be in scope, or the guard guards nothing.
    for expected in (
        "fl-tutorials/nvflare/image_classification/xray_classification/app_files",
        "fl-tutorials/flower/xray_classification/app",
        "fl-tutorials/nvflare/image_synthesis/latent_diffusion_model/app_files",
        "fl-apps/nvflare/standard",
        "fl-apps/flower/standard",
    ):
        assert expected in dirs, f"{expected} not discovered as an app directory"


@pytest.mark.parametrize("app_dir", _app_dirs(), ids=lambda p: _relative(p))
def test_app_fetches_nothing_at_run_time(app_dir: Path):
    offences = [
        f"{_relative(py)}:{lineno}: {label}"
        for py in _python_files(app_dir)
        for lineno, label in offences_in(py.read_text(encoding="utf-8"))
    ]
    assert not offences, (
        "run-time download in a shipped app (FLIP#1206) — ship the weights through the upload path "
        "(see the tutorial's `make weights`):\n  " + "\n  ".join(offences)
    )


def test_guard_catches_the_call_shapes_it_claims_to():
    sample = "\n".join(
        [
            '"""A docstring saying pretrained=True and torch.hub.load( is not a download."""',
            "net = DenseNet121(spatial_dims=2, pretrained=True)",
            "m = torchvision.models.resnet50(weights=ResNet50_Weights.IMAGENET1K_V2)",
            "torch.hub.load('facebookresearch/dino', 'dino_vits16')",
            "sd = load_state_dict_from_url(URL)",
            "sd = torch.hub.load_state_dict_from_url(URL)",
            "tok = AutoTokenizer.from_pretrained('bert-base-uncased/')",
            "tok = AutoTokenizer.from_pretrained('org/model')",
            "path = hf_hub_download(repo_id='x/y', filename='w.safetensors')",
            "loss = PerceptualLoss(2, network_type='alex')",
            "loss = PerceptualLoss(spatial_dims=2, network_type='radimagenet_resnet50')",
            "# pretrained=True in a comment is fine",
            "net = DenseNet121(spatial_dims=2, pretrained=False)  # pretrained=True was here",
            "tok = AutoTokenizer.from_pretrained(local_dir)",
            "tok = AutoTokenizer.from_pretrained('./weights/tok')",
            "ok = PerceptualLoss(2, network_type='squeeze')",
        ]
    )
    assert [label for _, label in offences_in(sample)] == [
        "pretrained=True",
        "weights=<torchvision enum> (downloads)",
        "torch.hub.load(",
        "load_state_dict_from_url(",
        "load_state_dict_from_url(",
        "from_pretrained(<hub id>)",
        "from_pretrained(<hub id>)",
        "hf_hub_download(",
        "network_type='alex' is fetched via torch.hub",
        "network_type='radimagenet_resnet50' is fetched via torch.hub",
    ]


WEIGHTS_FILE_REFERENCE = re.compile(r"[\"']([A-Za-z0-9_.-]+\.(?:safetensors|pth|pt))[\"']")


@pytest.mark.parametrize(
    "app_dir",
    [d for d in _app_dirs() if d.is_relative_to(FL_TUTORIALS)],
    ids=lambda p: _relative(p),
)
def test_tutorial_that_loads_a_weights_file_has_a_weights_target(app_dir: Path):
    """An app loading weights from beside its code must have a `make weights` step producing them."""
    references: set[str] = set()
    for py in _python_files(app_dir):
        text = py.read_text(encoding="utf-8")
        if "WEIGHTS_FILE" in text or "HUB_CHECKPOINT" in text:
            references.update(WEIGHTS_FILE_REFERENCE.findall(text))
    if not references:
        pytest.skip("app loads no weights file")
    makefile = app_dir.parent / "Makefile"
    assert makefile.is_file(), f"{_relative(app_dir)} loads {sorted(references)} but its tutorial has no Makefile"
    text = makefile.read_text(encoding="utf-8")
    assert re.search(r"^weights:", text, re.M), (
        f"{_relative(makefile)} has no `weights` target, yet the app loads {sorted(references)}"
    )
    for ref in references:
        assert ref in text, f"{_relative(makefile)} `weights` does not produce {ref}"
