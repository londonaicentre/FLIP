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

"""Pin what the DICOM tutorial apps actually feed their models.

A preprocessing bug that fed a foundation model sideways radiographs survived three apps and two
attempted fixes, depressing every reported metric without ever erroring (FLIP#821). Nothing in the
repository could have caught it: the only committed verification artefact read pixels through
``pydicom`` directly, so the published dataset figure showed upright radiographs while the model
was being fed the transpose.

These tests close that gap from two directions:

* :func:`test_loader_prefix_matches_pixel_data` compares the chain *up to the first resampling
  transform* against ``pydicom``'s ``PixelData``, bit for bit.
* :func:`test_full_chain_orientation_is_identity` runs the whole chain and correlates the result
  against all eight dihedral variants of the reference, asserting ``identity`` wins. Correlation
  is invariant to the resize and normalisation the full chain applies, and names the defect
  ("looks like transpose") rather than only reporting that two arrays differ.

Neither assertion is on shape. ``Rotate90d(k=-1)`` restores the correct shape and is still wrong —
upright but mirrored, which swaps the patient's left and right and looks entirely correct.
"""

from __future__ import annotations

import monai.transforms as mt
import numpy as np
import pytest
from dicom_phantom import (
    assert_dihedrally_asymmetric,
    correlation,
    dihedral_variants,
    read_pixel_data,
)
from monai.data import PydicomReader
from tutorial_apps import DICOM_APPS, TUTORIALS_ROOT, find_load_transform, get_loader_prefix

# How far ahead of the runner-up `identity` must correlate. Comfortably clear of the ~0.02 spread
# resampling noise produces, comfortably below the ~0.5 gap a genuine dihedral confusion opens.
_CORRELATION_MARGIN = 0.05


def _to_2d(image: np.ndarray) -> np.ndarray:
    """Collapse a channel-first transform output to a single 2-D plane.

    The three channels of an ImageNet-normalised output are per-channel affine rescalings of one
    greyscale plane, so averaging them loses nothing about orientation.
    """
    array = np.asarray(image, dtype=np.float64)
    if array.ndim == 3:
        return array.mean(axis=0)
    return array


def _resample_to(image: np.ndarray, spatial_size: tuple[int, ...]) -> np.ndarray:
    """Resize a 2-D array onto ``spatial_size`` with the same resampler the apps use."""
    resized = mt.Resize(spatial_size=list(spatial_size), mode="area")(np.asarray(image)[None])
    return _to_2d(np.asarray(resized))


def _describe_orientation(actual: np.ndarray, reference: np.ndarray) -> str:
    """Return a message naming which dihedral variant of ``reference`` ``actual`` looks like."""
    lines = [f"output {actual.shape} does not match the reference {reference.shape} as loaded."]
    for name, variant in dihedral_variants(reference).items():
        if variant.shape != actual.shape:
            lines.append(f"  {name:<16} shape {variant.shape} (not comparable)")
            continue
        equal = np.array_equal(actual, variant)
        lines.append(f"  {name:<16} |r| = {correlation(actual, variant):.4f}{'  (exact match)' if equal else ''}")
    lines.append("An axis-order defect is not undone by a rotation or a flip; fix it at the reader.")
    return "\n".join(lines)


def test_phantom_has_no_dihedral_symmetry(phantom: np.ndarray) -> None:
    """The fixture must be distinguishable from all eight of its own dihedral variants.

    A mirror-symmetric phantom passes a ``Flipd`` unchanged and a monotone gradient correlates with
    its own transpose, so every other test here would pass on a broken chain.
    """
    assert_dihedrally_asymmetric(phantom)


def test_phantom_dicom_round_trips(dicom_path, phantom: np.ndarray) -> None:
    """Every encoding must decode back to the phantom, so the reference itself is above suspicion."""
    decoded = read_pixel_data(dicom_path)
    assert decoded.dtype == np.uint16
    assert decoded.shape == phantom.shape
    assert np.array_equal(decoded, phantom)


def test_loader_prefix_matches_pixel_data(dicom_app, dicom_path) -> None:
    """The chain's loader prefix must reproduce ``PixelData`` exactly, orientation included."""
    prefix = get_loader_prefix(dicom_app.transforms(is_validation=True))
    loaded = np.squeeze(np.asarray(prefix({"image": str(dicom_path)})["image"]))
    reference = read_pixel_data(dicom_path)

    assert loaded.shape == reference.shape, _describe_orientation(loaded, reference)
    assert np.array_equal(loaded, reference), _describe_orientation(loaded, reference)


@pytest.mark.parametrize("is_validation", [True, False])
def test_full_chain_orientation_is_identity(dicom_app, dicom_path, is_validation: bool) -> None:
    """The full chain's output must correlate best with the unrotated, unflipped reference.

    Both chains are pinned, not just the validation one. Models *train* on the training chain, and
    its ``if not is_validation:`` branch is exactly where a well-meaning "fix the orientation for
    training" lands — a ``Rotate90d`` appended there passes every validation-chain assertion while
    reproducing the original defect: trained sideways, validated upright, every metric silently
    depressed. The chain is seeded so the run is reproducible; the training-only ``RandAffined``
    jitter (a few degrees, ``prob=0.1``) is orders of magnitude inside the correlation margin.
    """
    if not is_validation and not dicom_app.has_training_chain:
        pytest.skip(f"{dicom_app.app_id} is inference-only and exposes no training chain")

    chain = dicom_app.transforms(is_validation=is_validation)
    chain.set_random_state(seed=0)
    output = _to_2d(np.asarray(chain({"image": str(dicom_path)})["image"]))
    reference = read_pixel_data(dicom_path)

    scores = {
        name: correlation(output, _resample_to(variant, output.shape))
        for name, variant in dihedral_variants(reference).items()
    }
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    report = "\n".join(f"  {name:<16} |r| = {score:.4f}" for name, score in ranked)

    best_name, best_score = ranked[0]
    runner_up_score = ranked[1][1]
    assert best_name == "identity", f"chain output looks like {best_name}, not the loaded image:\n{report}"
    assert best_score - runner_up_score >= _CORRELATION_MARGIN, (
        f"identity wins by only {best_score - runner_up_score:.4f}, too little to call:\n{report}"
    )


def test_registry_covers_every_dicom_load_site() -> None:
    """Every ``LoadImaged`` call site in the tutorial tree is registered or on the NIfTI path.

    ``DICOM_APPS`` is hand-maintained, and a seventh DICOM app that nobody registers would be
    silently uncovered — the drift mode this suite exists to end. This walk makes the registry's
    completeness a test: a file composing ``LoadImaged`` must either be a registered entry or read
    3-D NIfTI through ``Orientationd`` (the documented exclusion — those chains orient from image
    metadata, and the ``swap_ij`` correction would be actively wrong there). The scan is textual,
    so a false positive fails loudly and is resolved by registering the app or routing it through
    ``Orientationd``, never by weakening the suite.
    """
    registered = {app.path.resolve() for app in DICOM_APPS}
    unaccounted = []
    for path in sorted(TUTORIALS_ROOT.rglob("*.py")):
        # Skip this suite itself and anything non-tree-owned (per-tutorial .venv/ uv environments).
        if any(part.startswith(".") for part in path.parts) or TUTORIALS_ROOT / "tests" in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        if "LoadImaged(" not in text:
            continue
        if path.resolve() in registered or "Orientationd(" in text:
            continue
        unaccounted.append(str(path.relative_to(TUTORIALS_ROOT)))

    assert not unaccounted, (
        "these files compose LoadImaged but are neither registered in DICOM_APPS nor on the "
        f"Orientationd NIfTI path — a DICOM-loading app here is untested for orientation: {unaccounted}"
    )


def test_loader_pins_its_reader(dicom_app) -> None:
    """The chain must name its reader instead of inheriting whichever one is registered last.

    ``LoadImaged`` tries registered readers last-first, and only registers the ones whose backend
    imports. The transpose these tests pin is ``PydicomReader(swap_ij=True)``, a MONAI default that
    holds solely because ``itk`` is absent: installing ``itk`` promotes ``ITKReader`` and silently
    changes the axis order under an otherwise-green suite. An explicitly constructed reader is
    appended last, so it wins regardless of what else is installed.
    """
    loader = find_load_transform(dicom_app.transforms(is_validation=True))
    # ``_loader`` is MONAI-private (no public accessor exists for a LoadImaged's readers). Accepted
    # coupling for a config pin: a MONAI rename breaks this loudly with AttributeError on a healthy
    # tree, never silently — and the pin earns it, e.g. a typo'd ``swap_ji=`` kwarg is swallowed by
    # MONAI's own ``except TypeError`` reader probing and only this test notices.
    readers = loader._loader.readers
    assert readers, "LoadImaged registered no readers"

    pinned = readers[-1]
    assert isinstance(pinned, PydicomReader), (
        f"{dicom_app.app_id} leaves reader selection to whatever is installed — the last-registered "
        f"reader is {type(pinned).__name__}, not an explicitly requested PydicomReader; pass "
        "reader='PydicomReader' to LoadImaged"
    )
    assert pinned.swap_ij is False, (
        f"{dicom_app.app_id} loads with swap_ij=True, which transposes the image relative to "
        "PixelData; pass swap_ij=False to LoadImaged"
    )


@pytest.mark.parametrize("is_validation", [True, False])
def test_chain_composes(dicom_app, dicom_path, is_validation: bool) -> None:
    """Both chains a tutorial exposes must import, compose and run on a DICOM.

    The training chain is exercised too — its extra ``RandAffined`` is the one step whose output is
    not deterministic, so it is checked for shape and finiteness rather than for values. The
    evaluation apps expose only an inference chain and have nothing to run for the training case.
    """
    if not is_validation and not dicom_app.has_training_chain:
        pytest.skip(f"{dicom_app.app_id} is inference-only and exposes no training chain")

    output = np.asarray(dicom_app.transforms(is_validation=is_validation)({"image": str(dicom_path)})["image"])
    assert output.ndim == 3, f"expected channel-first 2-D output, got shape {output.shape}"
    assert output.shape[0] in (1, 3), f"unexpected channel count in {output.shape}"
    assert output.shape[1] == output.shape[2], f"expected a square target, got {output.shape}"
    assert np.isfinite(output).all()


def test_validation_chain_is_deterministic(dicom_app, dicom_path) -> None:
    """Two runs of the validation chain must agree, so the assertions above are reproducible."""
    first = np.asarray(dicom_app.transforms(is_validation=True)({"image": str(dicom_path)})["image"])
    second = np.asarray(dicom_app.transforms(is_validation=True)({"image": str(dicom_path)})["image"])
    assert np.array_equal(first, second)
