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

"""Pin the exported spleen bundle's preprocessing to the training-side validation chain.

The MAP packaging path has already failed exactly here: ``export/inference.json`` resampled before
windowing and omitted ``CropForegroundd`` entirely, so a MAP built from the bundle fed the model a
field of view it never saw in training — a well-formed DICOM SEG out, Dice quietly down on five of
six MSD cases (fixed in e4981613; nothing has guarded the two copies of the chain since). These
tests are that guard: they fail whenever ``export/inference.json``'s preprocessing drifts from
``app_files/transforms.py::get_val_transforms``, in either direction.

A naive ordered equality of ``_target_`` + kwargs fails against *correct* code, because the bundle
is the deliberate image-only projection of the training chain — no label exists at inference. The
comparison therefore runs through an explicit normalisation that treats exactly three projection
asymmetries as equivalent, and nothing else:

* **keys** — the bundle drives only the image: its ``keys`` must normalise to ``["image"]``, while
  the training transform's keys must contain ``"image"`` (usually alongside ``"label"``).
* **per-key interpolation** — ``Spacingd.mode`` is per-key on the training side
  (``("bilinear", "nearest")``); the bundle's scalar is compared against the *image slot* only.
* **numbers** — compared as floats: ``b_min: 0`` (JSON) equals ``b_min=0.0`` (Python), and a JSON
  list, a Python tuple and MONAI's numpy ``pixdim`` all normalise to the same list of floats.

Everything else is strict. The ordered transform sequence is pinned by class name on both sides —
the e4981613 defect was an ordering defect, so a consistent-looking reorder must stop here too. Per
transform, the bundle may declare only the parameters :func:`_training_params` compares (a
bundle-side addition fails until the spec learns it), and a transform class new to both sides fails
in :func:`_training_params` until taught. The load-bearing values are additionally pinned to
literals on **both** sides, so retuning either copy — even both consistently — is forced through a
deliberate update of this file. Known gap, accepted: a *new* keyword added on the training side of
an already-known transform while the bundle keeps relying on the MONAI default is not detected —
that requires a table of MONAI defaults this test refuses to maintain.

The training side is the instantiated ``get_val_transforms()`` ``Compose``, introspected through
the attributes MONAI stores its init parameters on — the shipped chain, not a reconstruction. The
bundle side is read as plain JSON and never instantiated through ``monai.bundle``: the test pins
what the bundle *declares*, stays hermetic, and needs no bundle runtime.
"""

from __future__ import annotations

import json
import warnings
from typing import Any

import monai.transforms as mt
import numpy as np
from tutorial_apps import TUTORIALS_ROOT, TutorialApp

_INFERENCE_JSON = TUTORIALS_ROOT / "nvflare/image_segmentation/3d_spleen_segmentation/export/inference.json"

# Used purely as the file-path module loader. Deliberately NOT registered in DICOM_APPS: the spleen
# tutorial loads 3-D NIfTI through Orientationd/Spacingd, where the axis-order suite's correction
# would be actively wrong (tests/README.md, "Out of scope by design").
_SPLEEN_APP = TutorialApp(
    app_id="nvflare_3d_spleen_segmentation",
    backend="nvflare",
    module_path="nvflare/image_segmentation/3d_spleen_segmentation/app_files/transforms.py",
    factory="get_val_transforms",
)

# Both copies of the chain, in the order the checkpoint was trained against. Windowing and the
# foreground crop run on native voxels *before* Spacingd resamples — swapping that order is the
# exact e4981613 defect, so the sequence is pinned literally rather than merely cross-compared.
_CANONICAL_SEQUENCE = (
    "LoadImaged",
    "EnsureChannelFirstd",
    "Orientationd",
    "ScaleIntensityRanged",
    "CropForegroundd",
    "Spacingd",
    "EnsureTyped",
)

# The values the published tutorial checkpoint was trained against, asserted on BOTH sides so a
# consistent retune of the two copies still stops here and updates this table deliberately.
_PINNED_VALUES: dict[str, dict[str, Any]] = {
    "Orientationd": {"axcodes": "RAS"},
    "ScaleIntensityRanged": {"a_min": -57.0, "a_max": 250.0, "b_min": 0.0, "b_max": 1.0, "clip": True},
    "CropForegroundd": {"source_key": "image", "allow_smaller": True},
    "Spacingd": {"pixdim": [1.5, 1.5, 2.0], "mode": "bilinear"},
}


def _val_transforms() -> mt.Compose:
    """Return the shipped ``get_val_transforms()`` chain, instantiated."""
    module = _SPLEEN_APP.load_module()
    with warnings.catch_warnings():
        # monai 1.6 announces that Orientationd's `labels` default will change, and pytest.ini
        # escalates MONAI's own FutureWarnings to errors. That notice concerns runtime axis
        # labelling on meta-tensor spaces, not the declared-parameter parity pinned here — so
        # ignore exactly this notice, and only this one: any other MONAI deprecation surfacing in
        # this chain still fails the run, which is the suite's design.
        warnings.filterwarnings(
            "ignore", category=FutureWarning, message=r"monai\.transforms.*Orientationd\.__init__:labels"
        )
        return module.get_val_transforms()


def _bundle_preprocessing() -> list[dict[str, Any]]:
    """Return the bundle's declared preprocessing transforms, as plain JSON data."""
    config = json.loads(_INFERENCE_JSON.read_text(encoding="utf-8"))
    preprocessing = config["preprocessing"]
    assert preprocessing["_target_"] == "Compose", f"preprocessing is not a Compose: {preprocessing['_target_']}"
    return preprocessing["transforms"]


def _comparable(value: Any) -> Any:
    """Normalise one parameter value for cross-side comparison.

    Numbers become floats (``0`` == ``0.0``), sequences become lists of normalised elements (JSON
    list == Python tuple == numpy ``pixdim``), booleans stay booleans, and everything else is
    compared as its string form (which also flattens MONAI's str-valued enums). This is the whole
    numeric normalisation — no other equivalence is granted.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_comparable(element) for element in value]
    return str(value)


def _as_keys(value: Any) -> list[Any]:
    """Normalise a JSON ``keys`` declaration (scalar or list) to a list."""
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _training_params(transform: mt.MapTransform) -> dict[str, Any]:
    """Project a training transform's load-bearing parameters onto the image slot.

    The values are read off the attributes MONAI stores its init parameters on, so the comparison
    is against the shipped chain as instantiated. Every parameter named here is compared against
    the bundle, and the bundle may declare no others.

    Raises:
        AssertionError: On a transform class this spec has not been taught — a new transform added
            to both copies of the chain must extend this function before the parity check covers it.
    """
    if isinstance(transform, mt.Orientationd):
        return {"axcodes": transform.ornt_transform.axcodes}
    if isinstance(transform, mt.ScaleIntensityRanged):
        scaler = transform.scaler
        return {"a_min": scaler.a_min, "a_max": scaler.a_max, "b_min": scaler.b_min, "b_max": scaler.b_max,
                "clip": scaler.clip}
    if isinstance(transform, mt.CropForegroundd):
        return {"source_key": transform.source_key, "allow_smaller": transform.cropper.allow_smaller}
    if isinstance(transform, mt.Spacingd):
        # `mode` is per-key, aligned with `keys`; the bundle's scalar maps to the image slot.
        return {
            "pixdim": transform.spacing_transform.pixdim,
            "mode": transform.mode[transform.keys.index("image")],
        }
    if isinstance(transform, (mt.LoadImaged, mt.EnsureChannelFirstd, mt.EnsureTyped)):
        return {}
    raise AssertionError(
        f"{type(transform).__name__} is new to the spleen chain — teach _training_params which of its "
        "parameters are load-bearing (and mirror it into export/inference.json) before this can pass"
    )


def _bundle_params(entry: dict[str, Any]) -> dict[str, Any]:
    """Return one bundle transform's declared parameters, minus ``_target_`` and ``keys``.

    A per-key ``mode`` declared as a one-element list collapses to its scalar — with the bundle's
    keys pinned to ``["image"]``, that element *is* the image slot.
    """
    params = {name: value for name, value in entry.items() if name not in ("_target_", "keys")}
    if isinstance(params.get("mode"), list) and len(params["mode"]) == 1:
        params["mode"] = params["mode"][0]
    return params


def test_preprocessing_sequences_match() -> None:
    """Both copies of the chain must declare the canonical transform sequence, in order.

    Order is the parameter the e4981613 defect changed: the bundle resampled before windowing and
    dropped the foreground crop, and every transform it *did* declare carried the right values.
    """
    training = [type(transform).__name__ for transform in _val_transforms().transforms]
    bundle = [entry["_target_"] for entry in _bundle_preprocessing()]
    assert training == list(_CANONICAL_SEQUENCE), (
        f"get_val_transforms() is no longer the canonical spleen chain:\n  got      {training}\n"
        f"  expected {list(_CANONICAL_SEQUENCE)}\nif this is deliberate, update export/inference.json "
        "and this test's _CANONICAL_SEQUENCE in the same change"
    )
    assert bundle == list(_CANONICAL_SEQUENCE), (
        f"export/inference.json's preprocessing has drifted from get_val_transforms():\n"
        f"  bundle   {bundle}\n  expected {list(_CANONICAL_SEQUENCE)}\na MAP built from this bundle "
        "would feed the model inputs it never saw in training (the e4981613 failure mode)"
    )


def test_bundle_is_the_image_only_projection_of_val_transforms() -> None:
    """Per transform, the bundle must equal the training chain under the documented projection.

    The projection: bundle keys are exactly ``["image"]``, per-key interpolation is compared at the
    image slot, numbers compare as floats. Everything else is strict — including the set of
    parameter names the bundle declares, so a parameter added to the bundle alone fails here until
    :func:`_training_params` learns to compare it.
    """
    training_chain = list(_val_transforms().transforms)
    bundle_chain = _bundle_preprocessing()
    # Guarded pairing: the sequence test owns naming the drift, this keeps zip() honest.
    assert [type(t).__name__ for t in training_chain] == [e["_target_"] for e in bundle_chain]

    for position, (transform, entry) in enumerate(zip(training_chain, bundle_chain, strict=True)):
        where = f"preprocessing[{position}] ({entry['_target_']})"
        assert _as_keys(entry.get("keys")) == ["image"], (
            f"{where}: the bundle chain is the image-only projection, but keys is {entry.get('keys')!r}"
        )
        assert "image" in transform.keys, (
            f"{where}: get_val_transforms() no longer drives 'image' here (keys={transform.keys!r}), "
            "so the bundle's image-only projection has nothing to project from"
        )

        expected = _training_params(transform)
        declared = _bundle_params(entry)
        assert set(declared) == set(expected), (
            f"{where}: declared parameters {sorted(declared)} != compared parameters {sorted(expected)} — "
            "either the bundle drifted from get_val_transforms(), or _training_params must be taught the "
            "new parameter so it is compared rather than ignored"
        )
        for name, value in expected.items():
            assert _comparable(declared[name]) == _comparable(value), (
                f"{where}: {name} is {declared[name]!r} in inference.json but {value!r} in "
                "get_val_transforms() — the bundle must match the chain the checkpoint was trained with"
            )


def test_load_bearing_values_are_pinned_on_both_sides() -> None:
    """The values the checkpoint was trained against are pinned literally, on each side.

    The cross-comparison above cannot see a *consistent* retune of both copies; this table makes
    any change to pixdim, the CT window, RAS or the crop a deliberate three-way edit (both chain
    copies and this pin), with the training-provenance question in plain sight.
    """
    sides = {
        "app_files/transforms.py": {type(t).__name__: _training_params(t) for t in _val_transforms().transforms},
        "export/inference.json": {e["_target_"]: _bundle_params(e) for e in _bundle_preprocessing()},
    }
    for side_name, side in sides.items():
        for class_name, params in _PINNED_VALUES.items():
            assert class_name in side, f"{side_name} no longer declares a {class_name}"
            for name, value in params.items():
                actual = side[class_name].get(name)
                assert _comparable(actual) == _comparable(value), (
                    f"{side_name}: {class_name}.{name} is {actual!r}, pinned at {value!r} — the published "
                    "spleen checkpoint was trained against the pinned value; retuning it must update both "
                    "chain copies and this pin together"
                )
