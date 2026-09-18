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

Every weight, checkpoint or auxiliary network an app needs arrives through the scanned model-file
upload path, so the file a Trust can inspect before training is byte-for-byte the file that runs
— on the hub's FL server and on every trust's client. A ``pretrained=True``, ``torch.hub`` or
``from_pretrained`` call sidesteps that, and on a platform-managed estate (or behind an NHS
firewall) there is no internet route anyway, so the job hangs. The user guide's *Model Files*
section is the canonical statement of the rule.

This guard walks every shipped app — the tutorials' ``app_files/`` and ``app/`` trees and the
``fl-apps/`` templates, discovered through ``git ls-files`` so gitignored build output and venvs
can never be scanned — parses each module and fails on the common call shapes that download.
It is a lint for honest mistakes, not a sandbox: it resolves import aliases and module-level
constants, but not ``getattr``, ``**kwargs`` or values computed at run time. Working on the AST
means a docstring or comment that mentions ``pretrained=True`` (as the fixed apps do, to explain
themselves) is not an offence. Dataset tooling under ``fl-tutorials/datasets/`` is host-side and
deliberately exempt: it is *supposed* to download, into the gitignored ``fl-tutorials/data/``.

Two rules are positive rather than name-based, because the negative form kept missing spellings:
any ``weights=`` that names a torchvision weights enum or weight name downloads, whatever the
model builder; and constructing MONAI's ``PerceptualLoss`` (or lpips' ``LPIPS``) fetches its
backbone through torch.hub *whatever* ``network_type`` says — ``squeeze`` included — unless the
module first stages a hub dir (``torch.hub.set_dir``), which is what the LDM tutorial does.

The same walk holds shipped app code to weights-only checkpoint loading (FLIP#1245, #1249):
every ``torch.load`` passes ``weights_only=True`` explicitly. A load that unpickles arbitrary
objects is code execution beside patient data, and leaving the keyword off is only safe while
torch's default says so. Raw upstream checkpoints that need full unpickling are converted host-side
(the tutorials' ``process_tools/``), outside the app dirs this guard walks.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest
from tutorial_apps import TUTORIALS_ROOT

REPO_ROOT = TUTORIALS_ROOT.parent
APP_DIR_NAMES = {"app_files", "app"}

# Downloaders by canonical dotted name (after import-alias resolution) …
DOWNLOADERS = {
    "torch.hub.load",
    "torch.hub.load_state_dict_from_url",
    "torch.hub.download_url_to_file",
    "huggingface_hub.hf_hub_download",
    "huggingface_hub.snapshot_download",
    "monai.apps.download_url",
    "monai.apps.download_and_extract",
    "monai.apps.utils.download_url",
    "monai.apps.utils.download_and_extract",
    "monai.bundle.download",
    "monai.bundle.load",
    "urllib.request.urlretrieve",
}
# … and by final name wherever they come from, for the ones whose name is unambiguous.
DOWNLOADING_CALLEES = {
    "load_state_dict_from_url",
    "download_url_to_file",
    "hf_hub_download",
    "snapshot_download",
    "download_url",
    "download_and_extract",
    "urlretrieve",
}
HUB_BACKED_LOSSES = {"PerceptualLoss", "LPIPS"}
STAGES_HUB_DIR = "torch.hub.set_dir"
# torchvision weight names: `weights="DEFAULT"`, `weights="IMAGENET1K_V1"`, `weights="COCO_V1"`.
WEIGHT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
LOCAL_PATH_PREFIXES = ("/", "./", "../", "~")

_UNKNOWN = object()


def _dotted(node: ast.expr) -> str:
    """`torch.hub.load` for a Name/Attribute chain, the bare name for a Name, '' for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> canonical dotted name, for `import a.b as c` and `from a.b import c as d`."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:  # `import torch.hub` binds `torch`
                    aliases[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _bound_names(node: ast.AST) -> list[str]:
    """Names a statement binds: assignment targets (through tuples) and `global` declarations."""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    elif isinstance(node, ast.Global):
        return list(node.names)
    else:
        return []
    return [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]


def _module_constants(tree: ast.Module) -> dict[str, object]:
    """Module-level `NAME = <literal>` bindings, so `network_type=BACKBONE` can be checked.

    Only names bound exactly once anywhere in the module count: a later rebinding — in a branch,
    from the environment, or through `global` inside a function — would otherwise leave the first
    literal standing for a value the guard cannot see.
    """
    bindings: dict[str, int] = defaultdict(int)
    for node in ast.walk(tree):
        for name in _bound_names(node):
            bindings[name] += 1
    constants: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and bindings[target.id] == 1:
                    constants[target.id] = node.value.value
    return constants


def _canonical(func: ast.expr, aliases: dict[str, str]) -> str:
    dotted = _dotted(func)
    if not dotted:
        return ""
    head, _, rest = dotted.partition(".")
    if head in aliases:
        dotted = aliases[head] + (f".{rest}" if rest else "")
    return dotted


def _value(node: ast.expr | None, constants: dict[str, object]) -> object:
    """The literal a keyword carries, through one module-level constant; `_UNKNOWN` otherwise."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id, _UNKNOWN)
    return _UNKNOWN


def _is_torchvision_weights(node: ast.expr | None, constants: dict[str, object]) -> bool:
    if node is None:
        return False
    if any(part.endswith("_Weights") for part in _dotted(node).split(".")):
        return True
    value = _value(node, constants)
    return isinstance(value, str) and bool(WEIGHT_NAME.match(value))


def _is_hub_id(value: object) -> bool:
    """A string shaped like a Hugging Face repo id rather than a local path (write `./dir` for a local dir)."""
    return isinstance(value, str) and not value.startswith(LOCAL_PATH_PREFIXES)


def offences_in(source: str) -> list[tuple[int, str]]:
    """Every run-time download call site in ``source``, as (line, label)."""
    tree = ast.parse(source)
    aliases = _import_aliases(tree)
    constants = _module_constants(tree)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    stages_hub_dir = any(_canonical(call.func, aliases) == STAGES_HUB_DIR for call in calls)

    found: list[tuple[int, str]] = []
    for node in calls:
        canonical = _canonical(node.func, aliases)
        final = canonical.rsplit(".", 1)[-1] if canonical else getattr(node.func, "attr", "")
        keywords = {k.arg: k.value for k in node.keywords if k.arg}

        if _value(keywords.get("pretrained"), constants) is True:
            found.append((node.lineno, "pretrained=True"))
        for kwarg in ("weights", "weights_backbone"):
            if _is_torchvision_weights(keywords.get(kwarg), constants):
                found.append((node.lineno, f"{kwarg}=<torchvision weights> (downloads)"))
        if canonical in DOWNLOADERS or final in DOWNLOADING_CALLEES:
            found.append((node.lineno, f"{final}("))
        if final == "from_pretrained":
            target = node.args[0] if node.args else keywords.get("pretrained_model_name_or_path")
            if _is_hub_id(_value(target, constants)):
                found.append((node.lineno, "from_pretrained(<hub id>)"))
        if final in HUB_BACKED_LOSSES and not stages_hub_dir:
            found.append((node.lineno, f"{final}( fetches its backbone through torch.hub unless the app stages it"))
    return sorted(found)


# `torch.load` under both names it is reachable by; a module-level `NAME = torch.load` counts too.
UNPICKLING_LOADERS = {"torch.load", "torch.serialization.load"}


def unpickling_loads_in(source: str) -> list[tuple[int, str]]:
    """Every ``torch.load`` in ``source`` that does not pass ``weights_only=True``, as (line, label)."""
    tree = ast.parse(source)
    aliases = _import_aliases(tree)
    constants = _module_constants(tree)
    loader_names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign) and _canonical(node.value, aliases) in UNPICKLING_LOADERS
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        canonical = _canonical(node.func, aliases)
        if canonical not in UNPICKLING_LOADERS and canonical not in loader_names:
            continue
        loader = canonical if canonical in UNPICKLING_LOADERS else "torch.load"
        keywords = {k.arg: k.value for k in node.keywords if k.arg}
        if "weights_only" not in keywords:
            found.append((node.lineno, f"{loader}( without weights_only"))
        elif _value(keywords["weights_only"], constants) is not True:
            found.append((node.lineno, f"{loader}( with weights_only not provably True"))
    return sorted(found)


def _app_dir_of(path: Path) -> Path | None:
    """The shipped-app directory a tracked file belongs to, or None if it is not app code."""
    parts = path.relative_to(REPO_ROOT).parts
    if parts[0] == "fl-apps":  # fl-apps/<backend>/<template>/...
        return REPO_ROOT.joinpath(*parts[:3]) if len(parts) > 3 else None
    for depth, part in enumerate(parts):
        if part in APP_DIR_NAMES:
            return REPO_ROOT.joinpath(*parts[: depth + 1])
    return None


def _tracked(*subtrees: str) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *subtrees], cwd=REPO_ROOT, check=True, capture_output=True
    ).stdout
    return [REPO_ROOT / p for p in out.decode().split("\0") if p]


def _discover() -> dict[Path, list[Path]]:
    """Every shipped app dir -> its tracked Python files (so gitignored output is never scanned)."""
    files: dict[Path, list[Path]] = defaultdict(list)
    for path in _tracked("fl-tutorials/nvflare", "fl-tutorials/flower", "fl-apps"):
        if path.suffix == ".py" and (app_dir := _app_dir_of(path)) is not None:
            files[app_dir].append(path)
    return dict(sorted(files.items()))


APP_FILES = _discover()
APP_DIRS = list(APP_FILES)


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def test_the_guard_sees_every_shipped_app():
    dirs = {_relative(d) for d in APP_DIRS}
    # The apps that motivated FLIP#1206, plus a template per backend — or the guard guards nothing.
    for expected in (
        "fl-tutorials/nvflare/image_classification/xray_classification/app_files",
        "fl-tutorials/flower/xray_classification/app",
        "fl-tutorials/nvflare/image_synthesis/latent_diffusion_model/app_files",
        "fl-apps/nvflare/standard",
        "fl-apps/flower/standard",
    ):
        assert expected in dirs, f"{expected} not discovered as an app directory"


def test_every_tutorial_has_a_guarded_app_dir():
    """A tutorial whose app dir is named something new would otherwise be silently out of scope."""
    roots = [p.parent for p in _tracked("fl-tutorials/nvflare") if p.name == ".env.app"]
    roots += [
        p.parent
        for p in _tracked("fl-tutorials/flower")
        if p.name == "pyproject.toml" and p.parent.parent.name == "flower"
    ]
    assert roots, "no tutorial roots found — has the tutorial layout changed?"
    for root in roots:
        guarded = [d for d in APP_DIRS if d.is_relative_to(root) and APP_FILES[d]]
        assert guarded, f"{_relative(root)}: no app dir named {sorted(APP_DIR_NAMES)} with Python files under it"


@pytest.mark.parametrize("app_dir", APP_DIRS, ids=_relative)
def test_app_fetches_nothing_at_run_time(app_dir: Path):
    offences = [
        f"{_relative(py)}:{lineno}: {label}"
        for py in APP_FILES[app_dir]
        for lineno, label in offences_in(py.read_text(encoding="utf-8"))
    ]
    assert not offences, (
        "run-time download in a shipped app (FLIP#1206) — ship the weights through the upload path "
        "(see the LDM tutorial's `make weights`):\n  " + "\n  ".join(offences)
    )


@pytest.mark.parametrize("app_dir", APP_DIRS, ids=_relative)
def test_app_loads_checkpoints_weights_only(app_dir: Path):
    offences = [
        f"{_relative(py)}:{lineno}: {label}"
        for py in APP_FILES[app_dir]
        for lineno, label in unpickling_loads_in(py.read_text(encoding="utf-8"))
    ]
    assert not offences, (
        "checkpoint load that can unpickle arbitrary objects in a shipped app (FLIP#1245/#1249) — pass "
        "weights_only=True, and convert raw upstream checkpoints host-side (process_tools/):\n  "
        + "\n  ".join(offences)
    )


WEIGHTS = "weights=<torchvision weights> (downloads)"
HUB_LOSS = "PerceptualLoss( fetches its backbone through torch.hub unless the app stages it"
CAUGHT = [
    ("net = DenseNet121(spatial_dims=2, pretrained=True)", "pretrained=True"),
    ("PRETRAINED = True\nnet = DenseNet121(spatial_dims=2, pretrained=PRETRAINED)", "pretrained=True"),
    ("m = torchvision.models.resnet50(weights=ResNet50_Weights.IMAGENET1K_V2)", WEIGHTS),
    ("m = torchvision.models.resnet50(weights=torchvision.models.ResNet50_Weights.DEFAULT)", WEIGHTS),
    ("m = models.resnet50(weights='DEFAULT')", WEIGHTS),
    ("m = torchvision.models.get_model('resnet50', weights='IMAGENET1K_V1')", WEIGHTS),
    (
        "m = fasterrcnn_resnet50_fpn(weights=None, weights_backbone=ResNet50_Weights.DEFAULT)",
        "weights_backbone=<torchvision weights> (downloads)",
    ),
    ("torch.hub.load('facebookresearch/dino', 'dino_vits16')", "load("),
    ("from torch import hub\nhub.load('facebookresearch/dino', 'dino_vits16')", "load("),
    ("from torch.hub import load as hub_load\nhub_load('facebookresearch/dino', 'dino_vits16')", "load("),
    ("import torch.hub as th\nsd = th.load_state_dict_from_url(URL)", "load_state_dict_from_url("),
    ("sd = load_state_dict_from_url(URL)", "load_state_dict_from_url("),
    ("from huggingface_hub import hf_hub_download as dl\np = dl(repo_id='x/y', filename='w.pt')", "hf_hub_download("),
    ("from monai.bundle import load\nnet = load(name='spleen_ct_segmentation', bundle_dir='.')", "load("),
    ("monai.apps.download_url(URL, 'w.pt')", "download_url("),
    ("urllib.request.urlretrieve(URL, 'w.pt')", "urlretrieve("),
    ("tok = AutoTokenizer.from_pretrained('bert-base-uncased')", "from_pretrained(<hub id>)"),
    ("tok = AutoTokenizer.from_pretrained('org/model')", "from_pretrained(<hub id>)"),
    ("MODEL_ID = 'org/model'\ntok = AutoTokenizer.from_pretrained(MODEL_ID)", "from_pretrained(<hub id>)"),
    ("tok = AutoTokenizer.from_pretrained(pretrained_model_name_or_path='org/model')", "from_pretrained(<hub id>)"),
    ("loss = PerceptualLoss(2)", HUB_LOSS),
    ("loss = PerceptualLoss(2, 'alex')", HUB_LOSS),
    ("loss = PerceptualLoss(2, network_type='squeeze')", HUB_LOSS),
    ("BACKBONE = 'radimagenet_resnet50'\nloss = monai.losses.PerceptualLoss(3, network_type=BACKBONE)", HUB_LOSS),
    ("net = lpips.LPIPS(net='alex')", HUB_LOSS.replace("PerceptualLoss", "LPIPS")),
]

ACCEPTED = [
    '"""A docstring saying pretrained=True and torch.hub.load( is not a download."""',
    "# pretrained=True in a comment is fine",
    "net = DenseNet121(spatial_dims=2, pretrained=False)  # pretrained=True was here",
    "mean = np.average(values, weights=sample_weights)",
    "m = torchvision.models.resnet50(weights=None)",
    "tok = AutoTokenizer.from_pretrained('./weights/tok')",
    "tok = AutoTokenizer.from_pretrained(local_dir)",
    "torch.hub.set_dir(str(working_dir / 'torch_hub'))\nloss = PerceptualLoss(2, network_type='squeeze')",
    "import torch.hub as th\nth.set_dir(hub_dir)\nloss = PerceptualLoss(2, network_type='squeeze')",
    "state = torch.load('shipped.pt', weights_only=True)",
]


@pytest.mark.parametrize(("snippet", "label"), CAUGHT, ids=[c[0].splitlines()[-1][:60] for c in CAUGHT])
def test_guard_catches_the_call_shapes_it_claims_to(snippet: str, label: str):
    labels = [found for _, found in offences_in(snippet)]
    assert labels == [label], f"{snippet!r} → {labels}"


@pytest.mark.parametrize("snippet", ACCEPTED, ids=[s.splitlines()[-1][:60] for s in ACCEPTED])
def test_guard_accepts_the_offline_shapes(snippet: str):
    assert offences_in(snippet) == [], snippet


NOT_TRUE = "torch.load( with weights_only not provably True"
UNPICKLING = [
    ("ckpt = torch.load(path, map_location='cpu')", "torch.load( without weights_only"),
    ("import torch as T\nckpt = T.load(path)", "torch.load( without weights_only"),
    ("from torch import load as tload\nckpt = tload(path)", "torch.load( without weights_only"),
    ("ckpt = torch.load(path, **opts)", "torch.load( without weights_only"),
    (
        "import torch.serialization\nckpt = torch.serialization.load(path)",
        "torch.serialization.load( without weights_only",
    ),
    ("from torch.serialization import load\nckpt = load(path)", "torch.serialization.load( without weights_only"),
    ("loader = torch.load\nckpt = loader(path)", "torch.load( without weights_only"),
    ("ckpt = torch.load(path, weights_only=False)", NOT_TRUE),
    ("SAFE = False\nckpt = torch.load(path, weights_only=SAFE)", NOT_TRUE),
    ("ckpt = torch.load(path, weights_only=trusted)", NOT_TRUE),
    # A literal True the module later rebinds is not provably True either.
    ("SAFE = True\nSAFE = os.environ.get('X') is None\nckpt = torch.load(path, weights_only=SAFE)", NOT_TRUE),
    ("SAFE = True\nif os.environ.get('X'):\n    SAFE = False\nckpt = torch.load(path, weights_only=SAFE)", NOT_TRUE),
    ("SAFE = True\ndef relax():\n    global SAFE\n    SAFE = False\nckpt = torch.load(p, weights_only=SAFE)", NOT_TRUE),
]

WEIGHTS_ONLY = [
    "state = torch.load('shipped.pt', weights_only=True)",
    "SAFE = True\nstate = torch.load(p, map_location='cpu', weights_only=SAFE)",
    "from torch import load\nstate = load(p, weights_only=True)",
    "state = safetensors.torch.load_file(p)",
    "cfg = yaml.load(f, Loader=yaml.SafeLoader)",
    "arr = np.load(p)",
]


@pytest.mark.parametrize(("snippet", "label"), UNPICKLING, ids=[c[0].splitlines()[-1][:60] for c in UNPICKLING])
def test_guard_catches_the_unpickling_load_shapes(snippet: str, label: str):
    labels = [found for _, found in unpickling_loads_in(snippet)]
    assert labels == [label], f"{snippet!r} → {labels}"


@pytest.mark.parametrize("snippet", WEIGHTS_ONLY, ids=[s.splitlines()[-1][:60] for s in WEIGHTS_ONLY])
def test_guard_accepts_weights_only_loads(snippet: str):
    assert unpickling_loads_in(snippet) == [], snippet
