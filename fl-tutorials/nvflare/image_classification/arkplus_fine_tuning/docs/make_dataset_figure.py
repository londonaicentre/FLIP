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

"""Render the dataset figure embedded in this tutorial's README.

Draws one representative chest radiograph per target lesion, left to right in the classifier-head
index order given by ``app_files/config.json``.

Everything is validated before anything is drawn: the panels must cover each classifier head
exactly once, and each accession must carry exactly the lesion its panel claims and no other
finding in the split's cohort dataframe. A mislabelled figure therefore fails loudly rather than
shipping.

**This script reads pixels through ``pydicom`` directly, not through the app's transform chain.**
That is why it drew upright radiographs for months while the model was being fed the transpose of
them (FLIP#821) — it is a picture of the data, not of what the model sees, and it cannot become one
without pulling MONAI into the ``docs`` extra. The gap is closed at the other end instead:
``app_files/data_utils.py`` pins its loader to ``PydicomReader(swap_ij=False)``, which returns the
array exactly as ``PixelData`` stores it, and ``fl-tutorials/tests/`` asserts that equality on every
app on this path. Treat that suite, not this figure, as the evidence.

    uv run --extra docs python docs/make_dataset_figure.py --data-root /path/to/dataset
    make dataset-figure DATA_ROOT=/path/to/dataset
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pydicom  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = APP_DIR / "app_files" / "config.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "dataset_examples.png"

ACCESSION_DIRNAME = "accession-resources"
DATAFRAME_NAME = "sample_get_dataframe_response.csv"
ACCESSION_COLUMN = "accession_id"
POSITIVE = "Yes"

TRAIN_SPLIT = "re_final_site2"
HOLDOUT_SPLIT = "re_final_site2_holdoff"

# Panel geometry, in inches. The radiographs are square, so sizing the axes square too leaves no
# leftover width inside a panel and PANEL_GAP (a fraction of panel width) is the only spacing.
PANEL_INCHES = 2.4
TITLE_INCHES = 0.34
MARGIN_INCHES = 0.14
PANEL_GAP = 0.05


@dataclass(frozen=True)
class Example:
    """One panel: a lesion, and the accession chosen to illustrate it."""

    index: int
    lesion: str
    split: str
    accession_id: str


# Ordered by classifier-head index. `lesion` must match config.json LESIONS and the dataframe
# column of the same name; both are checked before rendering.
EXAMPLES: tuple[Example, ...] = (
    Example(
        index=0,
        lesion="Effusion",
        split=TRAIN_SPLIT,
        accession_id="final_3_method2_pleural_effusion_left_lfocused_pleural_effusion_effusion_608_left_001",
    ),
    Example(
        index=1,
        lesion="Consolidation",
        split=TRAIN_SPLIT,
        accession_id="final_2_method2_consolidation_right_lfocused_consolidation_consolidation_605_right_043",
    ),
    Example(
        index=2,
        lesion="Infiltration",
        split=TRAIN_SPLIT,
        accession_id="final_1_method2_infiltration_bilateral_lfocused_infiltration_infilltration_609_bilateral_034",
    ),
    Example(
        index=3,
        lesion="Lung Nodule or Mass",
        split=TRAIN_SPLIT,
        accession_id="final_1_method2_lung_nodule_or_mass_bilateral_lfocused_lung_nodule_or_mass_mass_304_bilateral_022",
    ),
    # Only present in the holdout split; the README records this.
    Example(
        index=4,
        lesion="Pneumothorax",
        split=HOLDOUT_SPLIT,
        accession_id="final_4_method2_pneumothorax_right_lfocused_pneumothorax_pneumothorax_410_right_026",
    ),
)


class FigureError(RuntimeError):
    """A resolved path, dataframe row, or label did not hold up."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def load_lesions() -> tuple[dict[int, str], str]:
    """Return config.json's classifier-head lesions keyed by head index, plus the negative override.

    LESIONS reserves index -1 for the negative override, which is not a head output.
    """
    lesions = json.loads(CONFIG_PATH.read_text())["LESIONS"]
    targets = {int(key): name for key, name in lesions.items() if int(key) >= 0}
    return targets, lesions["-1"]


# ---------------------------------------------------------------------------
# Validation against the cohort dataframe
# ---------------------------------------------------------------------------
def read_label_row(dataframe_path: Path, accession_id: str) -> dict[str, str]:
    """Return the cohort-dataframe row for ``accession_id``."""
    if not dataframe_path.is_file():
        raise FigureError(f"Cohort dataframe not found: {dataframe_path}")
    with dataframe_path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get(ACCESSION_COLUMN) == accession_id:
                return row
    raise FigureError(f"Accession {accession_id!r} has no row in {dataframe_path}")


def validate_panel_set(examples: Sequence[Example], targets: dict[int, str]) -> None:
    """Fail unless the panels cover every classifier head exactly once.

    ``validate_example`` only vets each panel in isolation, so without this a dropped or duplicated
    entry would quietly render a figure with the wrong number of lesions.
    """
    indices = sorted(example.index for example in examples)
    if indices != sorted(targets):
        raise FigureError(
            f"EXAMPLES must cover each classifier-head index exactly once; got {indices} for heads {sorted(targets)}."
        )


def validate_example(example: Example, data_root: Path, targets: dict[int, str], normal: str) -> None:
    """Fail unless the example is exactly what its panel will claim it is.

    Guards two independent drifts: an index/name pair that no longer matches config.json, and an
    accession whose cohort labels no longer single out the lesion being illustrated.
    """
    expected = targets.get(example.index)
    if expected != example.lesion:
        raise FigureError(
            f"Panel {example.index} claims {example.lesion!r}, but config.json head index "
            f"{example.index} is {expected!r}."
        )

    dataframe_path = data_root / example.split / DATAFRAME_NAME
    row = read_label_row(dataframe_path, example.accession_id)

    # An absent column reads as negative, which would quietly weaken the single-positive check below.
    absent = [name for name in (*targets.values(), normal) if name not in row]
    if absent:
        raise FigureError(f"{dataframe_path} is missing label column(s): {absent}")

    # Scan every label column, not just the configured lesions: the dataframe also carries findings
    # outside config.json's LESIONS (e.g. Edema), and a co-positive one would make this a poor
    # single-lesion example even though the head-facing labels look clean.
    positives = {name for name, value in row.items() if name != ACCESSION_COLUMN and value == POSITIVE}
    if positives != {example.lesion}:
        raise FigureError(
            f"{example.accession_id} should be positive for {example.lesion!r} alone, "
            f"but its positive labels are {sorted(positives) or ['none']}."
        )


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------
def find_dicom(data_root: Path, example: Example) -> Path:
    """Locate the single DICOM under the example's accession folder."""
    accession_dir = data_root / example.split / ACCESSION_DIRNAME / example.accession_id
    dicoms = sorted(accession_dir.rglob("*.dcm"))
    if not dicoms:
        raise FigureError(f"No DICOM under {accession_dir}")
    if len(dicoms) > 1:
        raise FigureError(f"Expected one DICOM under {accession_dir}, found {len(dicoms)}: {[p.name for p in dicoms]}")
    return dicoms[0]


def load_grayscale(dicom_path: Path) -> np.ndarray:
    """Read a DICOM as a single grey plane scaled to [0, 1] by its own min/max.

    These radiographs are stored as 8-bit RGB whose channels agree to within a few grey levels
    (compression noise), so the channel axis is averaged away rather than rendered as colour.
    """
    dataset = pydicom.dcmread(str(dicom_path))
    photometric = str(getattr(dataset, "PhotometricInterpretation", ""))
    pixels = dataset.pixel_array.astype(np.float32)

    if pixels.ndim == 3:
        # Averaging the third axis is only a valid grey collapse when it holds R, G and B. A
        # YBR-encoded export (what a JPEG re-compression yields) would average luma with chroma and
        # silently produce a washed-out image, and a multi-frame file would average across width.
        if photometric != "RGB":
            raise FigureError(f"Cannot average a {photometric!r} channel axis into grey: {dicom_path}")
        pixels = pixels.mean(axis=-1)
    if pixels.ndim != 2:
        raise FigureError(f"Expected a single-frame radiograph, got shape {pixels.shape}: {dicom_path}")

    if photometric == "MONOCHROME1":
        pixels = pixels.max() - pixels

    low, high = float(pixels.min()), float(pixels.max())
    if high <= low:
        raise FigureError(f"Radiograph has no intensity range (all pixels {low}): {dicom_path}")
    return (pixels - low) / (high - low)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render(images: Sequence[np.ndarray], examples: Sequence[Example], output: Path, dpi: int) -> None:
    """Draw the 1xN panel row and write it to ``output``."""
    panels = len(images)
    width = 2 * MARGIN_INCHES + PANEL_INCHES * (panels + PANEL_GAP * (panels - 1))
    height = MARGIN_INCHES + PANEL_INCHES + TITLE_INCHES

    figure, axes = plt.subplots(1, panels, figsize=(width, height))
    for axis, image, example in zip(np.atleast_1d(axes), images, examples, strict=True):
        axis.imshow(image, cmap="gray", vmin=0.0, vmax=1.0)
        axis.set_title(f"({example.index}) {example.lesion}", fontsize=11)
        axis.axis("off")

    figure.subplots_adjust(
        left=MARGIN_INCHES / width,
        right=1 - MARGIN_INCHES / width,
        bottom=MARGIN_INCHES / height,
        top=(MARGIN_INCHES + PANEL_INCHES) / height,
        wspace=PANEL_GAP,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=dpi, facecolor="white")
    plt.close(figure)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-root",
        type=Path,
        required=True,
        help=f"Dataset root holding the split directories ({TRAIN_SPLIT}, {HOLDOUT_SPLIT}).",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help=f"Output PNG (default: {DEFAULT_OUTPUT}).")
    parser.add_argument("--dpi", type=int, default=170, help="Output resolution (default: 170).")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        data_root = args.data_root.resolve()
        if not data_root.is_dir():
            raise FigureError(f"Dataset root does not exist: {data_root}")

        targets, normal = load_lesions()
        validate_panel_set(EXAMPLES, targets)
        for example in EXAMPLES:
            validate_example(example, data_root, targets, normal)

        images = [load_grayscale(find_dicom(data_root, example)) for example in EXAMPLES]
        render(images, EXAMPLES, args.out, args.dpi)
    except FigureError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    size_kb = args.out.stat().st_size / 1024
    print(f"Wrote {args.out} ({size_kb:.0f} KB, {len(EXAMPLES)} panels) from {data_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
