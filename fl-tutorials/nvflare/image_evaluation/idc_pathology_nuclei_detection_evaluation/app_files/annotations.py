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

"""Read reference nuclei from a DICOM Microscopy Bulk Simple Annotation (ANN) object.

The Pan-Cancer-Nuclei-Seg analysis result stores one nucleus per POLYGON annotation, encoded per
DICOM Supplement 222: all vertices of all polygons are concatenated into a single flat
``PointCoordinatesData`` buffer, and ``LongPrimitivePointIndexList`` holds the **1-based index into
that flat buffer** at which each polygon starts. Nothing delimits polygons other than those offsets.

These annotations are ``AnnotationGroupGenerationType`` == AUTOMATIC — model output, not curated
truth. Everything here calls them *reference* nuclei, never ground truth; see the tutorial README.

The tutorial scores **detection**, so each polygon is reduced to its centroid. That loses shape, and
a follow-on tutorial could score segmentation instead using the polygons directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pydicom

logger = logging.getLogger(__name__)

# Supplement 222 fixes these: coordinates are float32, and the primitive index list is 32-bit.
_COORDINATE_DTYPE = "<f4"
_INDEX_DTYPE = "<u4"

# The only coordinate type this reader accepts. "2D" means vertices are expressed in the slide's
# total-pixel-matrix frame, which is the same frame tile origins use -- so predictions and references
# are directly comparable without a spatial transform. "3D" would be slide-coordinate millimetres and
# would need converting; no Pan-Cancer-Nuclei-Seg object uses it, so it is rejected rather than guessed.
_SUPPORTED_COORDINATE_TYPE = "2D"


@dataclass(frozen=True)
class ReferenceNuclei:
    """Reference nucleus centroids for one slide, in total-pixel-matrix coordinates.

    Attributes:
        centroids: (N, 2) array of (column, row) centroids, i.e. (x, y).
        label: The annotation group label, e.g. "Nuclei".
        generation_type: AUTOMATIC for Pan-Cancer-Nuclei-Seg. Carried so callers can refuse to
            describe automatically generated annotations as ground truth.
    """

    centroids: np.ndarray
    label: str
    generation_type: str
    vertices: np.ndarray | None = None
    offsets: np.ndarray | None = None

    def __len__(self) -> int:
        return len(self.centroids)

    def polygons_within(self, x0: float, y0: float, width: float, height: float) -> list[np.ndarray]:
        """Return the full polygon outlines whose centroid falls in the given box, tile-local.

        Detection scoring only needs centroids, but drawing needs the boundaries -- so the decoded
        vertices are kept and sliced on demand rather than decoded twice. Selection is by centroid so
        a polygon is drawn by exactly the tile that scored it.
        """
        if self.vertices is None or self.offsets is None:
            raise ValueError("This ReferenceNuclei was loaded without vertices; pass keep_vertices=True.")
        inside = np.nonzero(
            (self.centroids[:, 0] >= x0)
            & (self.centroids[:, 0] < x0 + width)
            & (self.centroids[:, 1] >= y0)
            & (self.centroids[:, 1] < y0 + height)
        )[0]
        # ends[i + 1] is the exclusive end of polygon i; it is always in range because ends has one
        # more element than offsets.
        ends = np.append(self.offsets, len(self.vertices))
        origin = np.array([x0, y0], dtype=float)
        return [self.vertices[self.offsets[i] : ends[i + 1]] - origin for i in inside]


def _polygon_centroids(coordinates: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """Reduce concatenated polygon vertices to one centroid per polygon.

    Args:
        coordinates: (P, 2) array of every vertex of every polygon, concatenated.
        starts: (N,) array of 0-based offsets into *coordinates* where each polygon begins.

    Returns:
        (N, 2) array of vertex-mean centroids.

    Uses ``np.add.reduceat``, which sums each contiguous run in one pass — the alternative, slicing
    per polygon, is ~80,000 Python-level slices for a single slide.
    """
    if len(starts) == 0:
        return np.empty((0, 2), dtype=float)
    sums = np.add.reduceat(coordinates, starts, axis=0)
    counts = np.diff(np.append(starts, len(coordinates))).astype(float)
    return sums / counts[:, None]


def load_reference_nuclei(
    annotation_path: Path | str, group_label: str = "Nuclei", keep_vertices: bool = False
) -> ReferenceNuclei:
    """Load nucleus centroids from the ANN object at *annotation_path*.

    Args:
        annotation_path: Path to the DICOM ANN instance.
        group_label: Annotation group to read. Pan-Cancer-Nuclei-Seg emits a single Nuclei group;
            if it is absent the first group is used and a warning is logged.

    Returns:
        The reference nuclei for this slide.

    Raises:
        ValueError: If the object carries no annotation groups, uses an unsupported coordinate type,
            or stores a graphic type other than POLYGON.
    """
    dataset = pydicom.dcmread(str(annotation_path))

    coordinate_type = str(getattr(dataset, "AnnotationCoordinateType", ""))
    if coordinate_type != _SUPPORTED_COORDINATE_TYPE:
        raise ValueError(
            f"{annotation_path}: AnnotationCoordinateType is {coordinate_type!r}, but this reader only "
            f"handles {_SUPPORTED_COORDINATE_TYPE!r} (total-pixel-matrix coordinates)."
        )

    groups = list(getattr(dataset, "AnnotationGroupSequence", []) or [])
    if not groups:
        raise ValueError(f"{annotation_path}: no AnnotationGroupSequence.")

    matching = [g for g in groups if str(getattr(g, "AnnotationGroupLabel", "")) == group_label]
    if matching:
        group = matching[0]
    else:
        group = groups[0]
        logger.warning(
            "%s: no annotation group labelled %r; using %r instead.",
            annotation_path,
            group_label,
            str(getattr(group, "AnnotationGroupLabel", "<unlabelled>")),
        )

    graphic_type = str(getattr(group, "GraphicType", ""))
    if graphic_type != "POLYGON":
        raise ValueError(f"{annotation_path}: GraphicType {graphic_type!r} is not supported (expected POLYGON).")

    # VR OF/OL come back as raw bytes; frombuffer avoids copying ~80k polygons through Python floats.
    coordinates = np.frombuffer(group.PointCoordinatesData, dtype=_COORDINATE_DTYPE).reshape(-1, 2)
    # Supplement 222 indexes into the *flat* coordinate stream and is 1-based, so convert to 0-based
    # vertex offsets by subtracting one and halving (two values per 2D vertex).
    raw_index = np.frombuffer(group.LongPrimitivePointIndexList, dtype=_INDEX_DTYPE).astype(np.int64)
    starts = (raw_index - 1) // 2

    centroids = _polygon_centroids(coordinates.astype(float), starts)

    declared = int(getattr(group, "NumberOfAnnotations", len(centroids)))
    if declared != len(centroids):
        logger.warning(
            "%s: NumberOfAnnotations is %d but %d polygon(s) were decoded.",
            annotation_path,
            declared,
            len(centroids),
        )

    return ReferenceNuclei(
        centroids=centroids,
        label=str(getattr(group, "AnnotationGroupLabel", "")),
        generation_type=str(getattr(group, "AnnotationGroupGenerationType", "")),
        vertices=coordinates.astype(float) if keep_vertices else None,
        offsets=starts if keep_vertices else None,
    )
