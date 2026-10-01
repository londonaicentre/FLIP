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

"""One-to-one matching of predicted nucleus centres against reference centres.

Detection quality is scored by pairing each prediction with at most one reference and vice versa.
Naive nearest-neighbour matching is deliberately **not** used: it lets one reference absorb several
predictions, which silently converts false positives into true positives and inflates precision.

The matching is greedy by ascending distance, which is O(n log n) via a KD-tree and gives the same
answer as optimal (Hungarian) assignment in the regime that matters here — candidate pairs are
sparse because the radius is roughly one nucleus diameter, so conflicts are rare and local. Ties are
broken by index so the result is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree


@dataclass(frozen=True)
class MatchCounts:
    """Detection counts for one unit of evaluation (a tile, a slide, or a site).

    Attributes:
        tp: Predictions matched to a distinct reference.
        fp: Predictions with no reference within the matching radius (or whose reference was taken).
        fn: References left unmatched.
    """

    tp: int = 0
    fp: int = 0
    fn: int = 0

    def __add__(self, other: MatchCounts) -> MatchCounts:
        return MatchCounts(self.tp + other.tp, self.fp + other.fp, self.fn + other.fn)

    @property
    def n_predictions(self) -> int:
        return self.tp + self.fp

    @property
    def n_references(self) -> int:
        return self.tp + self.fn


def assign_points(predictions: np.ndarray, references: np.ndarray, radius: float) -> list[tuple[int, int]]:
    """Greedily pair predictions with references, one-to-one, within ``radius``.

    Returns:
        The accepted ``(prediction_index, reference_index)`` pairs. This is the single source of truth
        for matching -- :func:`match_points` counts these pairs rather than repeating the logic, and
        the overlay renderer uses them to colour each detection by outcome.
    """
    predictions = np.asarray(predictions, dtype=float).reshape(-1, 2)
    references = np.asarray(references, dtype=float).reshape(-1, 2)
    if len(predictions) == 0 or len(references) == 0:
        return []
    if radius <= 0:
        raise ValueError(f"Matching radius must be positive, got {radius!r}.")

    # Candidate pairs only within the radius, so the sort below stays small even on dense tiles.
    neighbours = cKDTree(predictions).query_ball_tree(cKDTree(references), r=radius)
    candidates = [
        (float(np.hypot(*(predictions[p] - references[r]))), p, r)
        for p, matches in enumerate(neighbours)
        for r in matches
    ]
    # Sort by distance, then by the index pair, so equidistant candidates resolve deterministically.
    candidates.sort()

    used_predictions: set[int] = set()
    used_references: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _distance, prediction_index, reference_index in candidates:
        if prediction_index in used_predictions or reference_index in used_references:
            continue
        used_predictions.add(prediction_index)
        used_references.add(reference_index)
        pairs.append((prediction_index, reference_index))
    return pairs


def match_points(predictions: np.ndarray, references: np.ndarray, radius: float) -> MatchCounts:
    """Match predicted centres to reference centres, one-to-one, within *radius*.

    Args:
        predictions: (N, 2) array of predicted centres, in the same coordinate space and units
            as *references*.
        references: (M, 2) array of reference centres.
        radius: Maximum centre-to-centre distance for a pair to count as a match, in the same units
            as the coordinates. Callers convert from micrometres using the slide's pixel spacing.

    Returns:
        The TP/FP/FN counts. With no predictions every reference is a false negative; with no
        references every prediction is a false positive; with neither, all counts are zero.
    """
    n_predictions = len(np.asarray(predictions, dtype=float).reshape(-1, 2))
    n_references = len(np.asarray(references, dtype=float).reshape(-1, 2))
    if n_predictions == 0:
        return MatchCounts(tp=0, fp=0, fn=n_references)
    if n_references == 0:
        return MatchCounts(tp=0, fp=n_predictions, fn=0)

    tp = len(assign_points(predictions, references, radius))
    return MatchCounts(tp=tp, fp=n_predictions - tp, fn=n_references - tp)
