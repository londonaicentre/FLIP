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
"""Deterministic selection of tissue-bearing tiles from a whole-slide image.

A slide holds tens of thousands of tiles and most of them are blank glass, so the evaluator scores a
bounded, reproducible sample rather than the whole slide.

Two properties matter and neither is free:

* **Deterministic.** The same slide and seed always yield the same tiles, so a re-run reproduces a
  metric exactly. Candidates are visited in a seeded permutation of the grid rather than raster
  order, because raster order starts in the top-left corner, which on a slide is almost always
  background.
* **Not annotation-guided.** Tiles are chosen by image content alone. Selecting tiles where the
  reference annotations already are would make the evaluation circular -- recall would be inflated
  and false positives suppressed, because the detector would only ever be asked about regions the
  reference model had already committed to.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import numpy as np
from detection import tissue_fraction
from dicom_wsi import SlideReader, Tile

logger = logging.getLogger(__name__)


def iter_tissue_tiles(
    reader: SlideReader,
    max_tiles: int,
    min_tissue_fraction: float,
    seed: int,
    max_candidates: int | None = None,
) -> Iterator[Tile]:
    """Yield up to *max_tiles* tissue-bearing tiles from *reader*.

    Args:
        reader: The slide to sample.
        max_tiles: Stop after this many tiles pass the tissue test.
        min_tissue_fraction: Minimum stained-tissue fraction for a tile to be scored.
        seed: Seed for the candidate permutation. Fixed in ``config.json`` so runs reproduce.
        max_candidates: Cap on tiles decoded while searching. Defaults to twenty times *max_tiles*,
            which bounds the work on a slide that is mostly glass -- without it, a sparse slide would
            decode its entire grid looking for tissue that is not there.

    Yields:
        Tiles that pass the tissue test, in the order they were found.
    """
    positions = reader.tile_positions()
    order = np.random.default_rng(seed).permutation(len(positions))
    budget = max_candidates if max_candidates is not None else max_tiles * 20

    accepted = 0
    for inspected, index in enumerate(order, start=1):
        if accepted >= max_tiles or inspected > budget:
            break
        tile_row, tile_column = positions[index]
        tile = reader.read_tile(tile_row, tile_column)
        if tissue_fraction(tile.pixels) < min_tissue_fraction:
            continue
        accepted += 1
        yield tile

    if accepted < max_tiles:
        logger.warning(
            "%s: only %d of %d requested tiles met the %.0f%% tissue threshold within %d candidates.",
            reader.path.name,
            accepted,
            max_tiles,
            min_tissue_fraction * 100,
            min(budget, len(positions)),
        )


def references_within_tile(centroids: np.ndarray, tile: Tile) -> np.ndarray:
    """Select the reference centroids that fall inside *tile*, returned in tile-local coordinates.

    Predictions are tile-local by construction, so references are cropped to the same tile and
    rebased to the same origin. Matching per tile (rather than pooling a whole slide) also keeps the
    assignment problem small and stops a prediction matching a reference on the far side of the slide.
    """
    centroids = np.asarray(centroids, dtype=float).reshape(-1, 2)
    if len(centroids) == 0:
        return np.empty((0, 2), dtype=float)
    height, width = tile.pixels.shape[:2]
    x = centroids[:, 0] - tile.x
    y = centroids[:, 1] - tile.y
    inside = (x >= 0) & (x < width) & (y >= 0) & (y < height)
    return np.column_stack([x[inside], y[inside]])
