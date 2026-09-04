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
"""Render what the detector saw: slide tiles with reference nuclei and detections overlaid.

Produces the picture the metrics summarise -- every scored tile with its reference nuclei drawn as
true polygon outlines, each detection marked, and both coloured by outcome. Optionally strings the
frames into an mp4.

This runs **entirely locally, on one site's data**, and is a debugging and explanation aid rather
than part of the federated job. It is deliberately not something the FL client can do: rendering
per-nucleus overlays centrally would mean shipping pixels and per-nucleus coordinates off the site,
which is exactly what the tutorial exists to avoid.

Tiles are chosen with the same seed and tissue threshold the evaluator uses, so the frames are the
tiles that produced the reported numbers rather than a flattering subset.

Usage::

    python process_tools/render_overlays.py --accession TCGA-A8-A0AB --tiles 12 --video
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Polygon  # noqa: E402

_APP_FILES_DIR = Path(__file__).resolve().parents[1] / "app_files"
sys.path.insert(0, str(_APP_FILES_DIR))

from annotations import load_reference_nuclei  # noqa: E402
from detection import DetectorParameters, HaematoxylinPeakDetector  # noqa: E402
from dicom_wsi import SlideReader  # noqa: E402
from matching import assign_points  # noqa: E402
from models import PARAMETER_NAMES, load_config  # noqa: E402
from tiling import iter_tissue_tiles, references_within_tile  # noqa: E402

logger = logging.getLogger("render_overlays")

# Outcome colours, kept distinguishable in greyscale by pairing them with different marker shapes.
COLOUR_MATCHED = "#00b45a"  # detection paired with a reference
COLOUR_MISSED = "#ffa600"  # reference with no detection (false negative)
COLOUR_SPURIOUS = "#e5006d"  # detection with no reference (false positive)


def _detector_and_radius() -> tuple[HaematoxylinPeakDetector, float]:
    """Build the detector exactly as ``config.json`` specifies it, with the matching radius."""
    ((_name, model_config),) = load_config()["models"].items()
    values = {name: float(model_config["parameters"][name]) for name in PARAMETER_NAMES}
    parameters = DetectorParameters(
        nucleus_diameter_um=values["nucleus_diameter_um"],
        smoothing_sigma_um=values["smoothing_sigma_um"],
        threshold_sigma=values["threshold_sigma"],
        background_disk_um=values["background_disk_um"],
        min_tissue_fraction=values["min_tissue_fraction"],
    )
    return HaematoxylinPeakDetector(parameters), values["matching_radius_um"]


def render_tile(tile, reference, predictions, pairs, accession: str, index: int, destination: Path) -> None:
    """Draw one tile with its reference outlines and detections, coloured by outcome."""
    matched_predictions = {p for p, _ in pairs}
    matched_references = {r for _, r in pairs}

    figure, axes = plt.subplots(figsize=(6, 6), dpi=170)
    height, width = tile.pixels.shape[:2]
    axes.imshow(tile.pixels)
    axes.set_xticks([])
    axes.set_yticks([])
    # Polygons are selected by centroid, so one near an edge legitimately extends past it. Clamp the
    # view to the tile so the frame shows the scored area rather than sprawling outlines.
    axes.set_xlim(0, width)
    axes.set_ylim(height, 0)

    polygons = reference.polygons_within(tile.x, tile.y, width, height)
    for order, outline in enumerate(polygons):
        found = order in matched_references
        axes.add_patch(
            Polygon(
                outline,
                closed=True,
                fill=False,
                edgecolor=COLOUR_MATCHED if found else COLOUR_MISSED,
                linewidth=1.1 if found else 1.6,
                linestyle="-" if found else "--",
            )
        )

    for order, (x, y) in enumerate(predictions):
        if order in matched_predictions:
            axes.plot(x, y, "o", color=COLOUR_MATCHED, markersize=3.5, markeredgecolor="white", markeredgewidth=0.5)
        else:
            axes.plot(x, y, "x", color=COLOUR_SPURIOUS, markersize=6, markeredgewidth=1.8)

    tp = len(pairs)
    axes.set_title(
        f"{accession}  tile {index}  ({tile.x}, {tile.y})\n"
        f"TP {tp}   FP {len(predictions) - tp}   FN {len(polygons) - tp}",
        fontsize=10,
    )
    axes.legend(
        handles=[
            Line2D([], [], color=COLOUR_MATCHED, marker="o", linestyle="-", label="matched (TP)"),
            Line2D([], [], color=COLOUR_MISSED, linestyle="--", label="reference missed (FN)"),
            Line2D([], [], color=COLOUR_SPURIOUS, marker="x", linestyle="none", label="spurious (FP)"),
        ],
        loc="lower right",
        fontsize=7,
        framealpha=0.85,
    )
    figure.tight_layout()
    figure.savefig(destination, bbox_inches="tight")
    plt.close(figure)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--accession", required=True, help="Accession (TCGA barcode) to render.")
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=Path(__file__).resolve().parents[4] / "data" / "idc_pathology" / "Trust_1" / "accession-resources",
        help="Directory of accession folders holding slide.dcm and annotation.dcm.",
    )
    parser.add_argument("--tiles", type=int, default=12, help="How many scored tiles to render.")
    parser.add_argument("--out", type=Path, default=Path("results/overlays"))
    parser.add_argument("--video", action="store_true", help="Also assemble the frames into an mp4 with ffmpeg.")
    parser.add_argument("--fps", type=float, default=1.0, help="Frames per second for the assembled video.")
    args = parser.parse_args(argv)

    accession_dir = args.images_dir / args.accession
    if not (accession_dir / "slide.dcm").exists():
        raise SystemExit(
            f"No slide at {accession_dir}. Fetch the data with:\n"
            "  make -C fl-tutorials download-idc-pathology-data"
        )

    config = load_config()
    reader = SlideReader(accession_dir / "slide.dcm")
    reference = load_reference_nuclei(accession_dir / "annotation.dcm", keep_vertices=True)
    detector, matching_radius_um = _detector_and_radius()
    radius_px = matching_radius_um / reader.micrometres_per_pixel
    logger.info("%s: %s, %d reference nuclei (%s)", args.accession, reader, len(reference), reference.generation_type)

    args.out.mkdir(parents=True, exist_ok=True)
    frames = 0
    for tile in iter_tissue_tiles(
        reader,
        max_tiles=args.tiles,
        min_tissue_fraction=detector.parameters.min_tissue_fraction,
        seed=int(config["SEED"]),
    ):
        predictions = detector.predict(tile.pixels, reader.micrometres_per_pixel)
        references = references_within_tile(reference.centroids, tile)
        pairs = assign_points(predictions, references, radius_px)
        frames += 1
        render_tile(tile, reference, predictions, pairs, args.accession, frames, args.out / f"tile_{frames:03d}.png")
        logger.info("  tile %d at (%d, %d): %d prediction(s), %d reference(s), %d matched",
                    frames, tile.x, tile.y, len(predictions), len(references), len(pairs))

    logger.info("Wrote %d frame(s) to %s", frames, args.out)

    if args.video and frames:
        video = args.out / f"{args.accession}_nuclei.mp4"
        # yuv420p and the even-dimension scale filter keep the result playable in browsers and QuickTime.
        command = [
            "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(args.fps),
            "-i", str(args.out / "tile_%03d.png"),
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-pix_fmt", "yuv420p", str(video),
        ]
        subprocess.run(command, check=True)
        logger.info("Wrote %s", video)
    return 0


if __name__ == "__main__":
    sys.exit(main())
