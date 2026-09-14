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
"""Client-API evaluator for the IDC digital-pathology nuclei-detection tutorial.

The canonical NVFLARE Client-API ``is_evaluate()`` loop: receive the broadcast detector
specification, score it against this site's own slides, and return **aggregate counts only**.

What leaves the site, and what does not, is the point of the tutorial:

* Returned: TP/FP/FN totals, slide and patient counts, and a summary of the per-patient spread.
* Never returned: pixels, polygons, per-nucleus coordinates, per-tile counts, per-patient rows, or
  anything else that identifies which patient contributed what. A per-patient list would expose the
  cohort composition; only its distribution leaves.

Metrics are *not* derived here. Each site returns counts and the server pools them, because pooled
TP/FP/FN weights each site by evidence whereas averaging per-site F1 weights a site with 200 nuclei
the same as one with 20,000. Both figures end up in the results, and the gap between them is the
cross-site heterogeneity being demonstrated.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import nvflare.client as flare
from data_utils import load_cohort, load_slide_case
from detection import DetectorParameters, HaematoxylinPeakDetector
from flip import FLIP
from matching import MatchCounts, match_points
from metrics_utils import detection_metrics
from models import PARAMETER_NAMES, load_config
from tiling import iter_tissue_tiles, references_within_tile

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project_id", type=str, default="")
    return parser.parse_args()


def load_query() -> str:
    """Read the cohort query from the client config's top-level ``query`` key.

    NVFLARE whitespace-splits ``task_script_args``, so a SQL query cannot ride there; the recipe
    writes it into ``config_fed_client.json`` instead. Ignored under LOCAL_DEV, where the cohort comes
    from the per-site CSV.
    """
    client_config = Path(__file__).parent.parent / "config" / "config_fed_client.json"
    if not client_config.exists():
        return ""
    try:
        return json.loads(client_config.read_text()).get("query", "")
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read the cohort query from %s", client_config)
        return ""


def parameters_from_broadcast(received: dict | None) -> tuple[DetectorParameters, float]:
    """Rebuild the detector parameters from the weights the server broadcast.

    Falls back to ``config.json`` only when the broadcast carries nothing usable, which happens in
    bare local runs outside a job. A partial broadcast is an error rather than a silent default: if
    sites silently ran different parameters, the cross-site comparison would be meaningless.

    Returns:
        The detector parameters, and the matching radius in micrometres (which parameterises the
        scoring rather than the detector, so it is returned separately).
    """
    if received:
        values = {
            name: float(np.asarray(received[name]).reshape(-1)[0])
            for name in PARAMETER_NAMES
            if name in received
        }
        missing = [name for name in PARAMETER_NAMES if name not in values]
        if not missing:
            return _to_parameters(values), values["matching_radius_um"]
        raise ValueError(
            f"The broadcast model is missing parameter(s) {missing}. Every site must evaluate the "
            "identical specification, so this is refused rather than defaulted."
        )

    logger.warning("No parameters were broadcast; falling back to config.json.")
    ((_name, model_config),) = load_config()["models"].items()
    values = {name: float(model_config["parameters"][name]) for name in PARAMETER_NAMES}
    return _to_parameters(values), values["matching_radius_um"]


def _to_parameters(values: dict[str, float]) -> DetectorParameters:
    """Build a :class:`DetectorParameters` from the broadcast values."""
    return DetectorParameters(
        nucleus_diameter_um=values["nucleus_diameter_um"],
        smoothing_sigma_um=values["smoothing_sigma_um"],
        threshold_sigma=values["threshold_sigma"],
        background_disk_um=values["background_disk_um"],
        min_tissue_fraction=values["min_tissue_fraction"],
    )


def evaluate_slide(case, detector, config: dict, matching_radius_um: float) -> tuple[MatchCounts, int]:
    """Score one slide, returning its counts and how many tiles were scored."""
    reader = case.reader
    radius_px = matching_radius_um / reader.micrometres_per_pixel
    counts = MatchCounts()
    tiles_scored = 0
    for tile in iter_tissue_tiles(
        reader,
        max_tiles=int(config["TILES_PER_SLIDE"]),
        min_tissue_fraction=detector.parameters.min_tissue_fraction,
        seed=int(config["SEED"]),
    ):
        predictions = detector.predict(tile.pixels, reader.micrometres_per_pixel)
        references = references_within_tile(case.reference_centroids, tile)
        counts = counts + match_points(predictions, references, radius_px)
        tiles_scored += 1
    return counts, tiles_scored


def _patient_spread(per_patient: dict[str, MatchCounts]) -> dict[str, float]:
    """Summarise how much F1 varies between patients at this site.

    Tiles from one patient are not independent observations, so a site-level interval computed over
    tiles would be overconfident by roughly the square root of the tiles-per-patient count. The
    per-patient spread is the honest uncertainty, and it is what says whether a between-site gap is
    bigger than the within-site variation. Only the distribution leaves the site, never the rows.
    """
    scores = [m["f1"] for m in (detection_metrics(c) for c in per_patient.values()) if m["f1"] is not None]
    if not scores:
        return {}
    return {
        "patient_f1_median": float(np.median(scores)),
        "patient_f1_min": float(np.min(scores)),
        "patient_f1_max": float(np.max(scores)),
        "patient_f1_iqr": float(np.subtract(*np.percentile(scores, [75, 25]))),
        "n_patients_scored": len(scores),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    config = load_config()
    query = load_query()

    flare.init()
    site_name = flare.get_site_name()
    flip = FLIP(config.get("job_type", "evaluation"))
    logger.info("Evaluator starting on %s", site_name)

    while flare.is_running():
        input_model = flare.receive()
        if not flare.is_evaluate():
            continue

        detector_parameters, matching_radius_um = parameters_from_broadcast(input_model.params)
        detector = HaematoxylinPeakDetector(detector_parameters)
        logger.info("%s: evaluating %s", site_name, detector_parameters)

        cohort = load_cohort(flip, args.project_id, query, site_name)
        site_counts = MatchCounts()
        per_patient: dict[str, MatchCounts] = {}
        tiles_scored = 0

        for row in cohort.itertuples(index=False):
            case = load_slide_case(flip, args.project_id, row.accession_id, row.patient_id, site_name)
            counts, tiles = evaluate_slide(case, detector, config, matching_radius_um)
            site_counts = site_counts + counts
            per_patient[case.patient_id] = per_patient.get(case.patient_id, MatchCounts()) + counts
            tiles_scored += tiles
            logger.info(
                "%s: %s tp=%d fp=%d fn=%d over %d tile(s)", site_name, case.accession_id, counts.tp,
                counts.fp, counts.fn, tiles,
            )

        metrics = detection_metrics(site_counts)
        results: dict[str, float] = {
            "tp": float(site_counts.tp),
            "fp": float(site_counts.fp),
            "fn": float(site_counts.fn),
            "n_predictions": float(site_counts.n_predictions),
            "n_references": float(site_counts.n_references),
            "n_slides": float(len(cohort)),
            "n_tiles": float(tiles_scored),
        }
        # Derived metrics are omitted when undefined rather than sent as 0.0, which would drag a
        # federated average down as though the site had performed badly rather than not at all.
        for key in ("precision", "recall", "f1"):
            if metrics[key] is not None:
                results[key] = float(metrics[key])
        results.update(_patient_spread(per_patient))

        logger.info("%s: returning aggregate results %s", site_name, results)
        flare.send(flare.FLModel(metrics=results))


if __name__ == "__main__":
    main()
