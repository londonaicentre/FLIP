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
"""CPU-only tests for the IDC digital-pathology nuclei-detection tutorial.

Nothing here needs the 2.1 GB dataset, a GPU or network access: the DICOM-shaped inputs are
synthesised in-process and the geometry is checked against values computed by hand.

The properties pinned are the ones whose failure would be **silent** -- producing plausible numbers
rather than an error:

* Polygon centroids, because ``LongPrimitivePointIndexList`` is a 1-based index into a *flat*
  coordinate stream, so an off-by-one or a missing halving still yields centroids, just wrong ones.
* One-to-one matching, because naive nearest-neighbour matching converts false positives into true
  positives and inflates precision.
* Zero denominators, because reporting 0.0 for an undefined precision drags a federated average down
  as though a site had performed badly rather than not at all.
* Pooled aggregation, because averaging per-site F1 and calling it global weights a site with 200
  nuclei the same as one with 20,000.
* The returned payload's key set, because the privacy claim of the whole tutorial is that only
  aggregates leave a site.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

_APP_FILES = (
    Path(__file__).resolve().parents[1]
    / "nvflare"
    / "image_evaluation"
    / "idc_pathology_nuclei_detection_evaluation"
    / "app_files"
)


def _load(module_name: str):
    """Import one of the tutorial's flat app modules by path.

    App files are copied flat into a job rather than installed, so they are not importable by dotted
    name. Loading under a namespaced module name also keeps the several same-named ``data_utils``
    modules across tutorials from colliding in ``sys.modules``.
    """
    unique_name = f"idc_pathology_{module_name}"
    if unique_name in sys.modules:
        return sys.modules[unique_name]
    spec = importlib.util.spec_from_file_location(unique_name, _APP_FILES / f"{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    # app_files/ must be importable while executing, because these modules import each other flat.
    sys.path.insert(0, str(_APP_FILES))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(_APP_FILES))
    return module


matching = _load("matching")
metrics_utils = _load("metrics_utils")
annotations = _load("annotations")
detection = _load("detection")


# --------------------------------------------------------------------------------------------
# Annotation decoding
# --------------------------------------------------------------------------------------------


def test_polygon_centroid_of_a_square_is_its_centre():
    coordinates = np.array([[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0]])
    centroids = annotations._polygon_centroids(coordinates, np.array([0]))
    np.testing.assert_allclose(centroids, [[2.0, 2.0]])


def test_polygon_centroids_split_on_the_index_list():
    # Two triangles concatenated; the second starts at vertex 3.
    coordinates = np.array([[0.0, 0.0], [2.0, 0.0], [1.0, 3.0], [10.0, 10.0], [12.0, 10.0], [11.0, 13.0]])
    centroids = annotations._polygon_centroids(coordinates, np.array([0, 3]))
    np.testing.assert_allclose(centroids, [[1.0, 1.0], [11.0, 11.0]])


def test_polygon_centroid_handles_an_irregular_polygon():
    # Deliberately non-convex and unevenly sampled: the centroid is the vertex mean, not the
    # area centroid, and the test states which of the two the reader should expect.
    coordinates = np.array([[0.0, 0.0], [6.0, 0.0], [6.0, 2.0], [2.0, 2.0], [2.0, 6.0], [0.0, 6.0]])
    centroids = annotations._polygon_centroids(coordinates, np.array([0]))
    np.testing.assert_allclose(centroids, [[np.mean([0, 6, 6, 2, 2, 0]), np.mean([0, 0, 2, 2, 6, 6])]])


def test_no_polygons_yields_an_empty_centroid_array():
    centroids = annotations._polygon_centroids(np.empty((0, 2)), np.array([], dtype=int))
    assert centroids.shape == (0, 2)


# --------------------------------------------------------------------------------------------
# One-to-one matching -- the five cases that distinguish it from nearest-neighbour matching
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("predictions", "references", "expected"),
    [
        pytest.param([[10, 10]], [[11, 10]], (1, 0, 0), id="one_to_one_within_radius"),
        pytest.param([[10, 10]], [[80, 80]], (0, 1, 1), id="outside_radius_is_fp_and_fn"),
        pytest.param([[10, 10], [11, 11]], [[10, 10]], (1, 1, 0), id="two_predictions_one_nucleus"),
        pytest.param([[10, 10]], [[10, 10], [11, 11]], (1, 0, 1), id="one_prediction_two_nuclei"),
        pytest.param([], [[1, 1], [2, 2]], (0, 0, 2), id="no_predictions"),
        pytest.param([[1, 1]], [], (0, 1, 0), id="no_references"),
        pytest.param([], [], (0, 0, 0), id="neither"),
    ],
)
def test_match_points_is_one_to_one(predictions, references, expected):
    counts = matching.match_points(
        np.array(predictions, dtype=float).reshape(-1, 2),
        np.array(references, dtype=float).reshape(-1, 2),
        radius=5.0,
    )
    assert (counts.tp, counts.fp, counts.fn) == expected


def test_match_points_never_reuses_a_reference():
    # Ten predictions crowded onto one reference: exactly one can match, whatever the order.
    predictions = np.array([[10.0 + offset * 0.1, 10.0] for offset in range(10)])
    counts = matching.match_points(predictions, np.array([[10.0, 10.0]]), radius=5.0)
    assert counts.tp == 1
    assert counts.fp == 9


def test_match_points_rejects_a_non_positive_radius():
    with pytest.raises(ValueError, match="radius must be positive"):
        matching.match_points(np.array([[0.0, 0.0]]), np.array([[0.0, 0.0]]), radius=0.0)


# --------------------------------------------------------------------------------------------
# Metrics and federated aggregation
# --------------------------------------------------------------------------------------------


def test_metrics_from_known_counts():
    result = metrics_utils.detection_metrics(matching.MatchCounts(tp=8, fp=2, fn=4))
    assert result["precision"] == pytest.approx(0.8)
    assert result["recall"] == pytest.approx(8 / 12)
    assert result["f1"] == pytest.approx(2 * 0.8 * (8 / 12) / (0.8 + 8 / 12))


@pytest.mark.parametrize(
    ("counts", "undefined"),
    [
        pytest.param(matching.MatchCounts(0, 0, 3), "precision", id="no_predictions_undefines_precision"),
        pytest.param(matching.MatchCounts(0, 2, 0), "recall", id="no_references_undefines_recall"),
    ],
)
def test_zero_denominators_are_none_not_zero(counts, undefined):
    result = metrics_utils.detection_metrics(counts)
    assert result[undefined] is None, "an undefined metric must not be reported as 0.0"
    assert result["f1"] is None


def test_federated_metrics_come_from_pooled_counts_not_averaged_f1():
    per_site = {
        "Trust_1": matching.MatchCounts(tp=9000, fp=1000, fn=1000),
        "Trust_2": matching.MatchCounts(tp=20, fp=80, fn=80),
    }
    summary = metrics_utils.federated_summary(per_site)

    pooled = metrics_utils.detection_metrics(matching.MatchCounts(tp=9020, fp=1080, fn=1080))
    assert summary["pooled"]["f1"] == pytest.approx(pooled["f1"])

    # The macro mean must not be mistaken for the pooled figure: the large site dominates the
    # pooled score, so they differ substantially and are reported under separate names.
    macro = (summary["sites"]["Trust_1"]["f1"] + summary["sites"]["Trust_2"]["f1"]) / 2
    assert summary["macro_site_f1"] == pytest.approx(macro)
    assert summary["pooled"]["f1"] != pytest.approx(summary["macro_site_f1"])
    assert "global_auroc" not in summary


def test_federated_summary_reports_the_cross_site_gap():
    per_site = {
        "Trust_1": matching.MatchCounts(tp=80, fp=20, fn=20),
        "Trust_2": matching.MatchCounts(tp=40, fp=60, fn=60),
    }
    summary = metrics_utils.federated_summary(per_site)
    assert summary["best_site"] == "Trust_1"
    assert summary["worst_site"] == "Trust_2"
    assert summary["site_f1_gap"] == pytest.approx(summary["best_site_f1"] - summary["worst_site_f1"])


# --------------------------------------------------------------------------------------------
# Detector behaviour
# --------------------------------------------------------------------------------------------


def _synthetic_he_tile(centres, size=192, radius=7):
    """An H&E-like tile: pale eosin-pink background with dark haematoxylin blobs at *centres*.

    The background is stained rather than white on purpose. Real H&E has eosinophilic cytoplasm
    between nuclei, and a white background would be classified as glass by the detector's own tissue
    filter, so the tile would be skipped before detection ever ran.
    """
    tile = np.full((size, size, 3), 0, dtype=np.uint8)
    tile[..., :] = (215, 160, 185)
    rows, columns = np.ogrid[:size, :size]
    for x, y in centres:
        tile[(rows - y) ** 2 + (columns - x) ** 2 < radius**2] = (95, 65, 145)
    return tile


def test_detector_finds_well_separated_nuclei():
    centres = [(40, 40), (40, 140), (140, 40), (140, 140)]
    detector = detection.HaematoxylinPeakDetector()
    predictions = detector.predict(_synthetic_he_tile(centres), micrometres_per_pixel=0.25)

    assert len(predictions) == len(centres)
    # Every detection lands on a planted nucleus, within one nucleus radius.
    for x, y in predictions:
        assert min(np.hypot(x - cx, y - cy) for cx, cy in centres) < 8.0


def test_detector_skips_a_blank_tile():
    blank = np.full((192, 192, 3), 245, dtype=np.uint8)
    predictions = detection.HaematoxylinPeakDetector().predict(blank, micrometres_per_pixel=0.25)
    assert len(predictions) == 0


def test_tissue_fraction_separates_tissue_from_glass():
    assert detection.tissue_fraction(np.full((32, 32, 3), 245, dtype=np.uint8)) == 0.0
    assert detection.tissue_fraction(_synthetic_he_tile([(16, 16)], size=32, radius=14)) > 0.5


def test_detector_parameters_are_physical_so_magnification_does_not_change_the_answer():
    """A parameter in pixels would silently mean different things at sites with different scanners.

    The tutorial's own two sites differ (0.2325 vs 0.2470 um/px), so this is the arithmetic that
    would otherwise manufacture a site effect. Doubling the physical scale must halve the pixel
    separation the detector enforces.
    """
    detector = detection.HaematoxylinPeakDetector(detection.DetectorParameters(nucleus_diameter_um=8.0))
    fine = _synthetic_he_tile([(40, 40), (40, 140), (140, 40), (140, 140)])
    assert len(detector.predict(fine, micrometres_per_pixel=0.25)) == 4
    assert len(detector.predict(fine, micrometres_per_pixel=0.50)) == 4


def test_detector_rejects_a_non_rgb_tile():
    with pytest.raises(ValueError, match="RGB tile"):
        detection.HaematoxylinPeakDetector().predict(np.zeros((32, 32), dtype=np.uint8), 0.25)


# --------------------------------------------------------------------------------------------
# Privacy boundary
# --------------------------------------------------------------------------------------------


def test_returned_payload_carries_no_per_nucleus_or_per_patient_rows():
    """The evaluator's result keys are the privacy contract, so they are pinned explicitly."""
    source = (_APP_FILES / "evaluator.py").read_text()
    forbidden = (
        "sample_id",
        "filename",
        "prediction_list",
        "score_list",
        "centroids",
        "per_patient_rows",
    )
    results_block = source.split("results: dict[str, float] = {", 1)[1].split("}", 1)[0]
    for key in forbidden:
        assert key not in results_block, f"{key!r} must never be returned to the FL server"

    # The per-patient contribution leaves only as a distribution, never as rows.
    assert "patient_f1_median" in source
    assert "n_patients_scored" in source
