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

"""Read regions from a DICOM Slide Microscopy (SM) whole-slide image.

A whole slide is far too large to hold in memory — the tutorial's own slides run to 35584 x 42752
pixels — so nothing here ever decodes more than one 256 x 256 frame at a time.

Frame geometry comes from DimensionOrganizationType == TILED_FULL, where tile positions are
**implicit**: frames run in row-major order across the tile grid and there is no
PerFrameFunctionalGroupsSequence to consult. That is what IDC's conversion of Pan-Cancer-Nuclei-Seg
slides uses. TILED_SPARSE stores an explicit position per frame and is rejected rather than
guessed at.

Edge tiles are full-size and padded: the tile grid covers more area than the total pixel matrix, so
the right-hand and bottom tiles contain padding beyond the image. :meth:
returns the valid extent alongside the pixels so callers never detect "nuclei" in padding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pydicom
from pydicom.pixels import pixel_array

logger = logging.getLogger(__name__)

_SM_SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.77.1.6"
_SUPPORTED_DIMENSION_ORGANIZATION = "TILED_FULL"


@dataclass(frozen=True)
class Tile:
    """One decoded tile and where it sits in the slide.

    Attributes:
        pixels: (rows, columns, 3) RGB array, cropped to the valid (non-padded) extent.
        x: Column of the tile's top-left corner in total-pixel-matrix coordinates.
        y: Row of the tile's top-left corner in total-pixel-matrix coordinates.
    """

    pixels: np.ndarray
    x: int
    y: int

    def to_slide_coordinates(self, local_points: np.ndarray) -> np.ndarray:
        """Translate tile-local (x, y) points into total-pixel-matrix coordinates."""
        points = np.asarray(local_points, dtype=float).reshape(-1, 2)
        if len(points) == 0:
            return np.empty((0, 2), dtype=float)
        return points + np.array([self.x, self.y], dtype=float)


class SlideReader:
    """Random access to the tiles of one DICOM SM instance.

    The dataset is read once without pixels; each :meth: then decodes exactly one frame.
    """

    def __init__(self, slide_path: Path | str) -> None:
        self.path = Path(slide_path)
        dataset = pydicom.dcmread(str(self.path), stop_before_pixels=True)

        sop_class = str(getattr(dataset, "SOPClassUID", ""))
        if sop_class != _SM_SOP_CLASS_UID:
            raise ValueError(
                f"{self.path}: SOPClassUID {sop_class!r} is not VL Whole Slide Microscopy Image "
                f"({_SM_SOP_CLASS_UID})."
            )
        organization = str(getattr(dataset, "DimensionOrganizationType", ""))
        if organization != _SUPPORTED_DIMENSION_ORGANIZATION:
            raise ValueError(
                f"{self.path}: DimensionOrganizationType {organization!r} is not supported; this reader "
                f"derives tile positions from the implicit {_SUPPORTED_DIMENSION_ORGANIZATION} ordering."
            )

        self.total_columns = int(dataset.TotalPixelMatrixColumns)
        self.total_rows = int(dataset.TotalPixelMatrixRows)
        self.tile_columns = int(dataset.Columns)
        self.tile_rows = int(dataset.Rows)
        self.number_of_frames = int(dataset.NumberOfFrames)

        # Ceiling division: the grid covers the matrix, so the last row/column is partly padding.
        self.tiles_across = -(-self.total_columns // self.tile_columns)
        self.tiles_down = -(-self.total_rows // self.tile_rows)
        expected_frames = self.tiles_across * self.tiles_down
        if expected_frames != self.number_of_frames:
            raise ValueError(
                f"{self.path}: TILED_FULL implies {self.tiles_across} x {self.tiles_down} = "
                f"{expected_frames} frames, but NumberOfFrames is {self.number_of_frames}. Refusing to "
                "guess the frame ordering."
            )

        measures = dataset.SharedFunctionalGroupsSequence[0].PixelMeasuresSequence[0]
        # PixelSpacing is (row spacing, column spacing) in mm; nuclei are near-isotropic so one scalar
        # in micrometres is enough, and it is what makes the detector's physical parameters portable.
        self.pixel_spacing_mm = float(measures.PixelSpacing[0])
        self.micrometres_per_pixel = self.pixel_spacing_mm * 1000.0

    def __repr__(self) -> str:
        return (
            f"SlideReader({self.path.name}, {self.total_columns}x{self.total_rows}px, "
            f"{self.tiles_across}x{self.tiles_down} tiles, {self.micrometres_per_pixel:.4f} um/px)"
        )

    def tile_positions(self) -> list[tuple[int, int]]:
        """Every (tile_row, tile_column) position in the grid, row-major."""
        return [(r, c) for r in range(self.tiles_down) for c in range(self.tiles_across)]

    def read_tile(self, tile_row: int, tile_column: int) -> Tile:
        """Decode the tile at (tile_row, tile_column), cropped to the valid image extent.

        Raises:
            IndexError: If the position is outside the tile grid.
        """
        if not (0 <= tile_row < self.tiles_down and 0 <= tile_column < self.tiles_across):
            raise IndexError(
                f"Tile ({tile_row}, {tile_column}) is outside the {self.tiles_down} x {self.tiles_across} grid."
            )

        frame_index = tile_row * self.tiles_across + tile_column
        pixels = pixel_array(str(self.path), index=frame_index)

        x = tile_column * self.tile_columns
        y = tile_row * self.tile_rows
        # Trim the padding the TILED_FULL grid adds past the total pixel matrix.
        valid_width = min(self.tile_columns, self.total_columns - x)
        valid_height = min(self.tile_rows, self.total_rows - y)
        return Tile(pixels=pixels[:valid_height, :valid_width], x=x, y=y)
